from flask import Blueprint
import os

# Определите пути к папкам templates и static
template_folder = os.path.join(os.path.dirname(__file__), 'templates')
static_folder = os.path.join(os.path.dirname(__file__), 'static')

# Инициализируйте блюпринт с указанными папками
payment_bp = Blueprint(
    'payment',
    __name__,
    template_folder=template_folder,
    static_folder=static_folder
)

from .payment import payment_bp 
