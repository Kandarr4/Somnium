#app.py
import os
import base64
import requests
import eventlet
eventlet.monkey_patch()  
from flask import Flask, render_template, redirect, url_for, flash, jsonify, session, request, send_from_directory, Response, abort, current_app
from flask_cors import CORS
from models import db, User, Mail, Category, SubCategory, Product, Specification, Elements, Service, Tariff, ServiceTariff, UserTariff, SurveyBase
from forms import LoginForm, RegisterForm, AvatarUploadForm, ProfileForm
from config import Config
from functools import wraps
from sqlalchemy import create_engine
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
from flask_wtf.csrf import CSRFError
from extensions import csrf 
import urllib.parse  # Импортируем для декодирования URL
from urllib.parse import unquote, urlparse, urljoin
import pandas as pd
from survey import survey_bp  # Импортируйте блюпринт из survey
from decorators import admin_required
from admin import admin_bp
import logging
from payment import payment_bp  # Импортируйте блюпринт payment_bp
from imapclient import IMAPClient
from flask_socketio import SocketIO  # Импортируем SocketIO

from admin.namespaces import AdminNamespace  # Импортируем Namespace для админки
from messenger.namespaces import MessengerNamespace  # Импортируем Namespace для мессенджера
from smtp_server import send_email
import uuid

# Импортируем блюпринт мессенджера
from messenger import messenger_bp

app = Flask(__name__)
app.config.from_object(Config)

# Инициализация расширений
db.init_app(app)
csrf.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'  # Убедись, что у тебя есть маршрут 'login'

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet',  # Явно указываем async_mode
    transports=["websocket", "polling"],
    logger=True,  # Включаем логирование
    engineio_logger=True  # Включаем логирование Engine.IO
)

# Делаем SocketIO доступным через current_app.socketio
app.socketio = socketio

# Инициализация CORS для всего приложения
CORS(app, resources={
    r"/messenger/*": {
        "origins": "*",
        "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        "allow_headers": ["Content-Type", "X-Requested-With", "X-CSRFToken"],
        "supports_credentials": True  # Важно для сохранения cookies и аутентификации
    }
})

# Подключение вторичной базы данных
from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker

survey_engine = create_engine(app.config['SECONDARY_DATABASE_URI'])
app.config['SURVEY_ENGINE'] = survey_engine

# Регистрация блюпринтов
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(survey_bp, url_prefix='/survey')
app.register_blueprint(payment_bp)
app.register_blueprint(messenger_bp, url_prefix='/messenger')  # Регистрируем блюпринт мессенджера

# Логирование
logging.basicConfig(level=logging.INFO)

# Настройки OAuth
app.config['GOOGLE_CLIENT_ID'] = 'Здесь может быть Ваш ID'
app.config['GOOGLE_CLIENT_SECRET'] = 'Здесь может быть Ваш cекрет'

@socketio.on('connect')
def handle_connect():
    app.logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    app.logger.info(f"Client disconnected: {request.sid}")


# Конфигурация путей для загрузки файлов
UPLOAD_FOLDER = 'static/uploads'
CATEGORY_FOLDER = os.path.join(UPLOAD_FOLDER, 'category')
SUBCATEGORY_FOLDER = os.path.join(UPLOAD_FOLDER, 'sub_category')
PRODUCT_FOLDER = os.path.join(UPLOAD_FOLDER, 'product')
FILES_FOLDER = 'static/files'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'xlsx', 'pdf', 'webp'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['CATEGORY_FOLDER'] = CATEGORY_FOLDER
app.config['SUBCATEGORY_FOLDER'] = SUBCATEGORY_FOLDER
app.config['PRODUCT_FOLDER'] = PRODUCT_FOLDER
app.config['FILES_FOLDER'] = FILES_FOLDER
app.config['ALLOWED_EXTENSIONS'] = ALLOWED_EXTENSIONS  # Добавьте эту строку

