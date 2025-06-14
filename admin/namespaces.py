#admin/namespace
from flask_socketio import Namespace
from flask_login import current_user
from flask import request

class AdminNamespace(Namespace):
    def on_connect(self):
        print("Попытка подключения к пространству имен '/admin'")
        if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
            print("Отклонено подключение: неавторизованный или неадминистратор.")
            return False  # Отклоняем подключение
        print(f"Client connected to '/admin': {request.sid}")

    def on_disconnect(self):
        print(f"Client disconnected from '/admin': {request.sid}")
