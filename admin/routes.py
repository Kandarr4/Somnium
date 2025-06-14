#admin/admin_routes.py
import logging
import os
import json
import urllib.parse
from flask import render_template, request, redirect, url_for, flash, jsonify, send_from_directory, current_app, abort
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from decorators import admin_required  # Импорт декоратора
from transliterate import translit  # Убедитесь, что библиотека transliterate установлена
from . import admin_bp
from models import User, Service, Category, SubCategory, Product, Tariff, ServiceTariff, UserTariff, Mail, Attachment, db  # Импортируйте нужные модели и db
from flask_socketio import Namespace
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import event

# Функция для создания файловых папок для категорий и подкатегорий
def create_files_folders(category_name, subcategory_name):
    category_folder = os.path.join(current_app.config['FILES_FOLDER'], secure_filename(category_name))
    subcategory_folder = os.path.join(category_folder, secure_filename(subcategory_name))
    os.makedirs(subcategory_folder, exist_ok=True)
    current_app.logger.info(f"Created directory: {subcategory_folder}")
    return subcategory_folder

# Проверка разрешенных файловых расширений
def allowed_file(filename):
    file_extension = filename.rsplit('.', 1)[1].lower()
    return '.' in filename and file_extension in current_app.config['ALLOWED_EXTENSIONS']
    
# Событие для удаления файлов-вложений перед удалением письма
@event.listens_for(Mail, 'before_delete')
def delete_attachments_before_delete_mail(mapper, connection, target):
    for attachment in target.attachments:
        try:
            file_path = os.path.join(current_app.config['FILES_FOLDER'], attachment.filepath)
            if os.path.exists(file_path):
                os.remove(file_path)
                current_app.logger.info(f"Файл удалён: {file_path}")
            else:
                current_app.logger.warning(f"Файл не найден: {file_path}")
        except Exception as e:
            current_app.logger.error(f"Ошибка при удалении файла {file_path}: {e}")


class AdminNamespace(Namespace):
    def on_connect(self):
        if not current_user.is_authenticated or not current_user.is_admin:
            return False  # Отклоняем подключение
        print(f"Client connected: {request.sid}")

    def on_disconnect(self):
        print(f"Client disconnected: {request.sid}")

@admin_bp.route('/')
@login_required
@admin_required
def admin_dashboard():
    from models import Category, Elements
    
    categories = Category.query.all()
    elements_list = Elements.query.all()
    users = User.query.all()
    services = Service.query.all()

    recipients_incoming = db.session.query(Mail.recipient).filter_by(direction='incoming').distinct().all()
    recipients_outgoing = db.session.query(Mail.sender).filter_by(direction='outgoing').distinct().all()
    
    # Объединяем и убираем дубликаты
    recipients = list(set([r[0] for r in recipients_incoming + recipients_outgoing]))
    recipient_list = sorted(recipients)

    selected_recipient = request.args.get('recipient', recipient_list[0] if recipient_list else None)

    incoming_threads = []
    outgoing_threads = []

    if selected_recipient:
        # Входящие письма
        incoming_emails_query = Mail.query.filter_by(recipient=selected_recipient, direction='incoming')
        incoming_emails = incoming_emails_query.order_by(Mail.received_at.asc()).all()
        
        # Группировка входящих писем по thread_id
        incoming_threads_map = {}
        for email in incoming_emails:
            thread_key = email.thread_id if email.thread_id else email.id
            if thread_key not in incoming_threads_map:
                incoming_threads_map[thread_key] = []
            incoming_threads_map[thread_key].append(email)
        
        incoming_threads = sorted(incoming_threads_map.values(), key=lambda x: x[0].received_at)

        # Исходящие письма
        outgoing_emails_query = Mail.query.filter_by(sender=selected_recipient, direction='outgoing')  # Изменено здесь
        outgoing_emails = outgoing_emails_query.order_by(Mail.received_at.asc()).all()
        
        # Группировка исходящих писем по thread_id
        outgoing_threads_map = {}
        for email in outgoing_emails:
            thread_key = email.thread_id if email.thread_id else email.id
            if thread_key not in outgoing_threads_map:
                outgoing_threads_map[thread_key] = []
            outgoing_threads_map[thread_key].append(email)
        
        outgoing_threads = sorted(outgoing_threads_map.values(), key=lambda x: x[0].received_at)

    logging.info(f"Selected Recipient: {selected_recipient}")
    logging.info(f"Number of incoming threads fetched: {len(incoming_threads)}")
    logging.info(f"Number of outgoing threads fetched: {len(outgoing_threads)}")  # Добавлено

    return render_template(
        'admin/admin_dashboard.html',
        categories=categories,
        Elements=elements_list,
        users=users,
        services=services,
        recipients=recipient_list,
        selected_recipient=selected_recipient,
        incoming_threads=incoming_threads,
        outgoing_threads=outgoing_threads
    )
    