# Убедимся, что все папки для загрузок существуют
for folder in [CATEGORY_FOLDER, SUBCATEGORY_FOLDER, PRODUCT_FOLDER, FILES_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Создание папки для вложений чата
CHAT_ATTACHMENTS_FOLDER = os.path.join(UPLOAD_FOLDER, 'chat_attachments')
os.makedirs(CHAT_ATTACHMENTS_FOLDER, exist_ok=True)

# Создание файловых папок для категорий и подкатегорий в FILES_FOLDER
def create_files_folders(category_name, subcategory_name):
    category_folder = os.path.join(FILES_FOLDER, secure_filename(category_name))
    subcategory_folder = os.path.join(category_folder, secure_filename(subcategory_name))

    # Проверка и создание вложенных папок
    os.makedirs(subcategory_folder, exist_ok=True)
    current_app.logger.info(f"Created directory: {subcategory_folder}")

    return subcategory_folder

# Проверка разрешенных файловых расширений
def allowed_file(filename):
    file_extension = filename.rsplit('.', 1)[1].lower()
    return '.' in filename and file_extension in ALLOWED_EXTENSIONS

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    jwks_uri='https://www.googleapis.com/oauth2/v3/certs',
    client_kwargs={'scope': 'openid email profile'}
)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Создание таблиц для survey, если их нет
with app.app_context():
    SurveyBase.metadata.create_all(survey_engine)

# Инициализируем базу данных и создаем таблицы, если они еще не созданы
with app.app_context():
    db.create_all()

def is_safe_url(target):
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in ('http', 'https') and host_url.netloc == redirect_url.netloc


def check_admin():
    admin_user = User.query.filter_by(role='admin').first()
    if not admin_user:
        print("Администратор не найден в базе данных.")
        create_admin = input("Хотите создать администратора? (y/n): ")
        if create_admin.lower() == 'y':
            username = input("Введите имя пользователя для администратора: ")
            email = input("Введите адрес электронной почты для администратора: ")
            phone = input("Введите номер телефона для администратора (необязательно): ")
            first_name = input("Введите имя для администратора (необязательно): ")
            last_name = input("Введите фамилию для администратора (необязательно): ")
            middle_name = input("Введите отчество для администратора (необязательно): ")
            password = input("Введите пароль для администратора: ")

            # Создаем объект администратора с введенными данными
            admin_user = User(
                username=username,
                email=email,
                phone=phone or None,
                first_name=first_name or None,
                last_name=last_name or None,
                middle_name=middle_name or None,
                role='admin',
                avatar='img/placeholder/avatar_placeholder.png'  # Устанавливаем плейсхолдер
            )
            admin_user.set_password(password)
            db.session.add(admin_user)
            db.session.commit()
            print("Администратор успешно создан.")
        else:
            print("Администратор не был создан. Продолжение без администратора.")

# Выполняем проверку администратора при запуске
with app.app_context():
    check_admin()

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return jsonify({"success": False, "message": e.description}), 400

@app.route('/', methods=['GET'])
def index():
    login_form = LoginForm()
    register_form = RegisterForm()
    categories = Category.query.all()
    for category in categories:
        category.subcategories = SubCategory.query.filter_by(category_id=category.id).all()

    breadcrumbs = [
        {"position": 1, "name": "Главная", "item": url_for('index', _external=True)}
    ]

    og_meta = {
        "title": "ИП Somnium - IT компания",
        "description": "Разработка ПО, ремонт ПК, IT-услуги в Темиртау и Караганде.",
        "url": url_for('index', _external=True),
        "image": url_for('static', filename='img/f-logo_mini.png', _external=True)
    }

    return render_template(
        'index.html', 
        login_form=login_form, 
        register_form=register_form, 
        categories=categories, 
        breadcrumbs=breadcrumbs,
        og_meta=og_meta
    )

