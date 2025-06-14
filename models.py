#models.py
import uuid  # Добавьте этот импорт

from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from datetime import datetime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, ForeignKey, DateTime,
    UniqueConstraint, Index, JSON, event
)
from sqlalchemy.orm import relationship, backref
from datetime import datetime, timedelta
from sqlalchemy.sql import func
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy import JSON

db = SQLAlchemy()
SurveyBase = declarative_base()


user_services = db.Table('user_services',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('service_id', db.Integer, db.ForeignKey('services.id'), primary_key=True)
)

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False)
    internal_email = db.Column(db.String(150), unique=True, nullable=True)  # Поле для внутренней почты
    phone = db.Column(db.String(20), nullable=True)
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    middle_name = db.Column(db.String(100), nullable=True)
    avatar = db.Column(db.String(300), nullable=True)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(50), nullable=False, default='user')
    registered_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Связь с сервисами
    services = db.relationship('Service', secondary=user_services, backref=db.backref('users', lazy='dynamic'))
    
    # Связь с письмами
    mails = db.relationship('Mail', backref='user', lazy=True)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == 'admin'

class Mail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(255), nullable=True)
    body = db.Column(db.Text, nullable=True)
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    recipient = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    message_id = db.Column(db.String(255), unique=True, nullable=True)
    
    status = db.Column(db.String(50), nullable=False, default='Не прочитанно')
    
    thread_id = db.Column(db.Integer, db.ForeignKey('mail.id'), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('mail.id'), nullable=True)
    
    direction = db.Column(db.String(10), nullable=False, default='incoming')  # Новое поле
    
    # Отношение parent с каскадным удалением
    parent = db.relationship(
        'Mail',
        remote_side=[id],
        backref=backref('children', cascade='all, delete-orphan'),
        foreign_keys=[parent_id]
    )
    
    attachments = db.relationship('Attachment', backref='mail', cascade='all, delete-orphan', lazy=True)

