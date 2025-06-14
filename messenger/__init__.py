# messenger/__init__.py
from flask import Blueprint



messenger_bp = Blueprint('messenger', __name__, url_prefix='/messenger')

from . import messenger  # Импорт представлений из messenger.py