@csrf.exempt
@app.route('/notify_new_email', methods=['POST'])
def notify_new_email():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    recipient = data.get('recipient')
    email_id = data.get('email_id')

    if not recipient or not email_id:
        return jsonify({"error": "Missing recipient or email_id"}), 400

    app.logger.info(f"Отправка события new_email для получателя {recipient} и email_id {email_id}")

    # Отправляем событие через SocketIO с минимальной информацией
    socketio.emit('new_email', {'recipient': recipient, 'email_id': email_id}, namespace='/admin')
    app.logger.info("Событие new_email отправлено")

    return jsonify({"status": "Notification sent"}), 200


# Регистрация пространств имен SocketIO после регистрации блюпринтов
socketio.on_namespace(AdminNamespace('/admin'))
socketio.on_namespace(MessengerNamespace('/messenger'))  # Регистрируем пространство имен для мессенджера


@app.route('/send_email', methods=['POST'])
@login_required
@admin_required
def send_email_route():
    sender = request.form.get('sender')
    recipient = request.form.get('recipient')
    subject = request.form.get('subject')
    body = request.form.get('body')
    
    # Обработка вложений
    attachments = request.files.getlist('attachments')
    saved_file_paths = []
    
    if attachments:
        for file in attachments:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                # Создаём уникальное имя файла, чтобы избежать конфликтов
                unique_filename = f"{uuid.uuid4().hex}_{filename}"
                save_path = os.path.join(app.config['FILES_FOLDER'], unique_filename)
                try:
                    file.save(save_path)
                    saved_file_paths.append(save_path)
                    app.logger.info(f"Вложение сохранено: {save_path}")
                except Exception as e:
                    app.logger.error(f"Ошибка при сохранении вложения {filename}: {e}")
            else:
                app.logger.warning(f"Недопустимое вложение: {file.filename}")
    
    # Вызов функции отправки письма с вложениями
    success = send_email(sender, recipient, subject, body, attachments=saved_file_paths)
    
    if success:
        return jsonify({'status': 'success', 'message': 'Письмо отправлено'}), 200
    else:
        return jsonify({'status': 'error', 'message': 'Не удалось отправить письмо'}), 500

@app.route('/about', methods=['GET'])
def about():
    login_form = LoginForm()
    register_form = RegisterForm()

    breadcrumbs = [
        {"position": 1, "name": "Главная", "item": url_for('index', _external=True)},
        {"position": 2, "name": "О нас", "item": url_for('about', _external=True)}
    ]

    return render_template('about.html', login_form=login_form, register_form=register_form, breadcrumbs=breadcrumbs)

@app.route('/computer_repair', methods=['GET'])
def computer_repair():
    login_form = LoginForm()
    register_form = RegisterForm()

    breadcrumbs = [
        {"position": 1, "name": "Главная", "item": url_for('index', _external=True)},
        {"position": 2, "name": "Ремонт компьютеров", "item": url_for('computer_repair', _external=True)}
    ]

    return render_template('computer_repair.html', login_form=login_form, register_form=register_form, breadcrumbs=breadcrumbs)

@app.route('/contact', methods=['GET'])
def contact():
    login_form = LoginForm()
    register_form = RegisterForm()

    breadcrumbs = [
        {"position": 1, "name": "Главная", "item": url_for('index', _external=True)},
        {"position": 2, "name": "Контакты", "item": url_for('contact', _external=True)}
    ]

    return render_template('contact.html', login_form=login_form, register_form=register_form, breadcrumbs=breadcrumbs)


