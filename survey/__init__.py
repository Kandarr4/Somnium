#survey__init__.py
from flask import Blueprint
import os

# Определите пути к папкам templates и static
template_folder = os.path.join(os.path.dirname(__file__), 'templates')
static_folder = os.path.join(os.path.dirname(__file__), 'static')

# Инициализируйте блюпринт с указанными папками
survey_bp = Blueprint(
    'survey',
    __name__,
    template_folder=template_folder,
    static_folder=static_folder
)

from .survey import survey_bp  # Импортируйте блюпринт из survey.py