class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mail_id = db.Column(db.Integer, db.ForeignKey('mail.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(1024), nullable=False)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    image = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)  # Добавлено поле description
    subcategories = db.relationship('SubCategory', back_populates='category', lazy=True)

class SubCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    image = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)  # Добавлено поле description
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    category = db.relationship('Category', back_populates='subcategories')
    products = db.relationship('Product', back_populates='subcategory', lazy=True)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subcategory_id = db.Column(db.Integer, db.ForeignKey('sub_category.id'))
    name = db.Column(db.String(255), nullable=False)
    price = db.Column(db.Float, nullable=False)  # New price column
    article = db.Column(db.String(255), nullable=False)
    image = db.Column(db.String(255), nullable=True)
    specs_file = db.Column(db.String(255), nullable=True)
    elements = db.Column(db.String(512), nullable=True)  # Новый столбец для элементов
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    subcategory = db.relationship('SubCategory', back_populates='products')
    specifications = db.relationship('Specification', back_populates='product', lazy=True)

class Specification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', back_populates='specifications')

class Elements(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    html_file = db.Column(db.String(255), nullable=False)
    
class Tariff(db.Model):
    __tablename__ = 'tariffs'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)  # Название тарифа
    description = db.Column(db.String(255), nullable=True)  # Описание тарифа
    price = db.Column(db.Float, nullable=False)  # Цена тарифа
    duration_days = db.Column(db.Integer, nullable=False)  # Длительность в днях
    is_free = db.Column(db.Boolean, default=False, nullable=False)  # Бесплатный тариф или нет
    features = db.Column(JSON, nullable=False)  # JSON-поле для хранения функций тарифа

    service_tariffs = db.relationship('ServiceTariff', backref='tariff', lazy=True)
    user_tariffs = db.relationship('UserTariff', backref='tariff', lazy=True)

    def __repr__(self):
        return f'<Tariff {self.name}>'

    def get_counters(self):
        """
        Возвращает словарь счетчиков из features, инициализированных на 0.
        """
        counters = {}
        counters_features = self.features.get('counters', {})
        for key, value in counters_features.items():
            if value is not None:
                counters[key] = 0
            else:
                counters[key] = None  # Или другой подходящий начальный статус
        return counters
        
class ServiceTariff(db.Model):
    __tablename__ = 'service_tariffs'
    id = db.Column(db.Integer, primary_key=True)
    tariff_id = db.Column(db.Integer, db.ForeignKey('tariffs.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    
    max_elements = db.Column(db.Integer, nullable=True)  # Максимальное количество элементов
    # Добавьте другие ограничения по необходимости

    def __repr__(self):
        return f'<ServiceTariff Tariff:{self.tariff_id} Service:{self.service_id}>'

class UserTariff(db.Model):
    __tablename__ = 'user_tariffs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    tariff_id = db.Column(db.Integer, db.ForeignKey('tariffs.id'), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey('services.id'), nullable=False)
    start_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    counters = db.Column(MutableDict.as_mutable(JSON), nullable=False, default={})  # Используем MutableDict

    user = db.relationship('User', backref=db.backref('user_tariffs', lazy=True))
    service = db.relationship('Service', backref=db.backref('service_user_tariffs', lazy=True))  # Переименовали backref

    def __repr__(self):
        return f'<UserTariff User:{self.user_id} Tariff:{self.tariff_id}>'

    def is_active(self):
        return self.start_date <= datetime.utcnow() <= self.end_date


def assign_tariff_to_user(user_id, tariff_id, service_id):
    """
    Назначает тариф пользователю и инициализирует счетчики на основе Tariff.features.
    """
    user = User.query.get(user_id)
    tariff = Tariff.query.get(tariff_id)
    service = Service.query.get(service_id)

    if not user:
        print(f"Пользователь с ID {user_id} не найден.")
        return None

    if not tariff:
        print(f"Тариф с ID {tariff_id} не найден.")
        return None

    if not service:
        print(f"Сервис с ID {service_id} не найден.")
        return None

    # Определение даты окончания тарифа
    if tariff.duration_days and tariff.duration_days > 0:
        end_date = datetime.utcnow() + timedelta(days=tariff.duration_days)
    else:
        end_date = datetime.max  # Бессрочно

    # Инициализация счетчиков на основе features
    counters = tariff.get_counters()

    # Создание записи UserTariff
    user_tariff = UserTariff(
        user_id=user.id,
        tariff_id=tariff.id,
        service_id=service.id,
        start_date=datetime.utcnow(),
        end_date=end_date,
        counters=counters
    )

    try:
        db.session.add(user_tariff)
        db.session.commit()
        print(f"Тариф '{tariff.name}' успешно назначен пользователю '{user.username}'.")
        return user_tariff
    except Exception as e:
        db.session.rollback()
        print(f"Ошибка при назначении тарифа: {e}")
        return None

class Service(db.Model):
    __tablename__ = 'services'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    blueprint = db.Column(db.String(100), unique=True, nullable=False)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    service_tariffs = db.relationship('ServiceTariff', backref='service', lazy=True)

    def __repr__(self):
        return f'<Service {self.name}>'

class PaymentInfo(db.Model):
    __tablename__ = 'payment_info'

    id = db.Column(db.Integer, primary_key=True)  # Уникальный идентификатор записи
    user_id = db.Column(db.Integer, nullable=False)  # Идентификатор пользователя
    service_id = db.Column(db.Integer, nullable=False)  # Услуга, за которую был совершен платеж
    tariff_id = db.Column(db.Integer, nullable=False)  # Тариф
    payment_date = db.Column(db.DateTime, default=datetime.utcnow)  # Дата и время платежа
    payment_method = db.Column(db.String(50), nullable=False)  # Метод платежа
    transaction_number = db.Column(db.String(100), unique=True, nullable=False)  # Номер транзакции
    status = db.Column(db.String(50), default='pending')  # Статус платежа
    created_at = db.Column(db.DateTime, default=datetime.utcnow)  # Время создания записи
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)  # Время последнего обновления записи

    # Конструктор больше не нуждается в аргументе `payment_date`, так как оно имеет значение по умолчанию
    def __init__(self, user_id, service_id, tariff_id, payment_method, transaction_number, status='pending'):
        self.user_id = user_id
        self.service_id = service_id
        self.tariff_id = tariff_id
        self.payment_method = payment_method
        self.transaction_number = transaction_number
        self.status = status

    def __repr__(self):
        return f"<PaymentInfo {self.transaction_number} - User: {self.user_id}, Service: {self.service_id}, Tariff: {self.tariff_id}>"
    
class Survey(SurveyBase):
    __tablename__ = 'survey'
    
    id = Column(Integer, primary_key=True)
    title = Column(String(140), nullable=False)
    description = Column(Text, nullable=True)
    user_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Поле для хранения ID пользователей, имеющих доступ
    access_user_ids = Column(JSON, nullable=True)  # Хранит список ID пользователей

    # Новое поле для хранения типа опроса
    survey_type = Column(String(50), nullable=False)  # Возможные значения: тест, опрос, анонимный опрос, анонимный тест

    # Поле для отслеживания включен ли объект
    is_active = Column(Boolean, default=0, nullable=True)

    # Поле для хранения URL элемента
    url = Column(String(255), nullable=True)

    # Поле для хранения пароля
    password = Column(String(255), nullable=True)

    questions = relationship('Question', back_populates='survey', cascade="all, delete-orphan")
    participants = relationship('Participant', back_populates='survey', cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Survey(id={self.id}, title='{self.title}', type='{self.survey_type}', is_active={self.is_active}, url='{self.url}')>"



class Question(SurveyBase):
    __tablename__ = 'question'
    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=False)
    question_type = Column(String(20), nullable=False)
    survey_id = Column(Integer, ForeignKey('survey.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    survey = relationship('Survey', back_populates='questions')
    choices = relationship('Choice', back_populates='question', cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Question(id={self.id}, text='{self.text[:20]}...')>"

class Choice(SurveyBase):
    """
    Модель Choice представляет вариант ответа на вопрос в опросе или тесте.
    """
    __tablename__ = 'choice'
    
    id = Column(Integer, primary_key=True)  # Уникальный идентификатор варианта ответа
    text = Column(String(140), nullable=True)  # Текст варианта ответа
    is_correct = Column(Boolean, default=False)  # Указывает, является ли вариант ответа правильным (для тестов)
    answer_type = Column(String(50), nullable=False, default='text_field')  # Тип ответа: 'text_field' или 'choice'
    question_id = Column(Integer, ForeignKey('question.id'), nullable=False)  # Связь с вопросом
    participant_id = Column(Integer, ForeignKey('participant.id'), nullable=True)  # Связь с участником (может быть NULL)
    order = Column(Integer, nullable=False)  # Порядковый номер ответа
    created_at = Column(DateTime(timezone=True), server_default=func.now())  # Автоматическое заполнение времени создания

    # Связи с другими моделями
    question = relationship('Question', back_populates='choices')  # Обратная связь с моделью Question
    participant = relationship('Participant', back_populates='choices')  # Обратная связь с моделью Participant

    def __repr__(self):
        """
        Возвращает строковое представление объекта Choice.
        """
        return f"<Choice(id={self.id}, text='{self.text[:20]}...', type='{self.answer_type}', is_correct={self.is_correct}, order={self.order})>"

class Participant(SurveyBase):
    __tablename__ = 'participant'
    id = Column(Integer, primary_key=True)
    first_name = Column(String(140), nullable=True)
    last_name = Column(String(140), nullable=True)
    patronymic = Column(String(140), nullable=True)
    iin = Column(String(12), nullable=True, unique=True)
    phone_number = Column(String(20), nullable=True, unique=True)
    auth_type = Column(String(20), nullable=True)
    eds_data = Column(Text, nullable=True)
    manual_form_filled = Column(Boolean, default=True)
    
    # Новое поле session_id
    session_id = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=True, onupdate=datetime.utcnow)

    # Внешний ключ
    survey_id = Column(Integer, ForeignKey('survey.id', ondelete="CASCADE"), nullable=False)

    # Связь с Survey
    survey = relationship('Survey', back_populates='participants')

    # Добавляем связь с Choice
    choices = relationship('Choice', back_populates='participant', cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('iin', name='uq_participant_iin'),
        UniqueConstraint('phone_number', name='uq_participant_phone_number'),
    )

    def __repr__(self):
        return f"<Participant(id={self.id}, name='{self.first_name} {self.last_name}')>"
        
class Result(SurveyBase):
    __tablename__ = 'result'
    id = Column(Integer, primary_key=True)
    participant_id = Column(Integer, ForeignKey('participant.id'), nullable=False)
    survey_id = Column(Integer, ForeignKey('survey.id'), nullable=False)
    question_id = Column(Integer, ForeignKey('question.id'), nullable=False)
    choice_id = Column(Integer, ForeignKey('choice.id'), nullable=True)
    answer_text = Column(Text, nullable=True)  # Если это текстовый ответ
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Result(id={self.id}, participant_id={self.participant_id}, survey_id={self.survey_id})>"