@app.route('/present')
def present():
    return render_template('present.html')

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    form = ProfileForm()
    
    if form.validate_on_submit():
        if form.edit_mode.data == '1':
            # Включаем режим редактирования
            return render_template('profile.html', editing=True, form=form)
        
        # Обработка изменений данных пользователя
        current_user.first_name = form.first_name.data
        current_user.last_name = form.last_name.data
        current_user.middle_name = form.middle_name.data
        current_user.email = form.email.data
        current_user.phone = form.phone.data
        
        # Обработка загрузки аватарки, если она была выбрана
        if form.avatar.data:
            avatar_file = form.avatar.data
            if avatar_file and avatar_file.filename != '':
                filename = secure_filename(avatar_file.filename)
                avatar_path = os.path.join(app.static_folder, 'img/avatar', filename)
                avatar_file.save(avatar_path)
                current_user.avatar = f'img/avatar/{filename}'

        # Сохраняем изменения в базе данных
        db.session.commit()
        flash("Изменения профиля сохранены.", "success")
        return redirect(url_for('profile'))
    
    # Если метод GET или форма не валидна
    return render_template('profile.html', editing=False, form=form)


@app.route('/register', methods=['POST'])
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        # Проверка уникальности имени пользователя
        if User.query.filter_by(username=form.username.data).first():
            return jsonify(success=False, message='Имя пользователя уже занято.')

        # Проверка уникальности электронной почты
        if User.query.filter_by(email=form.email.data).first():
            return jsonify(success=False, message='Электронная почта уже зарегистрирована.')

        # Установка пути к аватарке на плейсхолдер
        avatar = 'img/placeholder/avatar_placeholder.png'

        # Создание нового пользователя
        new_user = User(
            username=form.username.data,
            email=form.email.data,
            phone=form.phone.data,
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            middle_name=form.middle_name.data,
            role='user',
            avatar=avatar,
        )
        new_user.set_password(form.password.data)
        db.session.add(new_user)
        db.session.commit()
        
        return jsonify(success=True, message='Аккаунт успешно создан! Вы можете войти.')

    else:
        # Форматируем ошибки для отображения в сообщении
        errors = {field: f"{getattr(form, field).label.text}: {', '.join(errors)}" for field, errors in form.errors.items()}
        return jsonify(success=False, errors=errors)


def is_safe_url(target):
    """Проверка безопасности URL, чтобы убедиться, что он остаётся в пределах домена приложения."""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

logging.basicConfig(level=logging.DEBUG)

@app.route('/login', methods=['POST'])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            (User.username == form.username.data) | 
            (User.email == form.username.data)
        ).first()
        
        if user and user.check_password(form.password.data):
            login_user(user)
            flash('Вход выполнен успешно!', 'success')
            
            # Возвращаем успешный ответ в формате JSON
            return jsonify(success=True, message="Вход выполнен успешно!")
        
        # Если логин не удался, возвращаем JSON с ошибкой
        return jsonify(success=False, message="Неверное имя пользователя, адрес электронной почты или пароль.")
    else:
        # Ошибки валидации формы
        errors = {field: error for field, error in form.errors.items()}
        return jsonify(success=False, errors=errors)

     
@app.route('/login/google')
def login_google():
    # Получаем параметр 'next' из запроса (если он есть)
    next_url = request.args.get('next')
    
    # Проверяем, безопасен ли URL
    if next_url and is_safe_url(next_url):
        session['next'] = next_url  # Сохраняем в сессии
    else:
        session['next'] = url_for('index')  # Устанавливаем URL по умолчанию

    redirect_uri = url_for('auth_callback', _external=True, _scheme='https')
    nonce = base64.urlsafe_b64encode(os.urandom(16)).decode('utf-8')  # Генерация nonce
    session['nonce'] = nonce  # Сохранение nonce в сессии

    return google.authorize_redirect(redirect_uri, nonce=nonce)
    
