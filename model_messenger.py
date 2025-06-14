# model_messenger.py
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey, DateTime, JSON
from sqlalchemy.orm import relationship
import uuid

# Используем тот же db объект, что и в основном приложении
from models import db

class Chat(db.Model):
    __tablename__ = 'chats'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)  # Может быть NULL для гостевых чатов
    guest_id = db.Column(db.String(36), nullable=True)  # UUID для гостевых чатов, убрали unique=True
    title = db.Column(db.String(100), nullable=True)  # Тема чата, может быть пустой
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # Активен ли чат
    
    # Статус чата: 'open' - открыт, 'closed' - закрыт, 'pending' - ожидает ответа администратора, 'resolved' - завершен с успехом
    status = db.Column(db.String(20), default='open', nullable=False)
    
    # Связь с пользователем (если авторизован)
    user = db.relationship('User', backref=db.backref('chats', lazy=True))
    
    # Связь с сообщениями
    messages = db.relationship('Message', backref='chat', lazy=True, cascade='all, delete-orphan')
    
    def __init__(self, user_id=None, title=None):
        self.user_id = user_id
        self.title = title
        
        # Если пользователь не авторизован, генерируем UUID для гостя
        if not user_id:
            self.guest_id = str(uuid.uuid4())
    
    def __repr__(self):
        if self.user_id:
            return f'<Chat {self.id}: User {self.user_id}>'
        else:
            return f'<Chat {self.id}: Guest {self.guest_id}>'

class Message(db.Model):
    __tablename__ = 'messages'
    
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    sender_type = db.Column(db.String(20), nullable=False)  # 'user', 'guest', 'admin'
    sender_id = db.Column(db.Integer, nullable=True)  # ID отправителя (если не гость)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    is_read = db.Column(db.Boolean, default=False)  # Прочитано ли сообщение
    
    # Связь с вложениями
    attachments = db.relationship('MessageAttachment', backref='message', lazy=True, cascade='all, delete-orphan')
    
    def __repr__(self):
        return f'<Message {self.id}: {self.sender_type} in Chat {self.chat_id}>'

class MessageAttachment(db.Model):
    __tablename__ = 'message_attachments'
    
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    filepath = db.Column(db.String(1024), nullable=False)
    file_type = db.Column(db.String(50), nullable=True)  # Тип файла (MIME type)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    def __repr__(self):
        return f'<Attachment {self.id}: {self.filename}>'

class ChatNotification(db.Model):
    __tablename__ = 'chat_notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)  # ID пользователя (админа), получающего уведомление
    chat_id = db.Column(db.Integer, db.ForeignKey('chats.id'), nullable=False)
    message_id = db.Column(db.Integer, db.ForeignKey('messages.id'), nullable=True)  # Может быть NULL для общих уведомлений о чате
    notification_type = db.Column(db.String(50), nullable=False)  # Тип уведомления: 'new_message', 'new_chat', и т.д.
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    
    # Связи
    user = db.relationship('User')
    chat = db.relationship('Chat')
    message = db.relationship('Message')
    
    def __repr__(self):
        return f'<Notification {self.id}: {self.notification_type} for User {self.user_id}>'