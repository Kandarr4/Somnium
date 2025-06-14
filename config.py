# config.py
import os
from datetime import timedelta

class Config:
    # Секретный ключ для Flask
    SECRET_KEY = os.environ.get('SECRET_KEY') or '040100Somnium2'

    # Настройки основной базы данных
    SQLALCHEMY_DATABASE_URI = 'sqlite:///app.db'
    
    # Используем абсолютный путь для SECONDARY_DATABASE_URI
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SECONDARY_DATABASE_URI = f'sqlite:///{os.path.join(BASE_DIR, "instance", "survey.db")}'

    # Отключение отслеживания изменений в SQLAlchemy
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Настройки CSRF
    WTF_CSRF_HEADERS = ['X-CSRFToken']
    WTF_CSRF_METHODS = ['POST', 'PUT', 'DELETE', 'PATCH']
    WTF_CSRF_ENABLE = True
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_IN_COOKIES = False

    # ------------------------------
    # Настройки SMTP-сервера для отправки писем
    # ------------------------------
 
    DOMAIN = 'somnium.kz'  # Замените на ваш домен

 
    # Адрес SMTP-сервера
    SMTP_HOST = os.environ.get('SMTP_HOST') or 'adminm@somnium.kz'
    
    # Порт SMTP-сервера
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))  # Обычно 587 для TLS или 465 для SSL
    
    # Имя пользователя для аутентификации на SMTP-сервере
    SMTP_USERNAME = os.environ.get('SMTP_USERNAME') or 'admin'
    
    # Пароль для аутентификации на SMTP-сервере
    SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD') or '040100Somnium2'
    
    # Использовать ли TLS для соединения с SMTP-сервером
    SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'True').lower() in ['true', '1', 'yes']
    
    # API-токен для аутентификации запросов на отправку писем через HTTP API
    SEND_EMAIL_API_TOKEN = os.environ.get('SEND_EMAIL_API_TOKEN') or '040100Somnium2'
    
    # ------------------------------
    # Дополнительные настройки (при необходимости)
    # ------------------------------
    
    # Время жизни сессии пользователя
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
    
    # Другие ваши настройки...