@admin_bp.route('/emails/mark_read/<int:email_id>', methods=['POST'])
@login_required
@admin_required
def mark_email_read(email_id):
    email = Mail.query.get_or_404(email_id)
    if email.status != 'Прочитанно':
        email.status = 'Прочитанно'
        db.session.commit()
        return jsonify({"status": "Email marked as read"}), 200
    return jsonify({"status": "Email already read"}), 200



@admin_bp.route('/emails/mark_read_thread/<int:thread_id>', methods=['POST'])
@login_required
@admin_required
def mark_thread_read(thread_id):
    # Получаем все письма в потоке, включая корневое письмо
    emails = Mail.query.filter(
        (Mail.thread_id == thread_id) | (Mail.id == thread_id)
    ).all()

    if not emails:
        return jsonify({"status": "No emails found in the thread"}), 404

    updated = False
    for email in emails:
        if email.status != 'Прочитанно':
            email.status = 'Прочитанно'
            updated = True

    if updated:
        db.session.commit()
        return jsonify({"status": "All emails in the thread marked as read"}), 200
    else:
        return jsonify({"status": "All emails already read"}), 200

@admin_bp.route('/emails/content/<int:email_id>', methods=['GET'])
@login_required
@admin_required
def email_content(email_id):
    email = Mail.query.get_or_404(email_id)
    
    # Определяем корневое письмо потока
    if email.thread_id:
        root_mail = Mail.query.get(email.thread_id)
        if not root_mail:
            abort(404, description="Корневое письмо не найдено.")
    else:
        root_mail = email  # Корневое письмо
    
    # Получаем все письма в потоке
    thread_emails = Mail.query.filter(
        (Mail.thread_id == root_mail.id) | (Mail.id == root_mail.id)
    ).order_by(Mail.received_at.asc()).all()
    
    # Подготовка данных для каждого письма
    thread_data = []
    for mail in thread_emails:
        attachments = [
            {
                "filename": attachment.filename,
                "filepath": url_for('static', filename=f"Email/Files/{mail.recipient}/{mail.received_at.strftime('%Y-%m-%d')}/{attachment.filename}").replace('\\', '/')
            }
            for attachment in mail.attachments
        ]
        
        children = [
            {
                "id": child.id,
                "sender": child.sender,
                "subject": child.subject,
                "received_at": child.received_at.isoformat(),
                "status": child.status
            }
            for child in Mail.query.filter_by(parent_id=mail.id).order_by(Mail.received_at.asc()).all()
        ]
        
        thread_data.append({
            "id": mail.id,
            "thread_id": mail.thread_id,
            "sender": mail.sender,
            "subject": mail.subject,
            "body": mail.body,
            "received_at": mail.received_at.isoformat(),
            "status": mail.status,
            "attachments": attachments,
            "children": children
        })
    
    return jsonify({
        "thread_id": root_mail.id,
        "emails": thread_data
    })


def delete_mail_recursively(mail, files_folder, processed_ids=None):
    if processed_ids is None:
        processed_ids = set()

    # Проверяем, было ли письмо уже обработано
    if mail.id in processed_ids:
        return

    # Добавляем текущее письмо в обработанные
    processed_ids.add(mail.id)

    # Удаляем дочерние письма через parent
    for child in mail.children:
        delete_mail_recursively(child, files_folder, processed_ids)

    # Удаляем дочерние письма через thread
    for thread_child in mail.thread_children:
        delete_mail_recursively(thread_child, files_folder, processed_ids)

    # Удаляем вложения
    for attachment in mail.attachments:
        try:
            # Формируем полный путь к файлу
            file_path = os.path.join(files_folder, attachment.filepath)
            if os.path.exists(file_path):
                os.remove(file_path)
                current_app.logger.info(f"Файл удалён: {file_path}")
            else:
                current_app.logger.warning(f"Файл не найден: {file_path}")
        except Exception as e:
            current_app.logger.error(f"Ошибка при удалении файла {file_path}: {e}")
        finally:
            db.session.delete(attachment)

    # Удаляем само письмо
    db.session.delete(mail)

@admin_bp.route('/emails/delete/<int:email_id>', methods=['DELETE'])
@login_required
@admin_required
def delete_email(email_id):
    email = Mail.query.get_or_404(email_id)
    
    # Определяем корневое письмо потока
    if email.thread_id:
        root_mail = Mail.query.get(email.thread_id)
        if not root_mail:
            abort(404, description="Корневое письмо не найдено.")
    else:
        root_mail = email  # Корневое письмо
    
    try:
        db.session.delete(root_mail)
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Ошибка при удалении писем: {e}")
        abort(500, description="Ошибка при удалении писем.")
    
    message = "Письмо и связанные с ним потоки успешно удалены."
    return jsonify({"message": message}), 200
    
