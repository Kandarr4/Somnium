# smtp_server.py
import asyncio
from aiosmtpd.controller import Controller
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from email.header import decode_header
from models import db, Mail, Attachment, User
from config import Config
from flask import Flask, request, jsonify
import os
from datetime import datetime, timezone
import smtplib
from email.message import EmailMessage
import uuid
import logging
import dns.resolver
import mimetypes
import ssl  # Для настройки SSL
import email  # Добавлено для доступа к email.utils
import requests  # Убедитесь, что этот модуль также импортирован, если используется


# Инициализируем Flask-приложение для доступа к базе данных
app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Базовый путь для сохранения вложений
BASE_ATTACHMENT_DIR = os.path.join(os.getcwd(), 'static', 'Email', 'Files')


class EmailHandler:
    async def handle_DATA(self, server, session, envelope):
        with app.app_context():
            try:
                # Парсинг письма
                parser = BytesParser(policy=policy.default)
                email_message = parser.parsebytes(envelope.content)

                # Извлечение тела письма
                body = self.extract_body(email_message)
                logger.info(f"Extracted body: {body[:100]}...")  # Логируем первые 100 символов тела

                # Извлечение получателя с использованием parseaddr
                recipient = parseaddr(email_message['To'])[1]
                logger.info(f"Recipient: {recipient}")

                # Проверка, существует ли пользователь с данным internal_email
                user = User.query.filter_by(internal_email=recipient).first()
                if not user:
                    logger.warning(f"Письмо отправлено на неизвестный internal_email: {recipient}. Письмо отклонено.")
                    return '550 No such user here'  # Возвращаем ошибку SMTP

                received_at = datetime.now(timezone.utc)
                date_folder = received_at.strftime('%Y-%m-%d')

                # Обработка threading
                message_id = email_message.get('Message-ID')
                in_reply_to = email_message.get('In-Reply-To')
                references = email_message.get('References')

                parent_mail = None
                if in_reply_to:
                    parent_mail = Mail.query.filter_by(message_id=in_reply_to.strip()).first()
                elif references:
                    # В References может быть несколько Message-ID, берем последний
                    refs = references.strip().split()
                    if refs:
                        parent_mail = Mail.query.filter_by(message_id=refs[-1]).first()

                thread_id = parent_mail.thread_id if parent_mail else None
                if parent_mail and not thread_id:
                    thread_id = parent_mail.id
                    parent_mail.thread_id = thread_id
                    db.session.add(parent_mail)

                # Обработка message_id
                if not message_id:
                    # Генерируем уникальный Message-ID, если его нет
                    message_id = f"<{uuid.uuid4()}@{Config.DOMAIN}>"
                else:
                    message_id = message_id.strip()

                # Проверка уникальности Message-ID
                existing_mail = Mail.query.filter_by(message_id=message_id).first()
                if existing_mail:
                    logger.warning(f"Duplicate Message-ID {message_id}. Письмо отклонено.")
                    return '550 Duplicate Message-ID'  # Возвращаем ошибку SMTP

                # Создание объекта Mail и сохранение в базе данных
                mail = Mail(
                    sender=email_message['From'],
                    recipient=recipient,
                    subject=email_message['Subject'],
                    body=body,
                    received_at=received_at,
                    user_id=user.id,
                    message_id=message_id,
                    thread_id=thread_id if thread_id else None,
                    parent=parent_mail,
                    direction='incoming'  # Устанавливаем направление как входящее
                )
                db.session.add(mail)
                db.session.commit()

                logger.info(f"Created Mail ID: {mail.id}, Thread ID: {mail.thread_id}")

                # Обработка вложений
                for part in email_message.iter_attachments():
                    filename = part.get_filename()
                    if filename:
                        sanitized_recipient = sanitize_filename(recipient)
                        sanitized_filename = sanitize_filename(filename)

                        dir_path = os.path.join(BASE_ATTACHMENT_DIR, sanitized_recipient, date_folder)
                        os.makedirs(dir_path, exist_ok=True)

                        file_path = os.path.join(dir_path, sanitized_filename)

                        with open(file_path, 'wb') as f:
                            f.write(part.get_payload(decode=True))

                        logger.info(f"Saved attachment: {file_path}")

                        # Сохранение информации о вложениях
                        attachment = Attachment(
                            mail_id=mail.id,
                            filename=sanitized_filename,
                            filepath=file_path
                        )
                        db.session.add(attachment)
                db.session.commit()

                # Уведомление Flask приложения о новом входящем письме
                notify_flask_app(recipient, mail.id)

            except Exception as e:
                logger.error(f"Error processing email: {e}", exc_info=True)
                return '451 Requested action aborted: local error in processing'

        return '250 Message accepted for delivery'

    def extract_body(self, email_message):
        """
        Извлекает тело письма, предпочитая text/plain, но также поддерживает text/html.
        """
        body = ""
        if email_message.is_multipart():
            for part in email_message.walk():
                content_type = part.get_content_type()
                content_disposition = part.get_content_disposition()
                # Пропускаем вложения
                if content_disposition == 'attachment':
                    continue
                if content_type == 'text/plain':
                    try:
                        body += part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                    except Exception as e:
                        logger.error(f"Error decoding text/plain part: {e}")
                elif content_type == 'text/html' and not body:
                    # Если text/plain нет, используем text/html
                    try:
                        html_content = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', errors='replace')
                        body += self.html_to_text(html_content)
                    except Exception as e:
                        logger.error(f"Error decoding text/html part: {e}")
        else:
            content_type = email_message.get_content_type()
            if content_type == 'text/plain':
                try:
                    body = email_message.get_payload(decode=True).decode(email_message.get_content_charset() or 'utf-8', errors='replace')
                except Exception as e:
                    logger.error(f"Error decoding text/plain message: {e}")
            elif content_type == 'text/html':
                try:
                    html_content = email_message.get_payload(decode=True).decode(email_message.get_content_charset() or 'utf-8', errors='replace')
                    body = self.html_to_text(html_content)
                except Exception as e:
                    logger.error(f"Error decoding text/html message: {e}")
        return body.strip()

    def html_to_text(self, html_content):
        """
        Простейший способ преобразования HTML в текст.
        Для более сложных случаев рекомендуется использовать библиотеки, такие как BeautifulSoup.
        """
        from html import unescape
        import re
        text = unescape(re.sub('<[^<]+?>', '', html_content))
        return text


