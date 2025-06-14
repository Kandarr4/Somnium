#/admin/init.py
from flask import Blueprint


# Настройка Blueprint с указанием папок для шаблонов и статических файлов
admin_bp = Blueprint('admin_bp', __name__,
                     template_folder='../templates/admin',
                     static_folder='../static',  # Указываем путь к статическим файлам относительно местоположения этого файла
                     static_url_path='/static')

from . import routes  # Импорт маршрутов