@admin_bp.route('/emails/<recipient>', methods=['GET'])
@login_required
@admin_required
def get_emails_by_recipient(recipient):
    direction = request.args.get('direction', 'incoming')  # 'incoming' или 'outgoing'

    # Получаем письма для выбранного получателя и направления
    if direction == 'incoming':
        emails = Mail.query.filter_by(recipient=recipient, direction='incoming').order_by(Mail.received_at.asc()).all()
    elif direction == 'outgoing':
        # Здесь изменяем фильтрацию на sender вместо recipient
        emails = Mail.query.filter_by(sender=recipient, direction='outgoing').order_by(Mail.received_at.asc()).all()
    else:
        return jsonify({'status': 'error', 'message': 'Неверное направление'}), 400

    # Группировка писем по thread_id
    threads = {}
    for email in emails:
        thread_key = email.thread_id if email.thread_id else email.id
        if thread_key not in threads:
            threads[thread_key] = []
        threads[thread_key].append(email)

    # Преобразование в список потоков
    thread_list = list(threads.values())

    # Сортировка потоков по дате первого письма
    sorted_threads = sorted(thread_list, key=lambda x: x[0].received_at, reverse=False)

    # Подготовка данных для JSON ответа
    threads_data = []
    for thread in sorted_threads:
        thread_emails = []
        for mail in thread:
            attachments = [
                {
                    "filename": attachment.filename,
                    "filepath": url_for('static', filename=f"Email/Files/{mail.recipient}/{mail.received_at.strftime('%Y-%m-%d')}/{attachment.filename}").replace('\\', '/')
                }
                for attachment in mail.attachments
            ]
            thread_emails.append({
                "id": mail.id,
                "thread_id": mail.thread_id,
                "sender": mail.sender,
                "recipient": mail.recipient,
                "subject": mail.subject or "Без темы",
                "body": mail.body or "",
                "received_at": mail.received_at.isoformat(),
                "status": mail.status,
                "attachments": attachments
            })
        threads_data.append(thread_emails)

    return jsonify(threads_data)

@admin_bp.route('/category/add', methods=['POST'])
@login_required
@admin_required
def add_category():
    name = request.form['name']
    description = request.form['description']
    image = request.files.get('image')
    
    new_category = Category(name=name, description=description)
    db.session.add(new_category)
    db.session.commit()  # Сохраняем категорию, чтобы получить ID

    if image and allowed_file(image.filename):
        # Используем current_app.config для получения пути
        ext = image.filename.rsplit('.', 1)[1].lower()
        image_filename = f"{new_category.id}.{ext}"
        image_path = os.path.join(current_app.config['CATEGORY_FOLDER'], image_filename)
        image.save(image_path)
        new_category.image = image_filename
        db.session.commit()  # Сохраняем изменения в категории

    return redirect(url_for('admin_bp.admin_dashboard'))