def sanitize_filename(filename):
    """
    Функция для безопасного создания имен файлов и каталогов.
    Удаляет или заменяет символы, которые могут быть проблематичными в путях.
    """
    return "".join(c for c in filename if c.isalnum() or c in (' ', '@', '.', '_', '-')).rstrip()


def notify_flask_app(recipient, email_id):
    """
    Отправляет HTTP-запрос к Flask приложению для уведомления о новом письме.
    """
    try:
        flask_app_url = 'http://localhost:7000/notify_new_email'  # Измени при необходимости
        payload = {
            'recipient': recipient,
            'email_id': email_id
        }
        headers = {'Content-Type': 'application/json'}
        response = requests.post(flask_app_url, json=payload, headers=headers)
        if response.status_code == 200:
            logger.info("Flask приложение успешно уведомлено о новом письме.")
        else:
            logger.error(f"Ошибка уведомления Flask приложения: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Не удалось уведомить Flask приложение: {e}")

def send_email(sender, recipient, subject, body, attachments=None):
    with app.app_context():
        try:
            logger.info(f"Starting send_email: sender={sender}, recipient={recipient}, subject={subject}, attachments={attachments}")
            # Генерация уникального Message-ID
            message_id = f"<{uuid.uuid4()}@{Config.DOMAIN}>"

            # Проверка, отправлялось ли уже письмо с таким Message-ID
            existing_mail = Mail.query.filter_by(message_id=message_id).first()
            if existing_mail:
                logger.info(f"Письмо с Message-ID {message_id} уже отправлено. Пропуск отправки.")
                return False

            # Разрешение MX-записей домена получателя
            domain = recipient.split('@')[-1]
            logger.info(f"Resolving MX records for domain: {domain}")
            try:
                answers = dns.resolver.resolve(domain, 'MX')
                mx_records = sorted([(r.preference, r.exchange.to_text()) for r in answers], key=lambda x: x[0])
                if not mx_records:
                    logger.error(f"No MX records found for domain {domain}")
                    return False
                logger.info(f"Found MX records: {mx_records}")
            except Exception as e:
                logger.error(f"Error resolving MX records for {domain}: {e}")
                return False

            # Перебор MX серверов
            for preference, exchange in mx_records:
                mx_host = exchange.rstrip('.')
                logger.info(f"Attempting to connect to MX server: {mx_host} on port 25")
                try:
                    with smtplib.SMTP(mx_host, 25, timeout=10) as server:
                        server.set_debuglevel(1)  # Включаем подробное логирование SMTP
                        server.ehlo()
                        if server.has_extn('STARTTLS'):
                            server.starttls()
                            server.ehlo()
                            logger.info("STARTTLS enabled")

                        server.mail(sender)
                        code, response = server.rcpt(recipient)
                        logger.info(f"SMTP RCPT TO response: {code} {response}")
                        if code not in (250, 251):
                            logger.error(f"Failed to send mail to {recipient}: {code} {response}")
                            continue  # Попробовать следующий MX-сервер

                        # Создание объекта EmailMessage
                        msg = EmailMessage()
                        msg['From'] = sender
                        msg['To'] = recipient
                        msg['Subject'] = subject
                        msg['Message-ID'] = message_id
                        msg['Date'] = email.utils.format_datetime(datetime.now(timezone.utc))
                        msg.set_content(body)

                        # Если тело содержит HTML, добавляем альтернативный вариант
                        if '<html>' in body.lower():
                            msg.add_alternative(body, subtype='html')

                        # Добавление вложений
                        if attachments:
                            for file_path in attachments:
                                logger.info(f"Processing attachment: {file_path}")
                                if os.path.isfile(file_path):
                                    with open(file_path, 'rb') as f:
                                        file_data = f.read()
                                        file_name = os.path.basename(file_path)
                                    maintype, subtype = ('application', 'octet-stream')
                                    mime_type, _ = mimetypes.guess_type(file_path)
                                    if mime_type:
                                        maintype, subtype = mime_type.split('/', 1)
                                    msg.add_attachment(file_data, maintype=maintype, subtype=subtype, filename=file_name)
                                    logger.info(f"Attachment added: {file_name}")
                                else:
                                    logger.warning(f"Attachment {file_path} not found and will be skipped.")

                        # Отправка письма
                        server.send_message(msg)
                        logger.info(f"Email successfully sent from {sender} to {recipient} with subject '{subject}' via {mx_host}.")

                        # Создание записи в базе данных
                        try:
                            user_id = get_user_id_by_email(sender)
                            if user_id is None:
                                logger.error(f"Unable to determine user_id for sender {sender}. Email will not be saved to the database.")
                                return False
                            else:
                                mail = Mail(
                                    sender=sender,
                                    recipient=recipient,
                                    subject=subject,
                                    body=body,
                                    received_at=datetime.now(timezone.utc),
                                    user_id=user_id,
                                    message_id=message_id,
                                    direction='outgoing'
                                )
                                db.session.add(mail)
                                db.session.commit()
                                logger.info(f"Created Mail ID: {mail.id} for outgoing email.")

                                # Создание записей о вложениях
                                if attachments:
                                    for file_path in attachments:
                                        if os.path.isfile(file_path):
                                            attachment = Attachment(
                                                mail_id=mail.id,
                                                filename=os.path.basename(file_path),
                                                filepath=os.path.relpath(file_path, BASE_ATTACHMENT_DIR)
                                            )
                                            db.session.add(attachment)
                                            logger.info(f"Created attachment for Mail ID {mail.id}: {attachment.filename}")
                                    db.session.commit()
                        except Exception as e:
                            logger.error(f"Error saving email and attachments to the database: {e}", exc_info=True)
                            return False

                        # Успешная отправка и сохранение, прерываем цикл
                        return True

                except Exception as e:
                    logger.error(f"Error sending email via MX server {mx_host}: {e}")
                    continue  # Попробовать следующий MX-сервер

            # Если ни один MX сервер не смог отправить письмо
            logger.error(f"Failed to send email to {recipient} via all MX servers.")
            return False

        except Exception as e:
            logger.error(f"Unexpected error in send_email: {e}", exc_info=True)
            return False


def get_user_id_by_email(email_address):
    """
    Получает user_id по email. Реализуйте согласно своей логике.
    """
    user = User.query.filter_by(internal_email=email_address).first()
    if user:
        return user.id
    else:
        logger.error(f"Пользователь с email {email_address} не найден.")
        return None


def run_smtp_server():
    handler = EmailHandler()
    controller = Controller(handler, hostname='10.0.0.2', port=25)  # Слушаем на всех интерфейсах через порт 25
    controller.start()
    logger.info("SMTP сервер запущен и слушает порт 25")
    try:
        # Используем asyncio.run для запуска бесконечного цикла
        asyncio.run(asyncio.Event().wait())
    except KeyboardInterrupt:
        controller.stop()
        logger.info("SMTP сервер остановлен")



if __name__ == '__main__':
    run_smtp_server()