@app.route('/auth/callback')
def auth_callback():
    try:
        token = google.authorize_access_token()
    except Exception as e:
        current_app.logger.error(f"Ошибка при авторизации через Google: {e}")
        return jsonify({"message": "Ошибка авторизации.", "success": False}), 400
    
    try:
        user_info = google.parse_id_token(token, nonce=session.pop('nonce', None))
    except Exception as e:
        current_app.logger.error(f"Ошибка при парсинге ID токена: {e}")
        return jsonify({"message": "Ошибка авторизации.", "success": False}), 400

    # Извлечение данных пользователя
    email = user_info.get('email')
    first_name = user_info.get('given_name', 'Пользователь')
    last_name = user_info.get('family_name', '')

    # Путь к фото профиля, полученному от Google
    profile_pic_url = user_info.get('picture')
    
    # Поиск пользователя в базе данных
    user = User.query.filter_by(email=email).first()

    # Проверка, если пользователь отсутствует и создаётся новый
    if user is None:
        username = email.split('@')[0]
        avatar_filename = f'{username}_avatar.png'
        avatar_path = f'img/avatar/{avatar_filename}'  # Путь для базы данных
        
        # Попытка загрузки аватарки от Google, если она есть
        if profile_pic_url:
            os.makedirs(os.path.join(app.static_folder, 'img/avatar'), exist_ok=True)
            response = requests.get(profile_pic_url)
            if response.status_code == 200:
                with open(os.path.join(app.static_folder, avatar_path), 'wb') as f:
                    f.write(response.content)
            avatar = avatar_path
        else:
            # Установка изображения-заглушки, если аватарки нет
            avatar = 'img/placeholder/avatar_placeholder.png'
        
        # Создание нового пользователя
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            avatar=avatar,
            password_hash=generate_password_hash(''),  # Пустой пароль для OAuth
            # registered_at будет установлено автоматически
        )
        db.session.add(user)
        db.session.commit()
    else:
        # Установка изображения-заглушки, если аватарка не указана в профиле
        if not user.avatar:
            user.avatar = 'img/placeholder/avatar_placeholder.png'
            db.session.commit()

    login_user(user)

    # Сохранение аватарки и имени пользователя в сессии
    session['profile_pic'] = url_for('static', filename=user.avatar)
    session['username'] = user.first_name if user.first_name else 'Пользователь'

    # Получаем `next_url` из сессии
    next_url = session.pop('next', None)
    
    # Проверяем, что `next_url` безопасен
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('index')  # Перенаправляем на главную страницу по умолчанию

    return redirect(next_url)


@app.route('/logout')
def logout():
    logout_user()
    session.clear()  # Очистка сессии
    return redirect(url_for('index'))

@app.route('/get_products_by_subcategory', methods=['POST'])
def get_products_by_subcategory():
    data = request.get_json()
    subcategory_id = data['subcategory_id']

    products = Product.query.filter_by(subcategory_id=subcategory_id).all()
    product_ids = [product.id for product in products]

    return jsonify({'status': 'success', 'product_ids': product_ids})

                           
@app.route('/catalog/product/<int:product_id>')
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    category = product.subcategory.category.name  # Объект категории
    subcategory = product.subcategory.name    # Название подкатегории

    # Определяем путь к спецификациям
    if product.specs_file:
        json_file_path = os.path.join('static', 'files', category, subcategory, product.specs_file)
    else:
        json_file_path = os.path.join('static', 'files', 'table_placeholder.json')

    file_path = url_for('static', filename=os.path.relpath(json_file_path, 'static').replace("\\", "/"))

    # Загружаем интерактивные элементы
    elements_contents = []
    if product.elements:
        element_filenames = [filename.strip() for filename in product.elements.split(',') if filename.strip()]
        for filename in element_filenames:
            element_path = os.path.join('static', 'files', category, subcategory, str(product.id), filename)
            absolute_element_path = os.path.join(app.root_path, element_path)

            # Отладочный вывод
            print(f"Ищу элемент по пути: {absolute_element_path}")

            if os.path.exists(absolute_element_path):
                print(f"Файл найден: {filename}")
                with open(absolute_element_path, 'r', encoding='utf-8') as file:
                    content = file.read()
                    elements_contents.append(content)
            else:
                print(f"Файл не найден: {filename}")
                elements_contents.append(f"<!-- Элемент {filename} не найден -->")

    return render_template('product.html', 
                           category=category,  
                           subcategory=subcategory, 
                           product=product, 
                           file_path=file_path,
                           elements_contents=elements_contents)