@admin_bp.route('/subcategory/add/<int:category_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def add_subcategory(category_id):
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        image = request.files['image']
        
        image_filename = None
        
        if image and allowed_file(image.filename):
            image_filename = secure_filename(image.filename)
            image_path = os.path.join(current_app.config['SUBCATEGORY_FOLDER'], image_filename)  # Используем current_app
            image.save(image_path)
        
        new_subcategory = SubCategory(
            name=name,
            description=description,
            image=image_filename,
            category_id=category_id
        )
        db.session.add(new_subcategory)
        db.session.commit()
        return redirect(url_for('admin_bp.view_category', id=category_id))
    
    return render_template('add_subcategory.html', category_id=category_id)

@admin_bp.route('/add_product', methods=['POST'])
@login_required
@admin_required
def add_product():
    name = request.form['name']
    description = request.form['description']
    price = float(request.form['price'])
    subcategory_id = int(request.form['subcategory_id'])

    # Получаем подкатегорию и ее категорию
    subcategory = SubCategory.query.get_or_404(subcategory_id)
    category = subcategory.category
    category_id = subcategory.category_id

    # Генерируем 12-значный артикул
    product_count = Product.query.count() + 1
    article_number = f"{category_id:04d}-{subcategory_id:04d}-{product_count:04d}"

    # Создаем новый продукт
    new_product = Product(
        name=name,
        description=description,
        price=price,
        article=article_number,
        subcategory_id=subcategory_id,
    )

    db.session.add(new_product)
    db.session.commit()

    # Обработка загрузки изображения
    if 'image' in request.files:
        image_file = request.files['image']
        if image_file.filename != '':
            ext = image_file.filename.rsplit('.', 1)[1].lower()
            image_filename = f"{new_product.id}.{ext}"
            image_folder = os.path.join(admin_bp.root_path, '..', app.config['PRODUCT_FOLDER'], category.name, subcategory.name)

            # Убедимся, что пути не содержат проблемных символов
            image_folder = os.path.normpath(image_folder)
            os.makedirs(image_folder, exist_ok=True)
            
            image_path = os.path.join(image_folder, image_filename)
            image_file.save(image_path)
            new_product.image = image_filename

            # Печатаем отладочную информацию
            print(f"Image folder: {image_folder}")
            print(f"Image filename: {image_filename}")
            print(f"Directories created: {os.path.isdir(image_folder)}")
            print(f"Image saved: {os.path.exists(image_path)}")

    db.session.commit()

    return redirect(url_for('admin_bp.view_subcategory', subcategory_id=subcategory_id))

@admin_bp.route('/create_json', methods=['POST'])
@login_required
@admin_required
def create_json():
    data = request.json.get('data', [])
    styles = request.json.get('styles', {})
    merges = request.json.get('merges', [])
    widths = request.json.get('widths', [])
    heights = request.json.get('heights', [])
    product_id = request.json.get('product_id')
    category_name = request.json.get('category_name')
    subcategory_name = request.json.get('subcategory_name')
    image_list = request.json.get('image_list', [])

    if not category_name or not subcategory_name:
        return jsonify({"message": "Ошибка: отсутствует название категории или подкатегории"}), 400

    # Создание директории для хранения файлов
    directory_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', category_name, subcategory_name)
    os.makedirs(directory_path, exist_ok=True)
    file_path = os.path.join(directory_path, f'{product_id}.json')

    try:
        # Сохранение данных в JSON-файл
        with open(file_path, 'w', encoding='utf-8') as json_file:
            json.dump({
                'data': data,
                'styles': styles,
                'merges': merges,
                'widths': widths,
                'heights': heights
            }, json_file, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"message": f"Ошибка при сохранении файла: {str(e)}"}), 500

    allowed_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp')

    # Декодируем и обрабатываем относительные пути изображений
    decoded_image_list = [get_relative_image_path(urllib.parse.unquote(image)) for image in image_list]

    for root, dirs, files in os.walk(directory_path):
        for file in files:
            if file.startswith(str(product_id)) and not file.endswith('.json'):
                if file.lower().endswith(allowed_extensions):
                    if file not in decoded_image_list:
                        print(f"Удаление файла: {file}")
                        os.remove(os.path.join(root, file))

    # Обновление данных о продукте
    product = Product.query.get_or_404(product_id)
    product.specs_file = f'{product_id}.json'
    db.session.commit()

    return jsonify({"message": "Файл успешно сохранен", "file_path": file_path})

def get_relative_image_path(full_path):
    """Преобразует полный URL изображения в относительный путь."""
    # Проверяем наличие полного URL и преобразуем его в относительный
    parsed_url = urllib.parse.urlparse(full_path)
    if parsed_url.netloc in ['127.0.0.1', 'localhost']:
        # Удаляем 'http://localhost:5000/' и оставляем только относительный путь
        return parsed_url.path.lstrip('/')
    return full_path

@admin_bp.route('/uploads/<folder>/<filename>')
@admin_bp.route('/uploads/<folder>/<category>/<filename>')
@admin_bp.route('/uploads/<folder>/<category>/<subcategory>/<filename>')
@login_required
@admin_required
def uploaded_file(folder, filename, category=None, subcategory=None):
    if folder == 'category':
        return send_from_directory(current_app.config['CATEGORY_FOLDER'], filename)
    elif folder == 'sub_category':
        return send_from_directory(current_app.config['SUBCATEGORY_FOLDER'], filename)
    elif folder == 'product' and category and subcategory:
        product_folder = os.path.join(current_app.config['PRODUCT_FOLDER'], secure_filename(category), secure_filename(subcategory))
        return send_from_directory(product_folder, filename)
    else:
        return "Invalid folder", 404

