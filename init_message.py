#!/usr/bin/env python
"""
Скрипт для инициализации базы данных мессенджера.
Запустите этот скрипт один раз перед использованием мессенджера.
"""

import os
from flask import Flask
from models import db
from model_messenger import Chat, Message, MessageAttachment, ChatNotification
from config import Config
import datetime

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)

def init_db():
    with app.app_context():
        # Создаем таблицы
        print("Создание таблиц для мессенджера...")
        db.create_all()
        print("Таблицы созданы успешно!")
        
        # Проверяем, существуют ли уже тестовые данные
        if Chat.query.count() == 0:
            print("Создание тестовых данных...")
            
            # Создаем тестовый чат для администратора
            # Проверяем, какие поля у нас есть в модели Chat
            print("Доступные поля модели Chat:", [column.name for column in Chat.__table__.columns])
            
            # Создаем объект Chat только с параметрами, которые принимает конструктор
            admin_chat = Chat(
                title="Тестовый чат администратора"
            )
            
            # Устанавливаем дополнительные поля после создания объекта
            admin_chat.is_active = True
            admin_chat.status = 'open'
            admin_chat.created_at = datetime.datetime.now()
            admin_chat.updated_at = datetime.datetime.now()
            
            db.session.add(admin_chat)
            db.session.commit()
            
            # Добавляем тестовое сообщение
            admin_message = Message(
                chat_id=admin_chat.id,
                content="Это тестовое сообщение администратора. Система мессенджера готова к использованию!",
                sender_type="admin"
            )
            
            # Устанавливаем дополнительные поля, если они есть
            admin_message.created_at = datetime.datetime.now()
            
            db.session.add(admin_message)
            db.session.commit()
            
            print(f"Тестовый чат создан с ID: {admin_chat.id}")
            print("Тестовые данные созданы!")
        else:
            print("Тестовые данные уже существуют.")
        
        print("Инициализация базы данных мессенджера завершена!")

if __name__ == '__main__':
    init_db()