@app.route('/category_info/<int:category_id>')
def category_info(category_id):
    category = Category.query.get_or_404(category_id)

    # Путь к JSON файлу для категории
    json_file_path = os.path.join('static', 'files', category.name, f'{category_id}.json')

    # Преобразуем путь в относительный путь для использования в шаблоне
    file_path = url_for('static', filename=os.path.relpath(json_file_path, 'static').replace("\\", "/"))

    return render_template('category_info.html', category=category, file_path=file_path)

@app.route('/categories')
def categories():
    categories = Category.query.all()  # Получаем список всех категорий
    categories_data = []
    for category in categories:
        subcategories = SubCategory.query.filter_by(category_id=category.id).all()
        categories_data.append({
            'id': category.id,  # Добавляем ID категории
            'name': category.name,
            'image': url_for('static', filename=f'uploads/category/{category.image}'),
            'subcategories': [{'name': sub.name} for sub in subcategories]
        })
    return jsonify(categories_data)
    
@app.route('/category/<int:category_id>/subcategories')
def get_subcategories(category_id):
    app.logger.info(f"Fetching subcategories for category ID: {category_id}")
    subcategories = SubCategory.query.filter_by(category_id=category_id).all()
    if not subcategories:
        app.logger.warning(f"No subcategories found for category ID: {category_id}")
        return jsonify({"error": "No subcategories found"}), 404
    
    # Добавляем количество товаров для каждой подкатегории
    subcategories_data = [
        {
            'id': sub.id,
            'name': sub.name,
            'image': url_for('static', filename=f'uploads/sub_category/{sub.image}' if sub.image else 'img/default-placeholder.png'),
            'products': [{'id': prod.id, 'name': prod.name} for prod in sub.products]  # Добавляем список товаров
        }
        for sub in subcategories
    ]
    return jsonify(subcategories_data)



@app.route('/subcategory/<int:subcategory_id>/products')
def get_products(subcategory_id):
    subcategory = SubCategory.query.get_or_404(subcategory_id)
    category = subcategory.category
    products = subcategory.products
    products_data = [
        {
            'id': product.id,
            'name': product.name,
            'image': url_for('static', filename=f'uploads/product/{category.name}/{subcategory.name}/{product.image}'),
            'price': product.price,
            'article': product.article  # Добавляем артикул
        }
        for product in products
    ]
    return jsonify(products_data)