@admin_bp.route('/upload_image', methods=['POST'])
@login_required
@admin_required
def upload_image():
    image = request.files.get('image')
    product_id = request.form.get('product_id')
    category_name = urllib.parse.unquote(request.form.get('category_name'))
    subcategory_name = urllib.parse.unquote(request.form.get('subcategory_name'))

    if not image or not allowed_file(image.filename):
        return jsonify({'message': 'Неверный формат файла'}), 400
    
    base_folder = os.path.join(current_app.config['FILES_FOLDER'], secure_filename(category_name), secure_filename(subcategory_name))
    os.makedirs(base_folder, exist_ok=True)

    existing_images = os.listdir(base_folder)
    image_name_base = f'{product_id}'
    ext = image.filename.rsplit('.', 1)[1].lower()
    
    count = 1
    while f"{image_name_base} ({count}).{ext}" in existing_images:
        count += 1
    
    image_filename = f"{image_name_base} ({count}).{ext}"
    image_path = os.path.join(base_folder, image_filename)
    
    image.save(image_path)
    image_url = url_for('static', filename=os.path.relpath(image_path, current_app.config['UPLOAD_FOLDER']).replace("\\", "/"))
    
    return jsonify({'image_url': image_url})

@admin_bp.route('/category/edit', methods=['POST'])
@login_required
@admin_required
def edit_category():
    category_id = request.form['id']
    category = Category.query.get_or_404(category_id)
    category.name = request.form['name']
    category.description = request.form['description']
    
    if 'image' in request.files:
        image = request.files['image']
        if image.filename and allowed_file(image.filename):
            ext = image.filename.rsplit('.', 1)[1].lower()
            image_filename = f"{category_id}.{ext}"
            image_path = os.path.join(current_app.config['CATEGORY_FOLDER'], image_filename)
            image.save(image_path)
            category.image = image_filename
        else:
            flash('Недопустимый формат файла', 'error')
    
    db.session.commit()
    return redirect(url_for('admin_bp.admin_dashboard'))

@admin_bp.route('/edit_subcategory', methods=['POST'])
@login_required
@admin_required
def edit_subcategory():
    subcategory_id = request.form.get('id')
    name = request.form.get('name')
    description = request.form.get('description')
    image = request.files.get('image')

    subcategory = SubCategory.query.get(subcategory_id)
    if subcategory:
        subcategory.name = name
        subcategory.description = description
        if image and allowed_file(image.filename):
            filename = secure_filename(image.filename)
            image_path = os.path.join(current_app.config['SUBCATEGORY_FOLDER'], filename)
            image.save(image_path)
            subcategory.image = filename
        db.session.commit()
        flash('Подкатегория успешно обновлена', 'success')
    else:
        flash('Подкатегория не найдена', 'error')
    
    return redirect(url_for('admin_bp.view_category', id=subcategory.category_id))

@admin_bp.route('/edit_product/<int:product_id>', methods=['POST'])
@login_required
@admin_required
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)
    product.name = request.form['name']
    product.description = request.form['description']
    product.price = float(request.form['price'])
    subcategory = product.subcategory
    category = subcategory.category

    if 'image' in request.files:
        image_file = request.files['image']
        if image_file.filename and allowed_file(image_file.filename):
            ext = image_file.filename.rsplit('.', 1)[1].lower()
            image_filename = f"{product.id}.{ext}"
            image_folder = os.path.join(current_app.config['PRODUCT_FOLDER'], secure_filename(category.name), secure_filename(subcategory.name))
            os.makedirs(image_folder, exist_ok=True)
            
            image_path = os.path.join(image_folder, image_filename)
            image_file.save(image_path)
            product.image = image_filename

            print(f"Image folder: {image_folder}")
            print(f"Image filename: {image_filename}")
            print(f"Directories created: {os.path.isdir(image_folder)}")
            print(f"Image saved: {os.path.exists(image_path)}")

    db.session.commit()
    return redirect(url_for('admin_bp.view_subcategory', subcategory_id=subcategory.id))

@admin_bp.route('/category/delete', methods=['POST'])
@login_required
@admin_required
def delete_category():
    category_id = request.form['id']
    category = Category.query.get_or_404(category_id)
    
    # Удаление изображения категории, если оно существует
    if category.image:
        image_path = os.path.join(current_app.config['CATEGORY_FOLDER'], category.image)
        if os.path.exists(image_path):
            os.remove(image_path)
    
    # Удаление категории из базы данных
    db.session.delete(category)
    db.session.commit()
    return redirect(url_for('admin_bp.admin_dashboard'))

@admin_bp.route('/delete_subcategory', methods=['POST'])
@login_required
@admin_required
def delete_subcategory():
    subcategory_id = request.form.get('id')
    action = request.form.get('action')
    # Логирование для отладки
    current_app.logger.info(f"Получен запрос на удаление подкатегории с id: {subcategory_id} и action: {action}")
    if not subcategory_id or not action:
        return jsonify({'success': False, 'message': 'Необходимые параметры отсутствуют'}), 400
    subcategory = SubCategory.query.get(subcategory_id)
    if subcategory:
        if action == 'delete_only':
            for product in subcategory.products:
                product.subcategory_id = None
            db.session.delete(subcategory)
        elif action == 'delete_and_move':
            new_subcategory_id = request.form.get('new_subcategory_id')
            if new_subcategory_id:
                products_to_move = [product.id for product in subcategory.products]
                if products_to_move:
                    move_products({
                        'product_ids': products_to_move,
                        'new_subcategory_id': new_subcategory_id
                    })
                db.session.delete(subcategory)
            else:
                return jsonify({'success': False, 'message': 'new_subcategory_id не указан'}), 400
        elif action == 'delete_all':
            for product in subcategory.products:
                db.session.delete(product)
            db.session.delete(subcategory)
        db.session.commit()
        return jsonify({'success': True, 'redirect': url_for('admin_bp.view_category', id=subcategory.category_id)})
    return jsonify({'success': False, 'message': 'Подкатегория не найдена'}), 404



