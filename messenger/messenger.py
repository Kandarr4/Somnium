# messenger/messenger.py
import os
import uuid
from datetime import datetime
from flask import (
    current_app, render_template, request, redirect, url_for, 
    flash, jsonify, session, abort, make_response
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
from sqlalchemy import desc, or_

from . import messenger_bp
from models import db, User
# Импорт моделей мессенджера
import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from model_messenger import Chat, Message, MessageAttachment, ChatNotification
from decorators import admin_required
from extensions import csrf



# Константы
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf', 'doc', 'docx', 'txt'}
MAX_ATTACHMENT_SIZE = 5 * 1024 * 1024  # 5 MB

# Папка для хранения вложений
ATTACHMENTS_FOLDER = 'static/uploads/chat_attachments'
os.makedirs(ATTACHMENTS_FOLDER, exist_ok=True)

def allowed_file(filename):
    """Проверка допустимого расширения файла"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@messenger_bp.route('/admin/resolve_chat/<int:chat_id>', methods=['POST'])
@login_required
@admin_required
def resolve_chat(chat_id):
    """Пометка чата как решённого админом"""
    chat = Chat.query.get_or_404(chat_id)
    chat.status = 'resolved'
    db.session.commit()
    
    # --- НОВОЕ: Отправляем WebSocket события ---
    try:
        if hasattr(current_app, 'socketio'):
            # Уведомление пользователю, что чат закрыт
            current_app.socketio.emit(
                'chat_closed',
                {
                    'chat_id': chat_id, 
                    'message': 'Ваш запрос был решен и чат закрыт. Если у вас появятся новые вопросы, создайте новый чат.'
                },
                room=f'chat_{chat_id}',
                namespace='/messenger'
            )
            # Уведомление другим админам об изменении статуса
            current_app.socketio.emit(
                'chat_status_updated',
                {'chat_id': chat_id, 'status': 'resolved'},
                room='admin_room',
                namespace='/messenger'
            )
    except Exception as e:
        current_app.logger.error(f"SocketIO error on resolve_chat: {e}")

    return jsonify({'success': True})

@messenger_bp.route('/admin/close_chat/<int:chat_id>', methods=['POST'])
@login_required
@admin_required
def close_chat(chat_id):
    """Закрытие чата админом без решения"""
    chat = Chat.query.get_or_404(chat_id)
    chat.status = 'closed'
    db.session.commit()

    # --- НОВОЕ: Отправляем WebSocket события ---
    try:
        if hasattr(current_app, 'socketio'):
            # Уведомление пользователю, что чат закрыт
            current_app.socketio.emit(
                'chat_closed',
                {
                    'chat_id': chat_id, 
                    'message': 'Чат был закрыт администратором.'
                },
                room=f'chat_{chat_id}',
                namespace='/messenger'
            )
            # Уведомление другим админам об изменении статуса
            current_app.socketio.emit(
                'chat_status_updated',
                {'chat_id': chat_id, 'status': 'closed'},
                room='admin_room',
                namespace='/messenger'
            )
    except Exception as e:
        current_app.logger.error(f"SocketIO error on close_chat: {e}")

    return jsonify({'success': True})
    
# Изменение функции get_or_create_guest_id
def get_or_create_guest_id():
    """Получить или создать ID для гостевого пользователя"""
    if 'guest_id' not in session:
        # Проверяем, есть ли в куках гостевой ID
        guest_id_from_cookie = request.cookies.get('guest_id')
        if guest_id_from_cookie:
            session['guest_id'] = guest_id_from_cookie
        else:
            session['guest_id'] = str(uuid.uuid4())
    
    return session['guest_id']

# Модифицированная функция chat_home
@messenger_bp.route('/', methods=['GET'])
@csrf.exempt
def chat_home():
    """Главная страница чата для пользователя/гостя"""
    if current_user.is_authenticated:
        # Найти активные чаты пользователя
        chats = Chat.query.filter_by(user_id=current_user.id, is_active=True).all()
        user_type = 'user'
        identifier = current_user.id
    else:
        # Для гостя
        guest_id = get_or_create_guest_id()
        chats = Chat.query.filter_by(guest_id=guest_id, is_active=True).all()
        user_type = 'guest'
        identifier = guest_id
    
    # Если нет активных чатов, создадим новый
    if not chats:
        new_chat = Chat(
            user_id=current_user.id if current_user.is_authenticated else None,
            title="Новый чат"
        )
        if not current_user.is_authenticated:
            new_chat.guest_id = get_or_create_guest_id()
        
        db.session.add(new_chat)
        db.session.commit()
        chats = [new_chat]
    
    response = make_response(render_template(
        'messenger/chat.html', 
        chats=chats, 
        current_chat_id=chats[0].id,
        user_type=user_type,
        identifier=identifier
    ))
    
    # Устанавливаем guest_id в куки с долгим сроком действия
    if not current_user.is_authenticated:
        response.set_cookie('guest_id', guest_id, max_age=31536000)  # 1 год
    
    return response

# Замените функцию view_chat в messenger.py

@messenger_bp.route('/chat/<int:chat_id>', methods=['GET'])
@csrf.exempt
def view_chat(chat_id):
    """Просмотр конкретного чата"""
    chat = Chat.query.get_or_404(chat_id)
    guest_id = None
    
    # Проверка доступа
    has_access = False
    
    if current_user.is_authenticated:
        if chat.user_id == current_user.id or current_user.is_admin:
            has_access = True
    else:
        guest_id = get_or_create_guest_id()
        if chat.guest_id == guest_id:
            has_access = True
    
    # Если нет доступа, возвращаем ошибку
    if not has_access:
        # Для AJAX запросов возвращаем JSON с ошибкой
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({
                'success': False, 
                'message': 'У вас нет доступа к этому чату'
            }), 403
        
        # Для обычных запросов перенаправляем на главную страницу мессенджера
        flash('У вас нет доступа к этому чату', 'danger')
        return redirect(url_for('messenger_bp.chat_home'))
    
    # Получаем сообщения для этого чата
    messages = Message.query.filter_by(chat_id=chat_id).order_by(Message.created_at).all()
    
    # Отмечаем сообщения админа как прочитанные
    for message in messages:
        if message.sender_type == 'admin' and not message.is_read:
            message.is_read = True
    
    db.session.commit()
    
    # Проверяем, является ли запрос AJAX
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # Для AJAX возвращаем JSON
        messages_data = []
        for message in messages:
            # Добавляем информацию о вложениях, если они есть
            attachments_data = []
            for attachment in message.attachments:
                attachments_data.append({
                    'id': attachment.id,
                    'filename': attachment.filename,
                    'filepath': url_for('static', filename=attachment.filepath)
                })
            
            messages_data.append({
                'id': message.id,
                'content': message.content,
                'sender_type': message.sender_type,
                'sender_id': message.sender_id,
                'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'is_read': message.is_read,
                'attachments': attachments_data
            })
        
        # Добавляем информацию о статусе чата в ответ
        chat_data = {
            'id': chat.id,
            'title': chat.title,
            'status': chat.status,
            'updated_at': chat.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        return jsonify({
            'success': True,
            'chat_id': chat_id,
            'chat': chat_data,
            'messages': messages_data
        })
    
    # Для обычного запроса возвращаем HTML
    # Получаем все чаты пользователя/гостя
    if current_user.is_authenticated:
        chats = Chat.query.filter_by(user_id=current_user.id, is_active=True).all()
    else:
        chats = Chat.query.filter_by(guest_id=guest_id, is_active=True).all()
    
    response = make_response(render_template(
        'messenger/chat.html', 
        chats=chats, 
        current_chat_id=chat_id,
        messages=messages,
        user_type='user' if current_user.is_authenticated else 'guest',
        identifier=current_user.id if current_user.is_authenticated else guest_id
    ))
    
    # Устанавливаем guest_id в куки с долгим сроком действия
    if not current_user.is_authenticated and guest_id:
        response.set_cookie('guest_id', guest_id, max_age=31536000)  # 1 год
    
    return response



@messenger_bp.route('/send_message', methods=['POST'])
@csrf.exempt
def send_message():
    """Отправка сообщения"""
    chat_id = request.form.get('chat_id')
    content = request.form.get('content')
    sender_type_override = request.form.get('sender_type')
    force_send = request.form.get('force_send', 'false').lower() == 'true'
    
    if not chat_id or not content:
        return jsonify({'success': False, 'message': 'Не указан ID чата или содержание сообщения'}), 400
    
    chat = Chat.query.get_or_404(chat_id)
    
    # Проверка доступа
    if current_user.is_authenticated:
        if chat.user_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Доступ запрещен'}), 403
        sender_type = 'user'
        sender_id = current_user.id
    else:
        if chat.guest_id != get_or_create_guest_id():
            return jsonify({'success': False, 'message': 'Доступ запрещен'}), 403
        sender_type = 'guest'
        sender_id = None
    
    # Если сообщение от админа
    if current_user.is_authenticated and current_user.is_admin:
        sender_type = 'admin'
    
    # Переопределяем тип отправителя, если он указан
    if sender_type_override and current_user.is_authenticated and current_user.is_admin:
        sender_type = sender_type_override
    
    # Проверяем статус чата
    if chat.status in ['resolved', 'closed'] and sender_type not in ['system', 'admin'] and not force_send:
        return jsonify({
            'success': False, 
            'message': 'Нельзя отправлять сообщения в закрытый чат. Создайте новый чат.'
        }), 400
    
    # Создаем сообщение
    message = Message(
        chat_id=chat_id,
        sender_type=sender_type,
        sender_id=sender_id,
        content=content
    )
    
    db.session.add(message)
    
    # Обновляем статус чата ТОЛЬКО если это НЕ системное сообщение
    if sender_type != 'system' and chat.status not in ['resolved', 'closed']:
        chat.status = 'pending' if sender_type in ['user', 'guest'] else 'open'
        chat.updated_at = datetime.utcnow()
    
    db.session.commit()
    
    # Если сообщение от обычного пользователя и чат был закрыт,
    # автоматически переводим его в открытый статус
    if sender_type in ['user', 'guest'] and chat.status in ['resolved', 'closed'] and force_send:
        chat.status = 'pending'
        chat.updated_at = datetime.utcnow()
        db.session.commit()
    
    # Создаем уведомления для администраторов
    if sender_type in ['user', 'guest']:
        admins = User.query.filter_by(role='admin').all()
        for admin in admins:
            notification = ChatNotification(
                user_id=admin.id,
                chat_id=chat_id,
                message_id=message.id,
                notification_type='new_message'
            )
            db.session.add(notification)
        
        db.session.commit()
    
    # УЛУЧШЕНИЕ: Более прямая отправка WebSocket событий
    try:
        if hasattr(current_app, 'socketio'):
            # Подготовка данных сообщения
            message_data = {
                'chat_id': int(chat_id), 
                'message_id': message.id,
                'content': content,
                'sender_type': sender_type,
                'sender_id': sender_id,
                'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            # 1. Отправляем событие новое сообщение в комнату чата
            current_app.socketio.emit(
                'new_message', 
                message_data,
                room=f'chat_{chat_id}',
                namespace='/messenger'
            )
            current_app.logger.info(f"Emitted new_message to chat_{chat_id}")
            
            # 2. Отправляем глобальное событие admin_new_message для всех администраторов
            # Это дополнительное событие, которое будет отловлено на стороне администратора
            current_app.socketio.emit(
                'admin_new_message',
                message_data,
                room='admin_room',
                namespace='/messenger'
            )
            current_app.logger.info(f"Emitted admin_new_message to admin_room")
            
            # 3. Отправляем уведомление о новом чате всем админам
            if sender_type in ['user', 'guest']:
                current_app.socketio.emit(
                    'new_chat_notification',
                    {
                        'chat_id': int(chat_id),
                        'message': f"Новое сообщение в чате {chat.title}",
                        'sender_type': sender_type,
                        'content': content[:50] + ('...' if len(content) > 50 else ''),
                    },
                    room='admin_room',
                    namespace='/messenger'
                )
                current_app.logger.info(f"Emitted new_chat_notification to admin_room")
            
            # 4. Отправляем прямое событие клиенту, если сообщение от админа
            if sender_type == 'admin':
                if chat.user_id:
                    current_app.socketio.emit(
                        'user_new_message',
                        message_data,
                        room=f'user_{chat.user_id}',
                        namespace='/messenger'
                    )
                    current_app.logger.info(f"Emitted user_new_message to user_{chat.user_id}")
                elif chat.guest_id:
                    current_app.socketio.emit(
                        'guest_new_message',
                        message_data,
                        room=f'guest_{chat.guest_id}',
                        namespace='/messenger'
                    )
                    current_app.logger.info(f"Emitted guest_new_message to guest_{chat.guest_id}")
    except Exception as e:
        current_app.logger.error(f"SocketIO error in send_message: {str(e)}")
        current_app.logger.error(f"SocketIO error details: {repr(e)}")
    
    return jsonify({
        'success': True, 
        'message_id': message.id,
        'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
    })


@messenger_bp.route('/upload_attachment', methods=['POST'])
@csrf.exempt
def upload_attachment():
    """Загрузка вложения к сообщению"""
    chat_id = request.form.get('chat_id')
    message_id = request.form.get('message_id')
    
    if not chat_id or not message_id:
        return jsonify({'success': False, 'message': 'Не указан ID чата или сообщения'}), 400
    
    chat = Chat.query.get_or_404(chat_id)
    message = Message.query.get_or_404(message_id)
    
    # Проверка доступа
    if current_user.is_authenticated:
        if chat.user_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Доступ запрещен'}), 403
    else:
        if chat.guest_id != get_or_create_guest_id():
            return jsonify({'success': False, 'message': 'Доступ запрещен'}), 403
    
    # Проверка файла
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Файл не найден'}), 400
    
    file = request.files['file']
    
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Файл не выбран'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'success': False, 'message': 'Недопустимый тип файла'}), 400
    
    if file and allowed_file(file.filename):
        # Создаем директорию для вложений, если не существует
        chat_attachments_dir = os.path.join(ATTACHMENTS_FOLDER, str(chat_id))
        os.makedirs(chat_attachments_dir, exist_ok=True)
        
        # Сохраняем файл с уникальным именем
        filename = secure_filename(file.filename)
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(chat_attachments_dir, unique_filename)
        
        try:
            file.save(file_path)
            
            # Создаем запись о вложении
            attachment = MessageAttachment(
                message_id=message_id,
                filename=filename,
                filepath=os.path.join('uploads/chat_attachments', str(chat_id), unique_filename),
                file_type=file.content_type
            )
            
            db.session.add(attachment)
            db.session.commit()
            
            return jsonify({
                'success': True, 
                'attachment_id': attachment.id,
                'filename': filename,
                'filepath': url_for('static', filename=attachment.filepath)
            })
            
        except Exception as e:
            current_app.logger.error(f"File upload error: {e}")
            return jsonify({'success': False, 'message': 'Ошибка при загрузке файла'}), 500
    
    return jsonify({'success': False, 'message': 'Ошибка при обработке файла'}), 400

@messenger_bp.route('/reopen_chat/<int:chat_id>', methods=['POST'])
@csrf.exempt
def reopen_chat(chat_id):
    """Переоткрытие закрытого чата"""
    chat = Chat.query.get_or_404(chat_id)
    
    # Проверка доступа
    if current_user.is_authenticated:
        if chat.user_id != current_user.id and not current_user.is_admin:
            return jsonify({'success': False, 'message': 'Доступ запрещен'}), 403
    else:
        # --- ИСПРАВЛЕНИЕ: Используем get_or_create_guest_id ---
        guest_id = get_or_create_guest_id()
        if chat.guest_id != guest_id:
            return jsonify({'success': False, 'message': 'Доступ запрещен'}), 403
    
    # Меняем статус чата на "ожидает ответа"
    # --- ИЗМЕНЕНИЕ: Статус 'pending', чтобы он попал в нужный фильтр у админа ---
    chat.status = 'pending'
    chat.updated_at = datetime.utcnow()
    
    # Создаем системное сообщение о переоткрытии чата
    system_message = Message(
        chat_id=chat_id,
        sender_type='system',
        content='Чат был переоткрыт пользователем. Ожидается ответ администратора.'
    )
    db.session.add(system_message)
    db.session.commit()
    
    # Отправляем уведомление администраторам
    admins = User.query.filter_by(role='admin').all()
    for admin in admins:
        notification = ChatNotification(
            user_id=admin.id,
            chat_id=chat_id,
            message_id=system_message.id,
            notification_type='chat_reopened'
        )
        db.session.add(notification)
    
    db.session.commit()
    
    # Отправляем сообщение через WebSocket, если доступно
    try:
        if hasattr(current_app, 'socketio'):
            # --- ИЗМЕНЕНИЕ: Отправляем разные события для пользователя и админа ---
            # 1. Пользователю, чтобы он увидел, что чат переоткрыт
            current_app.socketio.emit(
                'chat_reopened', 
                {
                    'chat_id': chat_id,
                    'message': 'Чат был переоткрыт. Вы можете продолжить общение.',
                    'system_message_id': system_message.id,
                    'created_at': system_message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                },
                room=f'chat_{chat_id}',
                namespace='/messenger'
            )
            # 2. Админам, чтобы они увидели новый/обновленный чат
            current_app.socketio.emit(
                'new_chat_notification', # Используем тот же сигнал, что и для нового сообщения
                {'chat_id': chat_id},
                room='admin_room',
                namespace='/messenger'
            )
    except Exception as e:
        current_app.logger.error(f"SocketIO error on reopen_chat: {e}")
    
    return jsonify({
        'success': True, 
        'message': 'Чат успешно переоткрыт',
        'chat_id': chat_id,
        'status': chat.status,
        'system_message_id': system_message.id,
        'created_at': system_message.created_at.strftime('%Y-%m-%d %H:%M:%S')
    })

@messenger_bp.route('/create_chat', methods=['POST'])
@csrf.exempt
def create_chat():
    """Создание нового чата"""
    title = request.form.get('title', 'Новый чат')
    
    # Проверяем, если пользователь не авторизован и является гостем
    guest_id = None
    if not current_user.is_authenticated:
        guest_id = get_or_create_guest_id()
        
        # Проверяем, есть ли уже активный чат для этого гостя
        existing_chat = Chat.query.filter_by(
            guest_id=guest_id, 
            is_active=True
        ).first()
        
        # Если есть активные чаты, используем самый последний
        if existing_chat:
            return jsonify({
                'success': True, 
                'chat_id': existing_chat.id,
                'title': existing_chat.title,
                'message': 'Используем существующий чат'
            })
    
    # Если гостевого чата не существует или пользователь авторизован, создаем новый
    new_chat = Chat(
        user_id=current_user.id if current_user.is_authenticated else None,
        title=title
    )
    
    if not current_user.is_authenticated and guest_id:
        new_chat.guest_id = guest_id
    
    db.session.add(new_chat)
    
    try:
        db.session.commit()
        return jsonify({
            'success': True, 
            'chat_id': new_chat.id,
            'title': new_chat.title
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error creating chat: {e}")
        
        # В случае ошибки проверяем, не существует ли уже чат для этого гостя
        if not current_user.is_authenticated and guest_id:
            existing_chat = Chat.query.filter_by(
                guest_id=guest_id, 
                is_active=True
            ).first()
            
            if existing_chat:
                return jsonify({
                    'success': True, 
                    'chat_id': existing_chat.id,
                    'title': existing_chat.title,
                    'message': 'Используем существующий чат'
                })
        
        return jsonify({
            'success': False,
            'message': 'Ошибка при создании чата'
        }), 500
        
def _chat_to_dict(chat):
    """Сериализует модель Chat в примитивы JSON."""
    return {
        "id": chat.id,
        "title": chat.title,
        "status": chat.status,
        "updated_at": chat.updated_at.isoformat(),
        # если нужно — добавьте messages, attachments и т.д.
    }

@messenger_bp.route('/admin', methods=['GET'])
@login_required
@admin_required
def admin_chat_list():
    chats = Chat.query.filter_by(is_active=True) \
                      .order_by(Chat.status.desc(), desc(Chat.updated_at)) \
                      .all()

    result = []
    for chat in chats:
        unread = Message.query.filter_by(
            chat_id=chat.id, is_read=False
        ).filter(Message.sender_type.in_(["user", "guest"])).count()

        user_info = None
        if chat.user_id:
            u = User.query.get(chat.user_id)
            if u:
                user_info = {
                    "id": u.id,
                    "username": u.username,
                    "email": u.email,
                    "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username
                }

        result.append({
            "chat": _chat_to_dict(chat),
            "unread_count": unread,
            "user_info": user_info
        })

    # ▶ если это AJAX (или явно просят JSON) — отдаём JSON
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" \
                 or request.accept_mimetypes.accept_json
    if wants_json:
        return jsonify(success=True, chats=result)

    # ▶ иначе работаем как старый MVC и отдаём страницу
    return render_template("messenger/admin_chats.html", chats=result)

@messenger_bp.route('/admin/chat/<int:chat_id>', methods=['GET'])
@login_required
@admin_required
def admin_view_chat(chat_id):
    chat = Chat.query.get_or_404(chat_id)
    messages = Message.query.filter_by(chat_id=chat_id) \
                            .order_by(Message.created_at).all()

    # помечаем сообщения прочитанными
    for msg in messages:
        if msg.sender_type in ("user", "guest") and not msg.is_read:
            msg.is_read = True

    # удаляем уведомления
    ChatNotification.query.filter_by(
        user_id=current_user.id,
        chat_id=chat_id
    ).delete(synchronize_session=False)

    db.session.commit()

    # ------------- если это AJAX — отдаём JSON ----------
    wants_json = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.accept_json
    )
    if wants_json:
        messages_data = [
            {
                "id": m.id,
                "content": m.content,
                "sender_type": m.sender_type,
                "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                "attachments": [
                    {
                        "id": a.id,
                        "filename": a.filename,
                        "filepath": url_for("static", filename=a.filepath)
                    } for a in m.attachments
                ]
            } for m in messages
        ]

        # информация о пользователе чата
        user_info = None
        if chat.user_id:
            u = User.query.get(chat.user_id)
            if u:
                user_info = {
                    "id": u.id,
                    "username": u.username,
                    "email": u.email,
                    "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.username
                }

        return jsonify(
            success=True,
            chat=_chat_to_dict(chat),
            messages=messages_data,
            user_info=user_info
        )

    # ------------- иначе (редкий случай) рендерим страницу ----------
    return render_template(
        "messenger/admin_chat.html",
        chat=chat,
        messages=messages
    )




@messenger_bp.route('/admin/delete_chat/<int:chat_id>', methods=['POST'])
@login_required
@admin_required
def delete_chat(chat_id):
    """Удаление чата админом (деактивация)"""
    chat = Chat.query.get_or_404(chat_id)
    chat.is_active = False
    db.session.commit()
    
    return jsonify({'success': True})

@messenger_bp.route('/admin/get_notifications', methods=['GET'])
@login_required
@admin_required
def get_notifications():
    """Получение уведомлений для админа"""
    notifications = ChatNotification.query.filter_by(
        user_id=current_user.id,
        is_read=False
    ).all()
    
    data = []
    for notification in notifications:
        chat = notification.chat
        data.append({
            'id': notification.id,
            'type': notification.notification_type,
            'chat_id': chat.id,
            'chat_title': chat.title,
            'created_at': notification.created_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    
    return jsonify({'success': True, 'notifications': data})

# WebSocket обработчики будут добавлены при интеграции с Flask-SocketIO