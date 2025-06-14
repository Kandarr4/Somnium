# messenger/namespaces.py
from flask_socketio import Namespace, emit, join_room, leave_room
from flask import current_app, request, session
from flask_login import current_user
from models import db, User
# Импорт моделей мессенджера
import sys
import os
sys.path.append(os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
from model_messenger import Chat, Message, ChatNotification
from datetime import datetime

class MessengerNamespace(Namespace):
    def on_connect(self):
        """Обработчик подключения клиента"""
        current_app.logger.info(f"Client connected to /messenger namespace: {request.sid}")
        
        # Если пользователь авторизован, добавляем его в личную комнату
        if current_user.is_authenticated:
            join_room(f"user_{current_user.id}")
            current_app.logger.info(f"User {current_user.id} joined room user_{current_user.id}")
            
            # Если пользователь - администратор, добавляем его в комнату администраторов
            if current_user.is_admin:
                join_room("admin_room")
                current_app.logger.info(f"Admin {current_user.id} joined admin_room")
        else:
            # Для гостя, комната на основе идентификатора сессии
            guest_id = session.get('guest_id')
            if guest_id:
                join_room(f"guest_{guest_id}")
                current_app.logger.info(f"Guest {guest_id} joined room guest_{guest_id}")
        
        # Отправляем подтверждение о подключении
        emit('connect_confirmed', {
            'status': 'connected',
            'sid': request.sid
        })
    
    def on_disconnect(self):
        """Обработчик отключения клиента"""
        current_app.logger.info(f"Client disconnected from /messenger namespace: {request.sid}")
        
        if current_user.is_authenticated:
            leave_room(f"user_{current_user.id}")
            if current_user.is_admin:
                leave_room("admin_room")
        else:
            guest_id = session.get('guest_id')
            if guest_id:
                leave_room(f"guest_{guest_id}")
    
    def on_join_room(self, data):
        """Присоединение к админской комнате"""
        room = data.get('room')
        if not room:
            return
            
        join_room(room)
        current_app.logger.info(f"Client {request.sid} joined room {room}")
        
        # Отправляем уведомление о присоединении
        emit('room_joined', {
            'room': room,
            'status': 'success'
        })
    
    def on_join_chat(self, data):
        """Присоединение к комнате чата"""
        chat_id = data.get('chat_id')
        if not chat_id:
            current_app.logger.error(f"join_chat called without chat_id")
            emit('error', {'message': 'No chat_id provided'})
            return
        
        # Явно преобразуем chat_id в целое число, если он пришел как строка
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            current_app.logger.error(f"Invalid chat_id format: {chat_id}")
            emit('error', {'message': 'Invalid chat_id format'})
            return
        
        # Логируем действие
        current_app.logger.info(f"Client {request.sid} attempting to join chat room chat_{chat_id}")
        
        # Проверка доступа к чату
        chat = Chat.query.get(chat_id)
        if not chat:
            current_app.logger.error(f"Chat with ID {chat_id} not found")
            emit('error', {'message': 'Chat not found'})
            return
        
        # Создаем имя комнаты
        room_name = f"chat_{chat_id}"
        
        # Только владелец чата или администратор может присоединиться
        has_access = False
        
        if current_user.is_authenticated:
            if chat.user_id == current_user.id or current_user.is_admin:
                has_access = True
                join_room(room_name)
                current_app.logger.info(f"User {current_user.id} joined chat room {room_name}")
        else:
            guest_id = session.get('guest_id')
            if guest_id and chat.guest_id == guest_id:
                has_access = True
                join_room(room_name)
                current_app.logger.info(f"Guest {guest_id} joined chat room {room_name}")
        
        # Если нет доступа для записи, все равно подключаем для чтения
        if not has_access:
            # Если нет доступа для записи, все равно подключаем для чтения
            join_room(room_name)
            current_app.logger.info(f"Client {request.sid} joined chat room {room_name} (read-only)")
            
        # Отметить сообщения как прочитанные, если есть доступ
        if has_access:
            try:
                if current_user.is_authenticated and current_user.is_admin:
                    # Администратор отмечает сообщения пользователей как прочитанные
                    unread_messages = Message.query.filter_by(
                        chat_id=chat_id,
                        is_read=False
                    ).filter(Message.sender_type.in_(['user', 'guest'])).all()
                    
                    for message in unread_messages:
                        message.is_read = True
                    
                    db.session.commit()
                    
                    # Отправляем подтверждение о прочтении
                    emit('messages_read', {
                        'chat_id': chat_id,
                        'reader_type': 'admin'
                    }, room=room_name)
                    
                    current_app.logger.info(f"Admin {current_user.id} marked {len(unread_messages)} messages as read in chat {chat_id}")
                
                elif current_user.is_authenticated and chat.user_id == current_user.id:
                    # Пользователь отмечает сообщения админа как прочитанные
                    unread_messages = Message.query.filter_by(
                        chat_id=chat_id,
                        sender_type='admin',
                        is_read=False
                    ).all()
                    
                    for message in unread_messages:
                        message.is_read = True
                    
                    db.session.commit()
                    
                    # Отправляем подтверждение о прочтении
                    emit('messages_read', {
                        'chat_id': chat_id,
                        'reader_type': 'user'
                    }, room=room_name)
                    
                    current_app.logger.info(f"User {current_user.id} marked {len(unread_messages)} messages as read in chat {chat_id}")
                
                elif not current_user.is_authenticated and chat.guest_id == session.get('guest_id'):
                    # Гость отмечает сообщения админа как прочитанные
                    unread_messages = Message.query.filter_by(
                        chat_id=chat_id,
                        sender_type='admin',
                        is_read=False
                    ).all()
                    
                    for message in unread_messages:
                        message.is_read = True
                    
                    db.session.commit()
                    
                    # Отправляем подтверждение о прочтении
                    emit('messages_read', {
                        'chat_id': chat_id,
                        'reader_type': 'guest'
                    }, room=room_name)
                    
                    current_app.logger.info(f"Guest {session.get('guest_id')} marked {len(unread_messages)} messages as read in chat {chat_id}")
            
            except Exception as e:
                current_app.logger.error(f"Error marking messages as read: {e}")
        
        # Отправляем подтверждение о присоединении к комнате
        emit('joined_chat', {
            'chat_id': chat_id,
            'status': 'success'
        })
        
        # Отправляем событие о статусе подключения другим участникам чата
        emit('user_connected', {
            'chat_id': chat_id,
            'user_type': 'admin' if (current_user.is_authenticated and current_user.is_admin) else 
                        ('user' if current_user.is_authenticated else 'guest')
        }, room=room_name, include_self=False)
    
    def on_leave_chat(self, data):
        """Покидание комнаты чата"""
        chat_id = data.get('chat_id')
        if not chat_id:
            return
            
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            return
            
        room_name = f"chat_{chat_id}"
        leave_room(room_name)
        current_app.logger.info(f"Client {request.sid} left chat room {room_name}")
    
    def on_new_message(self, data):
        """Обработчик нового сообщения"""
        chat_id = data.get('chat_id')
        content = data.get('content')
        
        if not chat_id or not content:
            emit('error', {'message': 'Missing required fields'})
            return
        
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            emit('error', {'message': 'Invalid chat_id format'})
            return
        
        chat = Chat.query.get(chat_id)
        if not chat:
            emit('error', {'message': 'Chat not found'})
            return
        
        # Определяем тип отправителя
        if current_user.is_authenticated:
            if current_user.is_admin:
                sender_type = 'admin'
            else:
                sender_type = 'user'
            sender_id = current_user.id
        else:
            sender_type = 'guest'
            sender_id = None
            
        # Проверяем доступ к чату
        if sender_type == 'user' and chat.user_id != current_user.id:
            emit('error', {'message': 'Access denied'})
            return
        elif sender_type == 'guest':
            guest_id = session.get('guest_id')
            if not guest_id or chat.guest_id != guest_id:
                emit('error', {'message': 'Access denied'})
                return
        
        # Проверяем статус чата
        if chat.status in ['resolved', 'closed'] and sender_type not in ['admin', 'system']:
            emit('error', {'message': 'Chat is closed. Please create a new chat.', 'code': 'chat_closed'})
            return
        
        # Создаем сообщение
        message = Message(
            chat_id=chat_id,
            sender_type=sender_type,
            sender_id=sender_id,
            content=content,
            created_at=datetime.utcnow()
        )
        
        db.session.add(message)
        
        # Обновляем статус чата
        chat.status = 'pending' if sender_type in ['user', 'guest'] else 'open'
        chat.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        # Подготавливаем данные сообщения для отправки
        message_data = {
            'message_id': message.id,
            'chat_id': chat_id,
            'sender_type': sender_type,
            'sender_id': sender_id,
            'content': content,
            'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        # Имя комнаты для отправки сообщения
        room_name = f"chat_{chat_id}"
        
        # Отправляем уведомление в комнату чата ВСЕМ участникам
        emit('new_message', message_data, room=room_name)
        
        # Логируем отправку события
        current_app.logger.info(f"Sent new_message event to room {room_name}: sender={sender_type}, content={content[:50]}...")
        
        # Если сообщение от пользователя или гостя, отправляем уведомление администраторам
        if sender_type in ['user', 'guest']:
            # Создаем уведомления для всех администраторов
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
            
            # Отправляем уведомление в комнату администраторов
            emit('new_chat_notification', {
                'chat_id': chat_id,
                'message_id': message.id,
                'sender_type': sender_type,
                'content': content,
                'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
            }, room="admin_room")
            
            current_app.logger.info(f"Sent notification to admin_room for chat {chat_id}")
        
        # Если сообщение от администратора, отправляем уведомление пользователю или гостю
        elif sender_type == 'admin':
            if chat.user_id:
                emit('new_chat_notification', {
                    'chat_id': chat_id,
                    'message_id': message.id,
                    'content': content,
                    'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                }, room=f"user_{chat.user_id}")
                
                current_app.logger.info(f"Sent notification to user_{chat.user_id} for chat {chat_id}")
                
            elif chat.guest_id:
                emit('new_chat_notification', {
                    'chat_id': chat_id,
                    'message_id': message.id,
                    'content': content,
                    'created_at': message.created_at.strftime('%Y-%m-%d %H:%M:%S')
                }, room=f"guest_{chat.guest_id}")
                
                current_app.logger.info(f"Sent notification to guest_{chat.guest_id} for chat {chat_id}")
    
    def on_read_messages(self, data):
        """Отметка сообщений как прочитанных"""
        chat_id = data.get('chat_id')
        if not chat_id:
            return
            
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            return
            
        chat = Chat.query.get(chat_id)
        if not chat:
            return
            
        # Проверяем доступ к чату
        if current_user.is_authenticated:
            if current_user.is_admin:
                # Администратор отмечает сообщения пользователя/гостя как прочитанные
                messages = Message.query.filter_by(
                    chat_id=chat_id,
                    is_read=False
                ).filter(
                    Message.sender_type.in_(['user', 'guest'])
                ).all()
            elif chat.user_id == current_user.id:
                # Пользователь отмечает сообщения администратора как прочитанные
                messages = Message.query.filter_by(
                    chat_id=chat_id,
                    sender_type='admin',
                    is_read=False
                ).all()
            else:
                return
        else:
            guest_id = session.get('guest_id')
            if guest_id and chat.guest_id == guest_id:
                # Гость отмечает сообщения администратора как прочитанные
                messages = Message.query.filter_by(
                    chat_id=chat_id,
                    sender_type='admin',
                    is_read=False
                ).all()
            else:
                return
                
        for message in messages:
            message.is_read = True
            
        db.session.commit()
        
        # Отправляем подтверждение прочтения
        room_name = f"chat_{chat_id}"
        emit('messages_read', {
            'chat_id': chat_id,
            'reader_type': 'admin' if current_user.is_authenticated and current_user.is_admin else ('user' if current_user.is_authenticated else 'guest')
        }, room=room_name)
    
    def on_typing(self, data):
        """Оповещение о наборе текста"""
        chat_id = data.get('chat_id')
        if not chat_id:
            return
            
        try:
            chat_id = int(chat_id)
        except (ValueError, TypeError):
            return
            
        chat = Chat.query.get(chat_id)
        if not chat:
            return
            
        # Определяем тип отправителя
        if current_user.is_authenticated:
            if current_user.is_admin:
                sender_type = 'admin'
            else:
                sender_type = 'user'
        else:
            sender_type = 'guest'
            
        # Проверяем доступ к чату
        if sender_type == 'user' and chat.user_id != current_user.id:
            return
        elif sender_type == 'guest':
            guest_id = session.get('guest_id')
            if not guest_id or chat.guest_id != guest_id:
                return
                
        # Отправляем событие typing в комнату чата
        room_name = f"chat_{chat_id}"
        emit('typing', {
            'chat_id': chat_id,
            'sender_type': sender_type
        }, room=room_name, include_self=False)
        
        current_app.logger.info(f"Sent typing event to room {room_name}, sender={sender_type}")