@admin_bp.route('/delete_product', methods=['POST'])
@login_required
@admin_required
def delete_product():
    product_id = request.form.get('product_id')

    print(f"Received request to delete product with ID: {product_id}")

    if product_id:
        try:
            product = db.session.get(Product, product_id)
            if product:
                print(f"Deleting product with ID: {product_id} and image: {product.image}")
                # Удаление файла изображения, если он существует
                if product.image:
                    image_folder = os.path.join(admin_bp.root_path, '..', app.config['PRODUCT_FOLDER'], secure_filename(product.subcategory.category.name), secure_filename(product.subcategory.name))
                    image_path = os.path.join(image_folder, product.image)
                    if os.path.exists(image_path):
                        os.remove(image_path)
                        print(f"Deleted image at path: {image_path}")
                    else:
                        print(f"Image path does not exist: {image_path}")
                db.session.delete(product)
                db.session.commit()
                print(f"Product with ID {product_id} deleted successfully")
            else:
                print(f"Product with ID {product_id} not found")
        except Exception as e:
            print(f"Error occurred while deleting product with ID {product_id}: {e}")
    else:
        print("Product ID is None")

    print("Deletion completed")
    return redirect(url_for('admin_bp.view_subcategory', subcategory_id=product.subcategory_id))

@admin_bp.route('/move_subcategory', methods=['POST'])
@login_required
@admin_required
def move_subcategory():
    data = request.get_json()
    subcategory_id = data['subcategory_id']
    new_category_id = data['new_category_id']
    
    subcategory = SubCategory.query.get(subcategory_id)
    if subcategory:
        subcategory.category_id = new_category_id
        db.session.commit()
        return jsonify({'status': 'success'}), 200
    return jsonify({'status': 'error', 'message': 'Subcategory not found'}), 404

@admin_bp.route('/move_product', methods=['POST'])
@login_required
@admin_required
def move_product():
    data = request.get_json()
    product_ids = data['product_ids']
    new_subcategory_id = data['new_subcategory_id']

    for product_id in product_ids:
        product = Product.query.get(product_id)
        if product:
            product.subcategory_id = new_subcategory_id
    db.session.commit()

    return jsonify({'status': 'success'}), 200

@admin_bp.route('/category/<int:id>')
@login_required
@admin_required
def view_category(id):
    category = Category.query.get_or_404(id)
    subcategories = category.subcategories
    return render_template('admin/view_category.html', category=category, subcategories=subcategories)

@admin_bp.route('/subcategory/<int:subcategory_id>')
@login_required
@admin_required
def view_subcategory(subcategory_id):
    subcategory = SubCategory.query.get_or_404(subcategory_id)
    category = subcategory.category
    products = subcategory.products
    return render_template('admin/view_subcategory.html', subcategory=subcategory, category=category, products=products)

@admin_bp.route('/product/<int:product_id>')
@login_required
@admin_required
def view_product(product_id):
    product = Product.query.get_or_404(product_id)
    category = product.subcategory.category.name
    subcategory = product.subcategory.name

    if product.specs_file:
        json_file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', category, subcategory, product.specs_file)
    else:
        json_file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', 'table_placeholder.json')

    # Преобразуем путь в относительный путь для использования в шаблоне
    file_path = url_for('static', filename=os.path.relpath(json_file_path, os.path.join(admin_bp.root_path, '..', 'static')).replace("\\", "/"))

    return render_template('admin/view_product.html', 
                           category=category, 
                           subcategory=subcategory, 
                           product=product, 
                           file_path=file_path)