@app.route('/search')
def search():
    query = request.args.get('q', '').strip()

    # Логирование запроса
    print(f"Поисковый запрос: {query}")

    if not query:
        return jsonify({'categories': [], 'subcategories': [], 'products': []})

    normalized_query = f'%{query}%'

    def perform_search(model, fields, search_query):
        conditions = [field.ilike(search_query) for field in fields]
        query_result = model.query.filter(or_(*conditions))
        return query_result.all()

    def fuzzy_match(search_term, target_string):
        ratio = SequenceMatcher(None, search_term.lower(), target_string.lower()).ratio()
        return ratio > 0.7  # Порог похожести для учета орфографических ошибок

    # Поиск точных совпадений
    categories = perform_search(Category, [Category.name, Category.description], normalized_query)
    subcategories = perform_search(SubCategory, [SubCategory.name, SubCategory.description], normalized_query)
    products = perform_search(Product, [Product.name, Product.description, Product.article], normalized_query)

    # Если ничего не найдено, пробуем поиск с укороченным запросом
    if not categories and not subcategories and not products:
        shortened_query = f'%{query[1:]}%'
        categories = perform_search(Category, [Category.name, Category.description], shortened_query)
        subcategories = perform_search(SubCategory, [SubCategory.name, SubCategory.description], shortened_query)
        products = perform_search(Product, [Product.name, Product.description, Product.article], shortened_query)

        categories = [cat for cat in categories if query.lower() in cat.name.lower() or fuzzy_match(query, cat.name)]
        subcategories = [sub for sub in subcategories if query.lower() in sub.name.lower() or fuzzy_match(query, sub.name)]
        products = [prod for prod in products if query.lower() in prod.name.lower() or fuzzy_match(query, prod.name) or fuzzy_match(query, prod.article)]

    if not products:
        products = [prod for prod in Product.query.all() if fuzzy_match(query, prod.name) or fuzzy_match(query, prod.description) or fuzzy_match(query, prod.article)]

    # Логирование результатов
    print(f"Найдено категорий: {len(categories)}, подкатегорий: {len(subcategories)}, товаров: {len(products)}")

    # Убираем дубликаты из результатов
    categories = list({cat.id: cat for cat in categories}.values())
    subcategories = list({sub.id: sub for sub in subcategories}.values())
    products = list({prod.id: prod for prod in products}.values())

    # Обработка результатов и формирование ссылок
    results = {
        'categories': [
            {
                'name': cat.name,
                'url': f"/#subcategories/{cat.id}",  # Ссылка на подкатегории с ID
                'image': url_for('static', filename=f'uploads/category/{cat.image}'),
                'description': cat.description
            } for cat in categories
        ],
        'subcategories': [
            {
                'name': sub.name,
                'url': f"/#products/{sub.id}",  # Ссылка на продукты с ID подкатегории
                'image': url_for('static', filename=f'uploads/sub_category/{sub.image}' if sub.image else 'img/default-placeholder.png'),
                'description': sub.description
            } for sub in subcategories
        ],
        'products': [
            {
                'name': prod.name,
                'url': f"/catalog/product/{prod.id}",  # Ссылка на страницу товара с ID
                'image': url_for(
                    'static', 
                    filename=os.path.normpath(
                        os.path.join(
                            'uploads', 
                            'product', 
                            unquote(prod.subcategory.category.name), 
                            unquote(prod.subcategory.name), 
                            prod.image
                        )
                    ).replace("\\", "/")  # Заменяем обратные слэши на прямые
                ) if prod.image else 'img/default-placeholder.png',
                'description': prod.description,
                'price': prod.price,
                'article': prod.article
            } for prod in products
        ],
    }

    return jsonify(results)

# Маршрут для подтверждающего файла Яндекс
@app.route('/yandex_831cd0e373c1362b.html')
def yandex_verification():
    return send_from_directory(directory='templates', path='yandex_831cd0e373c1362b.html')

# Маршрут для robots.txt
@app.route('/robots.txt')
def robots_txt():
    robots_content = """
    User-agent: *
    Disallow: /admin
    Sitemap: https://somnium.kz/sitemap.xml
    """
    return Response(robots_content, mimetype='text/plain')
    
@app.route('/sitemap.xml')
def sitemap_xml():
    sitemap_content = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url>
            <loc>https://somnium.kz/</loc>
            <priority>1.0</priority>
        </url>
        <url>
            <loc>https://somnium.kz/benefits</loc>
            <priority>0.9</priority>
        </url>
        <url>
            <loc>https://somnium.kz/about</loc>
            <priority>0.8</priority>
        </url>
        <url>
            <loc>https://somnium.kz/computer_repair</loc>
            <priority>1.0</priority>
        </url>
        <url>
            <loc>https://somnium.kz/contact</loc>
            <priority>0.8</priority>
        </url>
        <url>
            <loc>https://somnium.kz/survey_home</loc>
            <priority>0.7</priority>
        </url>
        <url>
            <loc>https://somnium.kz/messenger</loc>
            <priority>0.8</priority>
        </url>
    </urlset>
    """
    return Response(sitemap_content, mimetype='application/xml')
    
    
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=7000, debug=True)