@admin_bp.route('/elements', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_elements():
    if request.method == 'POST':
        name = request.form['name']
        transliterated_name = translit(name, 'ru', reversed=True).replace(' ', '_')
        file_name = f'{transliterated_name}.html'
        file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', 'elements', file_name)

        # Создаем пустой HTML-файл
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('<!DOCTYPE html>\n<html lang="ru">\n<head>\n<meta charset="UTF-8">\n<title>Новый элемент</title>\n</head>\n<body>\n<!-- Начните редактирование здесь -->\n</body>\n</html>')

        new_element = Elements(name=name, html_file=file_name)
        db.session.add(new_element)
        db.session.commit()
        return redirect(url_for('admin_bp.admin_elements'))
    
    elements_list = Elements.query.all()
    categories = Category.query.all()
    return render_template('admin_elements.html', categories=categories, elements_list=elements_list)

@admin_bp.route('/elements/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_element(id):
    element = Elements.query.get_or_404(id)
    file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', 'elements', element.html_file)
    
    if request.method == 'POST':
        # Получаем содержимое формы и сохраняем в HTML файл
        html_content = request.form['html_content']
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        element.name = request.form['name']
        transliterated_name = translit(element.name, 'ru', reversed=True).replace(' ', '_')
        file_name = f'{transliterated_name}.html'
        new_file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', 'elements', file_name)
        
        if file_path != new_file_path:  # Переименовываем файл, если название изменилось
            os.rename(file_path, new_file_path)
            element.html_file = file_name
        
        db.session.commit()
        return jsonify({'success': True})
    
    # Читаем данные элемента из файла
    with open(file_path, 'r', encoding='utf-8') as f:
        element_data = f.read()
    
    return jsonify({
        'element': {
            'id': element.id,
            'name': element.name
        },
        'element_data': element_data
    })

@admin_bp.route('/elements/update_preview', methods=['POST'])
def update_preview():
    html_content = request.form.get('html_content')
    return jsonify({'updated_html': html_content})

@admin_bp.route('/elements/save/<int:id>', methods=['POST'])
def save_element(id):
    element = Elements.query.get_or_404(id)
    name = request.form.get('name')
    html_content = request.form.get('html_content')

    # Логируем данные, которые пришли от клиента
    print(f"Полученные данные: имя - {name}, первые 100 символов содержимого - {html_content[:100]}")

    element.name = name
    file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', 'elements', element.html_file)
    try:
        with open(file_path, 'w', encoding='utf-8') as file:
            file.write(html_content)
        db.session.commit()
        print(f"Элемент {id} успешно сохранен с обновленным содержимым.")
        return jsonify({'success': True})
    except Exception as e:
        print(f"Ошибка при сохранении элемента {id}: {e}")
        return jsonify({'success': False, 'error': str(e)})

@admin_bp.route('/elements/delete/<int:id>', methods=['POST'])
@login_required
@admin_required
def delete_element(id):
    element = Elements.query.get_or_404(id)
    file_path = os.path.join(admin_bp.root_path, '..', 'static', 'files', 'elements', element.html_file)
    if os.path.exists(file_path):
        os.remove(file_path)
    db.session.delete(element)
    db.session.commit()
    return redirect(url_for('admin_bp.admin_elements'))

# маршруты для управления сервисами и тарифами


# Отображение списка сервисов и тарифов на панели администратора
@admin_bp.route('/services')
@login_required
@admin_required
def services():
    services = Service.query.all()
    return render_template('admin/admin_dashboard.html', services=services)

# Добавление нового сервиса
@admin_bp.route('/add_service', methods=['POST'])
@login_required
@admin_required
def add_service():
    name = request.form.get('name')
    blueprint = request.form.get('blueprint')
    description = request.form.get('description')
    
    if Service.query.filter_by(blueprint=blueprint).first():
        flash('Сервис с таким blueprint уже существует.', 'danger')
        return redirect(url_for('admin_bp.services'))

    new_service = Service(name=name, blueprint=blueprint, description=description)
    db.session.add(new_service)
    db.session.commit()
    flash('Сервис успешно добавлен.', 'success')
    return redirect(url_for('admin_bp.services'))

@admin_bp.route('/edit_service/<int:service_id>', methods=['POST'])
@login_required
@admin_required
def edit_service(service_id):
    service = Service.query.get_or_404(service_id)
    service.name = request.form.get('name')
    service.blueprint = request.form.get('blueprint')
    service.description = request.form.get('description')
    db.session.commit()
    flash('Сервис успешно обновлён.', 'success')
    return redirect(url_for('admin_bp.admin_dashboard'))


# Удаление сервиса
@admin_bp.route('/delete_service/<int:service_id>', methods=['POST'])
@login_required
@admin_required
def delete_service(service_id):
    service = Service.query.get(service_id)
    if not service:
        flash('Сервис не найден.', 'danger')
        return redirect(url_for('admin_bp.services'))

    db.session.delete(service)
    db.session.commit()
    flash('Сервис удален.', 'success')
    return redirect(url_for('admin_bp.services'))

@admin_bp.route('/delete_tariff/<int:service_tariff_id>', methods=['POST'])
@login_required
@admin_required
def delete_tariff(service_tariff_id):
    service_tariff = ServiceTariff.query.get_or_404(service_tariff_id)
    db.session.delete(service_tariff)
    db.session.commit()
    flash('Тариф удален.', 'success')
    return redirect(url_for('admin_bp.services'))

# Добавление нового тарифа к сервису
@admin_bp.route('/add_tariff_to_service/<int:service_id>', methods=['POST'])
@login_required
@admin_required
def add_tariff_to_service(service_id):
    service = Service.query.get(service_id)
    if not service:
        flash('Сервис не найден.', 'danger')
        return redirect(url_for('admin_bp.services'))

    # Получение данных из формы
    name = request.form.get('name')
    description = request.form.get('description')
    price = request.form.get('price', type=float)
    duration_days = request.form.get('duration_days', type=int)
    is_free = True if request.form.get('is_free') == 'on' else False
    features_str = request.form.get('features')

    # Валидация и парсинг JSON
    try:
        features = json.loads(features_str) if features_str else {}
    except json.JSONDecodeError:
        flash('Ошибка: некорректный формат JSON для настроек тарифа.', 'danger')
        return redirect(url_for('admin_bp.services'))

    # Создание нового тарифа
    tariff = Tariff(
        name=name,
        description=description,
        price=price,
        duration_days=duration_days,
        is_free=is_free,
        features=features
    )
    db.session.add(tariff)
    db.session.commit()

    # Привязка тарифа к сервису
    service_tariff = ServiceTariff(
        tariff_id=tariff.id,
        service_id=service.id,
        max_elements=features.get('max_responses_per_survey')  # Пример использования
    )
    db.session.add(service_tariff)
    db.session.commit()

    flash('Тариф успешно добавлен к сервису.', 'success')
    return redirect(url_for('admin_bp.services'))
    
@admin_bp.route('/edit_tariff/<int:service_tariff_id>', methods=['POST'])
@login_required
@admin_required
def edit_tariff(service_tariff_id):
    service_tariff = ServiceTariff.query.get_or_404(service_tariff_id)
    tariff = service_tariff.tariff

    # Получаем данные из формы
    name = request.form.get('name')
    description = request.form.get('description')
    price = request.form.get('price', type=float)
    duration_days = request.form.get('duration_days', type=int)
    features_str = request.form.get('features')

    # Обработка JSON данных из поля features
    try:
        features = json.loads(features_str) if features_str else {}
    except json.JSONDecodeError:
        flash("Некорректный формат JSON в поле 'Настройки тарифа'", "danger")
        return redirect(url_for('admin_bp.services'))

    # Обновляем тариф
    tariff.name = name
    tariff.description = description
    tariff.price = price
    tariff.duration_days = duration_days
    tariff.features = features

    db.session.commit()

    flash("Тариф успешно обновлён.", "success")
    return redirect(url_for('admin_bp.services'))
    
@admin_bp.route('/assign_tariff', methods=['POST'])
@login_required
@admin_required
def assign_tariff():
    user_id = request.form.get('user_id')
    tariff_id = request.form.get('tariff_id')
    service_id = request.form.get('service_id')

    if not all([user_id, tariff_id, service_id]):
        flash('Необходимо указать пользователя, тариф и сервис.', 'danger')
        return redirect(url_for('admin_bp.users'))

    user_tariff = assign_tariff_to_user(user_id, tariff_id, service_id)

    if user_tariff:
        flash('Тариф успешно назначен пользователю.', 'success')
    else:
        flash('Произошла ошибка при назначении тарифа.', 'danger')

    return redirect(url_for('admin_bp.users'))
    
@admin_bp.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    
    if user == current_user:
        flash('Вы не можете удалить свой аккаунт.', 'danger')
        return redirect(url_for('admin_bp.users'))
    
    try:
        db.session.delete(user)
        db.session.commit()
        flash('Пользователь успешно удалён.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Произошла ошибка при удалении пользователя.', 'danger')
    
    return redirect(url_for('admin_bp.users'))
    
@admin_bp.route('/edit_user/<int:user_id>', methods=['POST'])
@login_required
@admin_required
def edit_user(user_id):
    user = User.query.get_or_404(user_id)

    # Получение данных из формы
    user.username = request.form.get('username')
    user.email = request.form.get('email')
    user.role = request.form.get('role')
    user.first_name = request.form.get('first_name')
    user.last_name = request.form.get('last_name')
    user.middle_name = request.form.get('middle_name')
    user.phone = request.form.get('phone_number')

    try:
        db.session.commit()
        flash('Пользователь успешно обновлен.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Ошибка при обновлении пользователя: {e}', 'danger')

    return redirect(url_for('admin_bp.admin_dashboard'))