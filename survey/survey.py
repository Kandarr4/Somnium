# survey.py
import logging
from datetime import datetime, timedelta
import os
from flask import Blueprint, request, jsonify, current_app, render_template, make_response, g, session
from sqlalchemy.orm import sessionmaker, aliased
from models import db, Survey, Question, Choice, Participant, Tariff, User, Service, ServiceTariff, UserTariff, Result
from decorators import service_required, tariff_required
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
import json
from forms import LoginForm, RegisterForm  # Импортируйте LoginForm
from markupsafe import escape
from sqlalchemy.sql import func
from sqlalchemy import func, and_
import string
import random
import re  # модуль регулярных выражений

survey_bp = Blueprint('survey_bp', __name__, url_prefix='/survey')

def generate_unique_url():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=10))

# Функция для получения сессии для вторичной базы данных
def get_survey_session():
    survey_engine = current_app.config['SURVEY_ENGINE']
    # Получаем абсолютный путь к базе данных
    db_path = survey_engine.url.database
    absolute_db_path = os.path.abspath(db_path)
    SurveySession = sessionmaker(bind=survey_engine)
    return SurveySession()

@survey_bp.route('/', methods=['GET'])
def survey_home():
    login_form = LoginForm()
    register_form = RegisterForm()
    tariffs = Tariff.query.all()
    
    is_authenticated = current_user.is_authenticated

    user_tariff = None
    user_tariff_tariff = None
    surveys = []
    total_answers = 0
    total_surveys = 0
    total_questions = 0
    days_until_next_update = 0
    max_days = 0
    activation_date = None  # Добавлено
    survey_data = []

    if is_authenticated:
        service_id_survey = 1  # Убедитесь, что это правильный service_id для 'survey_bp'
        user_tariff = UserTariff.query.filter(
            UserTariff.user_id == current_user.id,
            UserTariff.service_id == service_id_survey,
            UserTariff.start_date <= datetime.utcnow(),
            UserTariff.end_date >= datetime.utcnow()
        ).first()

        if user_tariff:
            user_tariff_tariff = Tariff.query.get(user_tariff.tariff_id)
            user_tariff.is_forever = user_tariff.end_date == datetime.max

            # Дата активации тарифа
            activation_date = user_tariff.start_date.strftime('%d.%m.%Y')

            # Получаем опросы пользователя
            survey_session = g.survey_session
            surveys = survey_session.query(Survey).filter_by(user_id=current_user.id).all()

            for survey in surveys:
                # Подсчет количества ответов через связь Question -> Choice
                total_choices = (
                    survey_session.query(func.count(Choice.id))
                    .join(Question, Question.id == Choice.question_id)
                    .filter(Question.survey_id == survey.id)
                    .scalar()
                )
                survey_data.append({
                    "id": survey.id,
                    "title": survey.title,
                    "description": survey.description,
                    "created_at": survey.created_at.strftime('%d.%m.%Y %H:%M'),
                    "updated_at": survey.updated_at.strftime('%d.%m.%Y %H:%M'),
                    "survey_type": survey.survey_type,
                    "questions_count": len(survey.questions),
                    "total_choices": total_choices,
                    "url": survey.url,
                    "password": survey.password,
                    "is_active": survey.is_active
                })

            # Общее количество опросов
            total_surveys = len(survey_data)

            # Общее количество вопросов
            total_questions = sum(survey['questions_count'] for survey in survey_data)

            # Общий подсчет ответов по всем опросам
            total_answers = sum(
                survey_session.query(func.count(Choice.id))
                .join(Question, Question.id == Choice.question_id)
                .filter(Question.survey_id == survey.id)
                .scalar()
                for survey in surveys
            )

            # Рассчитываем дни до окончания тарифа
            if user_tariff.is_forever:
                days_until_next_update = 'Бессрочно'
            else:
                delta = user_tariff.end_date.date() - datetime.utcnow().date()
                days_until_next_update = max(delta.days + 1, 0)  # Добавляем 1 день и не допускаем отрицательных значений
            max_days = 0  # Можно оставить как есть или удалить, если не используется

    # Определяем, нужно ли показывать тур
    show_tour = is_authenticated and (user_tariff is None)
    show_tariff_tour = is_authenticated and (user_tariff is not None)

    return render_template(
        'survey_home.html',
        login_form=login_form,
        register_form=register_form,
        tariffs=tariffs,
        is_authenticated=is_authenticated,
        user_tariff=user_tariff,
        user_tariff_tariff=user_tariff_tariff,
        surveys=survey_data,
        total_answers=total_answers,
        total_surveys=total_surveys,
        total_questions=total_questions,
        days_until_next_update=days_until_next_update,
        max_days=max_days,
        activation_date=activation_date,  # Передаём дату активации
        datetime=datetime,
        show_tour=show_tour,               # Передаём переменную show_tour
        show_tariff_tour=show_tariff_tour  # Передаём переменную show_tariff_tour
    )

@survey_bp.route('/create', methods=['POST'])
@login_required
def create_survey():
    data = request.get_json()
    title = data.get('title')
    description = data.get('description')
    survey_type = data.get('survey_type')  # Получаем тип опроса из данных запроса
    access_user_ids = data.get('access_user_ids', [])

    if not title:
        return jsonify({"success": False, "message": "Название опроса обязательно."}), 400

    # Проверка корректности значения survey_type
    valid_survey_types = {'anon_test', 'anon_survey', 'id_test', 'id_survey'}
    if survey_type not in valid_survey_types:
        return jsonify({"success": False, "message": "Неверный тип опроса."}), 400

    try:
        # Проверяем наличие активного тарифа для пользователя и сервиса 'survey_bp'
        user_tariff = UserTariff.query.join(Service).filter(
            UserTariff.user_id == current_user.id,
            Service.blueprint == 'survey_bp',
            UserTariff.start_date <= datetime.utcnow(),
            UserTariff.end_date >= datetime.utcnow()
        ).first()

        if not user_tariff:
            current_app.logger.error(f"No active UserTariff found for user {current_user.id} and service 'survey_bp'")
            return jsonify({"success": False, "message": "Не найден активный тарифный план."}), 500

    except SQLAlchemyError as e:
        current_app.logger.error(f"Ошибка при проверке тарифного плана: {e}")
        return jsonify({"success": False, "message": "Ошибка при проверке тарифного плана."}), 500

    # Создаём новый объект Survey
    new_survey = Survey(
        title=title,
        description=description,
        user_id=current_user.id,
        access_user_ids=json.dumps(access_user_ids) if isinstance(access_user_ids, list) else access_user_ids,
        survey_type=survey_type,  # Устанавливаем тип опроса
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )

    try:
        # Добавляем и коммитим опрос во вторичной базе данных (survey.db)
        g.survey_session.add(new_survey)
        g.survey_session.commit()
        current_app.logger.info(f"Survey created with ID: {new_survey.id}")
    except SQLAlchemyError as e:
        # Откатываем транзакцию во вторичной базе данных в случае ошибки
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при создании опроса: {e}")
        return jsonify({"success": False, "message": "Ошибка при создании опроса."}), 500

    return jsonify({
        "success": True,
        "message": "Опрос успешно создан.",
        "survey": {
            "id": new_survey.id,
            "title": new_survey.title
        }
    }), 201


@survey_bp.route('/toggle_active/<int:survey_id>', methods=['POST'])
@login_required
def toggle_active(survey_id):
    # Используем сессию survey_session для доступа к модели Survey
    survey = g.survey_session.query(Survey).filter_by(id=survey_id, user_id=current_user.id).first()
    if not survey:
        return jsonify({"success": False, "message": "Опрос не найден."}), 404

    survey.is_active = not survey.is_active
    try:
        g.survey_session.commit()
        return jsonify({
            "success": True,
            "message": f"Опрос {'включен' if survey.is_active else 'выключен'}.",
            "is_active": survey.is_active
        }), 200
    except SQLAlchemyError as e:
        g.survey_session.rollback()
        return jsonify({"success": False, "message": "Ошибка при обновлении состояния опроса."}), 500

@survey_bp.route('/questions/<int:question_id>/toggle-multiple', methods=['PUT'])
@login_required
def toggle_multiple(question_id):
    """
    Тогглит значение 'multiple' в поле question_type для указанного вопроса.
    """
    # Получаем вопрос с проверкой принадлежности текущему пользователю через связь Survey -> Question
    question = (
        g.survey_session.query(Question)
        .join(Survey)  # Присоединяем таблицу Survey
        .filter(Question.id == question_id, Survey.user_id == current_user.id)  # Проверяем user_id через Survey
        .first()
    )
    
    if not question:
        return jsonify({"success": False, "message": "Вопрос не найден."}), 404

    # Тогглим значение question_type между 'multiple' и 'single'
    question.question_type = 'multiple' if question.question_type != 'multiple' else 'single'
    try:
        g.survey_session.commit()
        return jsonify({
            "success": True,
            "message": f"Тип вопроса изменен на {'множественный' if question.question_type == 'multiple' else 'одиночный'}.",
            "question_type": question.question_type
        }), 200
    except SQLAlchemyError as e:
        g.survey_session.rollback()
        return jsonify({"success": False, "message": "Ошибка при обновлении типа вопроса."}), 500



@survey_bp.route('/create_link/<int:survey_id>', methods=['POST'])
@login_required
def create_link(survey_id):
    data = request.get_json()
    action = data.get('action', 'create')  # По умолчанию 'create'

    try:
        # Используем g.survey_session для выборки и сохранения
        survey = g.survey_session.query(Survey).filter_by(id=survey_id, user_id=current_user.id).first()
        if not survey:
            return jsonify({"success": False, "message": "Опрос не найден."}), 404

        if action == 'create':
            if not survey.url:
                survey.url = generate_unique_url()
                survey.is_active = True  # Устанавливаем is_active в 1
                message = "Ссылка создана."
            else:
                # Возвращаем существующую ссылку
                return jsonify({
                    "success": True,
                    "message": "Ссылка уже существует.",
                    "url": survey.url
                }), 200
        elif action == 'regenerate':
            survey.url = generate_unique_url()
            survey.is_active = True  # Устанавливаем is_active в 1
            message = "Ссылка перегенерирована."
        else:
            return jsonify({"success": False, "message": "Некорректное действие."}), 400

        # Сохраняем изменения
        g.survey_session.commit()

        return jsonify({
            "success": True,
            "message": message,
            "url": survey.url
        }), 200
    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при создании ссылки: {e}")
        return jsonify({"success": False, "message": "Ошибка при создании ссылки."}), 500


@survey_bp.route('/add_password/<int:survey_id>', methods=['POST'])
@login_required
def add_password(survey_id):
    data = request.get_json()
    password = data.get('password')

    if not password:
        return jsonify({"success": False, "message": "Пароль не может быть пустым."}), 400

    # Используем session из вторичной базы данных
    survey = g.survey_session.query(Survey).filter_by(id=survey_id, user_id=current_user.id).first()
    if not survey:
        return jsonify({"success": False, "message": "Опрос не найден."}), 404

    survey.password = password
    try:
        g.survey_session.commit()  # Коммитим изменения через survey_session
        return jsonify({
            "success": True,
            "message": "Пароль успешно добавлен."
        }), 200
    except SQLAlchemyError as e:
        g.survey_session.rollback()  # Откатываем изменения в случае ошибки
        return jsonify({"success": False, "message": "Ошибка при добавлении пароля."}), 500

 
@survey_bp.route('/<int:survey_id>/questions', methods=['POST'])
@login_required
def add_question(survey_id):
    data = request.get_json()
    text = data.get('text')
    question_type = 'single'  # Устанавливаем тип по умолчанию
    is_correct = data.get('is_correct', False)

    if not text:
        return jsonify({"success": False, "message": "Текст вопроса обязателен."}), 400

    try:
        # Проверяем, что опрос принадлежит текущему пользователю
        survey = g.survey_session.query(Survey).filter_by(id=survey_id, user_id=current_user.id).first()
        if not survey:
            return jsonify({"success": False, "message": "Опрос не найден или у вас нет к нему доступа."}), 404

        # Создаём новый вопрос
        question = Question(
            text=text,
            question_type=question_type,  # Используем тип по умолчанию
            is_correct=is_correct,
            survey_id=survey_id,
            created_at=datetime.utcnow()
        )

        g.survey_session.add(question)
        g.survey_session.commit()
        current_app.logger.info(f"Вопрос добавлен в опрос с ID {survey_id}: {question.text}")
        return jsonify({
            "success": True,
            "message": "Вопрос успешно добавлен.",
            "question": {
                "id": question.id,
                "text": question.text,
                "question_type": question.question_type,  # Возвращаем тип вопроса
                "created_at": question.created_at.strftime('%d.%m.%Y %H:%M')
            }
        }), 201
    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при добавлении вопроса: {e}")
        return jsonify({"success": False, "message": "Ошибка при добавлении вопроса"}), 500

        
@survey_bp.route('/<int:survey_id>/questions/<int:question_id>/answers', methods=['POST'])
@login_required
def add_answer(survey_id, question_id):
    """
    Добавление ответа к вопросу в опросе.
    """
    data = request.get_json()
    text = data.get('text')
    is_correct = data.get('is_correct', False)
    answer_type = data.get('answer_type', 'choice')  # По умолчанию 'choice'

    # Если тип ответа "text_field", добавляем текст "текстовое поле"
    if answer_type == 'text_field' and not text:
        text = "текстовое поле"

    try:
        # Проверяем, что опрос принадлежит текущему пользователю
        survey = g.survey_session.query(Survey).filter_by(id=survey_id, user_id=current_user.id).first()
        if not survey:
            return jsonify({"success": False, "message": "Опрос не найден или у вас нет к нему доступа."}), 404

        # Проверяем, что вопрос принадлежит опросу
        question = g.survey_session.query(Question).filter_by(id=question_id, survey_id=survey_id).first()
        if not question:
            return jsonify({"success": False, "message": "Вопрос не найден или не принадлежит этому опросу."}), 404

        # Создаём новый ответ
        max_order = g.survey_session.query(func.max(Choice.order)).filter_by(question_id=question_id).scalar()
        new_order = (max_order or 0) + 1

        new_choice = Choice(
            text=text,
            is_correct=is_correct,
            answer_type=answer_type,
            question_id=question_id,
            order=new_order
        )

        g.survey_session.add(new_choice)
        g.survey_session.commit()

        current_app.logger.info(f"Ответ добавлен в вопрос с ID {question_id}: {new_choice.text}")
        return jsonify({
            "success": True,
            "message": "Ответ успешно добавлен.",
            "choice": {
                "id": new_choice.id,
                "text": new_choice.text,
                "is_correct": new_choice.is_correct,
                "order": new_choice.order
            }
        }), 201

    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при добавлении ответа: {e}")
        return jsonify({"success": False, "message": "Ошибка при добавлении ответа."}), 500

@survey_bp.route('/choices/<int:choice_id>/swap-order/<int:adjacent_choice_id>', methods=['POST'])
@login_required
@service_required('survey_bp')
def swap_order(choice_id, adjacent_choice_id):
    """
    Меняет местами порядок двух ответов.
    """
    try:
        # Получаем ответы
        choice = g.survey_session.query(Choice).get(choice_id)
        adjacent_choice = g.survey_session.query(Choice).get(adjacent_choice_id)

        if not choice or not adjacent_choice:
            return jsonify({"success": False, "message": "Один из ответов не найден."}), 404

        # Проверяем, что оба ответа принадлежат одному вопросу
        if choice.question_id != adjacent_choice.question_id:
            return jsonify({"success": False, "message": "Ответы принадлежат разным вопросам."}), 400

        # Меняем местами порядок
        choice.order, adjacent_choice.order = adjacent_choice.order, choice.order

        # Фиксируем изменения
        g.survey_session.commit()

        return jsonify({"success": True, "message": "Порядок ответов успешно обновлен."}), 200

    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при обновлении порядка ответов: {e}")
        return jsonify({"success": False, "message": "Ошибка при обновлении порядка ответов."}), 500


@survey_bp.route('/delete_password/<int:survey_id>', methods=['POST'])
@login_required
def delete_password(survey_id):
    survey = g.survey_session.query(Survey).filter_by(id=survey_id, user_id=current_user.id).first()
    if not survey:
        return jsonify({"success": False, "message": "Опрос не найден."}), 404

    survey.password = None
    try:
        g.survey_session.commit()
        return jsonify({
            "success": True,
            "message": "Пароль успешно удалён."
        }), 200
    except SQLAlchemyError as e:
        g.survey_session.rollback()
        return jsonify({"success": False, "message": "Ошибка при удалении пароля."}), 500


@survey_bp.route('/choices/<int:choice_id>/delete', methods=['DELETE'])
@login_required
@service_required('survey_bp')
def delete_choice(choice_id):
    """
    Удаление ответа по ID.
    """
    try:
        choice = g.survey_session.query(Choice).get(choice_id)
        if not choice:
            return jsonify({"success": False, "message": "Ответ не найден."}), 404

        # Получаем вопрос, к которому принадлежит ответ
        question = choice.question
        if not question:
            return jsonify({"success": False, "message": "Вопрос не найден для этого ответа."}), 404

        # Проверяем, что текущий пользователь владеет опросом
        survey = question.survey
        if not survey or survey.user_id != current_user.id:
            return jsonify({"success": False, "message": "У вас нет прав на удаление этого ответа."}), 403

        # Удаляем ответ
        g.survey_session.delete(choice)
        g.survey_session.commit()

        # Обновляем порядок остальных ответов
        remaining_choices = g.survey_session.query(Choice).filter_by(question_id=question.id).order_by(Choice.order).all()
        for idx, remaining_choice in enumerate(remaining_choices, start=1):
            if remaining_choice.order != idx:
                remaining_choice.order = idx
        g.survey_session.commit()

        return jsonify({"success": True, "message": "Ответ успешно удален."}), 200

    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при удалении ответа: {e}")
        return jsonify({"success": False, "message": "Ошибка при удалении ответа."}), 500


@survey_bp.route('/<int:survey_id>', methods=['DELETE'])
@service_required('survey_bp')
def delete_survey(survey_id):
    survey_session = get_survey_session()  # Используем вторичную сессию
    survey = survey_session.query(Survey).get(survey_id)
    if not survey:
        survey_session.close()
        return jsonify({"success": False, "message": "Опрос не найден."}), 404
    
    try:
        # Удаляем связанные результаты
        results = survey_session.query(Result).filter_by(survey_id=survey_id).all()
        for result in results:
            survey_session.delete(result)

        # Удаляем связанные варианты ответов (Choice)
        choices = survey_session.query(Choice).join(Question).filter(Question.survey_id == survey_id).all()
        for choice in choices:
            survey_session.delete(choice)

        # Удаляем связанные вопросы
        questions = survey_session.query(Question).filter_by(survey_id=survey_id).all()
        for question in questions:
            survey_session.delete(question)

        # Удаляем сам опрос
        survey_session.delete(survey)

        survey_session.commit()
        return jsonify({"success": True, "message": "Опрос и все связанные данные успешно удалены."}), 200
    except SQLAlchemyError as e:
        survey_session.rollback()
        current_app.logger.error(f"Ошибка при удалении опроса: {e}")
        return jsonify({"success": False, "message": "Ошибка при удалении опроса."}), 500
    finally:
        survey_session.close()


@survey_bp.route('/questions/<int:question_id>', methods=['DELETE'])
@login_required
@service_required('survey_bp')
def delete_question(question_id):
    """
    Удаление вопроса по ID. Удаляются также связанные варианты ответа.
    """
    survey_session = g.survey_session  # Используем вторичную сессию
    try:
        # Ищем вопрос по ID
        question = survey_session.query(Question).get(question_id)
        if not question:
            return jsonify({"success": False, "message": "Вопрос не найден."}), 404

        # Проверяем, что текущий пользователь является владельцем опроса
        survey = survey_session.query(Survey).get(question.survey_id)
        if not survey or survey.user_id != current_user.id:
            return jsonify({"success": False, "message": "У вас нет прав на удаление этого вопроса."}), 403

        # Удаляем вопрос (с удалением каскадно связанных объектов)
        survey_session.delete(question)
        survey_session.commit()

        return jsonify({"success": True, "message": "Вопрос успешно удален."}), 200

    except SQLAlchemyError as e:
        # В случае ошибки откатываем транзакцию
        survey_session.rollback()
        current_app.logger.error(f"Ошибка при удалении вопроса: {e}")
        return jsonify({"success": False, "message": "Ошибка при удалении вопроса."}), 500

    finally:
        survey_session.close()

@survey_bp.route('/survey/<int:survey_id>/answers/<int:choice_id>', methods=['DELETE'])
@login_required
@service_required('survey_bp')
def delete_answer(survey_id, choice_id):
    """
    Удаление ответа по ID.
    """
    survey_session = g.survey_session
    try:
        # Получаем опрос
        survey = survey_session.query(Survey).get(survey_id)
        if not survey:
            return jsonify({"success": False, "message": "Опрос не найден."}), 404

        # Получаем ответ
        choice = survey_session.query(Choice).get(choice_id)
        if not choice:
            return jsonify({"success": False, "message": "Ответ не найден."}), 404

        # Проверяем, что ответ принадлежит опросу
        if choice.question.survey_id != survey.id:
            return jsonify({"success": False, "message": "Ответ не принадлежит этому опросу."}), 400

        # Проверяем права пользователя
        if survey.user_id != current_user.id:
            return jsonify({"success": False, "message": "У вас нет прав на удаление этого ответа."}), 403

        # Удаляем ответ
        survey_session.delete(choice)
        survey_session.commit()

        return jsonify({"success": True, "message": "Ответ успешно удален."}), 200

    except SQLAlchemyError as e:
        survey_session.rollback()
        current_app.logger.error(f"Ошибка при удалении ответа: {e}")
        return jsonify({"success": False, "message": "Ошибка при удалении ответа."}), 500
    finally:
        survey_session.close()




@survey_bp.route('/all', methods=['GET'])
@service_required('survey_bp')
def get_surveys():
    survey_session = g.survey_session  # Используем вторичную сессию для запросов
    surveys = survey_session.query(Survey).all()  # Загружаем все опросы из вторичной базы данных
    surveys_data = []
    
    for survey in surveys:
        # Подсчет количества ответов через связь Question -> Choice
        total_choices = (
            survey_session.query(func.count(Choice.id))
            .join(Question, Question.id == Choice.question_id)
            .filter(Question.survey_id == survey.id)
            .scalar()
        )

        surveys_data.append({
            "id": survey.id,
            "title": survey.title,
            "description": survey.description,
            "user_id": survey.user_id,
            "created_at": survey.created_at.strftime('%d.%m.%Y %H:%M'),
            "updated_at": survey.updated_at.strftime('%d.%m.%Y %H:%M'),
            "access_user_ids": survey.access_user_ids,
            "survey_type": survey.survey_type,
            "total_choices": total_choices,  # Общее количество ответов
            "questions": len(survey.questions),  # Количество вопросов
            "is_active": survey.is_active  # Добавлено
        })

    return render_template('surveys_list.html', surveys=surveys_data)



@survey_bp.route('/<int:survey_id>', methods=['GET'])
@service_required('survey_bp')
def get_survey(survey_id):
    # Используем сессию для вторичной базы данных
    survey_session = g.survey_session
    survey = survey_session.query(Survey).get(survey_id)
    if not survey:
        return jsonify({"message": "Опрос не найден"}), 404

    # Подсчитываем количество ответов для всех вопросов в опросе
    choices_count = sum(len(question.choices) for question in survey.questions)

    survey_data = {
        "id": survey.id,
        "title": survey.title,
        "description": survey.description,
        "user_id": survey.user_id,
        "created_at": survey.created_at,
        "updated_at": survey.updated_at,
        "access_user_ids": survey.access_user_ids,
        "survey_type": survey.survey_type,
        "choices_count": choices_count,  # Общее количество ответов
        "questions": [
            {
                "id": question.id,
                "text": question.text,
                "choices": sorted(
                    [
                        {
                            "id": choice.id,
                            "text": choice.text,
                            "is_correct": choice.is_correct,
                            "order": choice.order,
                            "answer_type": choice.answer_type,
                            "icon": "/static/img/icon/text-field.svg" if choice.answer_type == "text_field" else None
                        }
                        for choice in question.choices
                    ],
                    key=lambda c: c["order"]
                )
            }
            for question in survey.questions
        ]
    }
    return jsonify({"survey": survey_data}), 200




@survey_bp.route('/<int:survey_id>/questions/<int:question_id>/choices', methods=['GET'])
@login_required
@service_required('survey_bp')
def get_choices(survey_id, question_id):
    """
    Получение списка ответов для конкретного вопроса.
    """
    try:
        # Получаем опрос
        survey = g.survey_session.query(Survey).get(survey_id)
        if not survey:
            return jsonify({"success": False, "message": "Опрос не найден."}), 404

        # Получаем вопрос
        question = g.survey_session.query(Question).get(question_id)
        if not question:
            return jsonify({"success": False, "message": "Вопрос не найден."}), 404

        # Проверяем, что вопрос принадлежит опросу
        if question.survey_id != survey.id:
            return jsonify({"success": False, "message": "Вопрос не принадлежит этому опросу."}), 400

        # Получаем список ответов, отсортированных по order
        choices = g.survey_session.query(Choice).filter_by(question_id=question_id).order_by(Choice.order).all()

        choices_data = [{
            "id": choice.id,
            "text": choice.text,
            "is_correct": choice.is_correct,
            "order": choice.order
        } for choice in choices]

        # Логирование полученных данных
        current_app.logger.info(f"Полученные ответы для вопроса {question_id}: {choices_data}")

        return jsonify({
            "success": True,
            "choices": choices_data,
            "survey_type": survey.survey_type
        }), 200

    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при получении ответов: {e}")
        return jsonify({"success": False, "message": "Ошибка при получении ответов."}), 500


@survey_bp.route('/<int:survey_id>', methods=['PUT'])
@service_required('survey_bp')
def update_survey(survey_id):
    survey_session = g.survey_session
    survey = survey_session.query(Survey).get(survey_id)
    if not survey:
        return jsonify({"message": "Опрос не найден"}), 404

    data = request.get_json()
    survey.title = data.get('title', survey.title).strip()
    survey.description = data.get('description', survey.description).strip()
    survey.survey_type = data.get('survey_type', survey.survey_type)
    survey.updated_at = datetime.utcnow()

    try:
        survey_session.commit()
        return jsonify({"message": "Survey updated"}), 200
    except SQLAlchemyError as e:
        survey_session.rollback()
        current_app.logger.error(f"Ошибка при обновлении опроса: {e}")
        return jsonify({"message": "Ошибка при обновлении опроса"}), 500

@survey_bp.route('/questions/<int:question_id>/update-type', methods=['PUT'])
@login_required
@service_required('survey_bp')
def update_question_type(question_id):
    """
    Обновление типа вопроса (single/multiple).
    """
    survey_session = g.survey_session
    data = request.get_json()
    new_type = data.get('question_type')

    if new_type not in ['single', 'multiple']:
        return jsonify({"success": False, "message": "Недопустимый тип вопроса."}), 400

    try:
        # Ищем вопрос по ID
        question = survey_session.query(Question).get(question_id)
        if not question:
            return jsonify({"success": False, "message": "Вопрос не найден."}), 404

        # Проверяем, что пользователь владеет опросом
        survey = survey_session.query(Survey).get(question.survey_id)
        if not survey or survey.user_id != current_user.id:
            return jsonify({"success": False, "message": "У вас нет прав на изменение этого вопроса."}), 403

        # Обновляем тип вопроса
        question.question_type = new_type
        question.updated_at = datetime.utcnow()
        survey_session.commit()

        return jsonify({"success": True, "message": "Тип вопроса успешно обновлен."}), 200

    except SQLAlchemyError as e:
        survey_session.rollback()
        current_app.logger.error(f"Ошибка при обновлении типа вопроса: {e}")
        return jsonify({"success": False, "message": "Ошибка при обновлении типа вопроса."}), 500


@survey_bp.route('/<int:survey_id>/questions/<int:question_id>/edit', methods=['PUT'])
@service_required('survey_bp')
def edit_question(survey_id, question_id):
    data = request.get_json()
    new_text = data.get('text')

    if not new_text:
        return jsonify({"success": False, "message": "Текст вопроса обязателен."}), 400

    try:
        survey_session = g.survey_session
        question = survey_session.query(Question).get(question_id)
        if not question:
            return jsonify({"success": False, "message": "Вопрос не найден."}), 404

        survey = survey_session.query(Survey).get(survey_id)
        if survey.user_id != current_user.id:
            return jsonify({"success": False, "message": "У вас нет прав для редактирования этого вопроса."}), 403

        question.text = new_text
        question.updated_at = datetime.utcnow()

        survey_session.commit()
        return jsonify({"success": True, "message": "Вопрос успешно обновлен."}), 200
    except SQLAlchemyError as e:
        survey_session.rollback()
        return jsonify({"success": False, "message": "Ошибка при редактировании вопроса."}), 500

@survey_bp.route('/choices/<int:choice_id>/update-correct-answer', methods=['PUT'])
@login_required
@service_required('survey_bp')
def update_correct_answer(choice_id):
    data = request.get_json()
    is_correct = data.get('is_correct', False)

    try:
        # Получаем ответ
        choice = g.survey_session.query(Choice).get(choice_id)
        if not choice:
            return jsonify({"success": False, "message": "Ответ не найден."}), 404

        # Обновляем поле is_correct
        choice.is_correct = is_correct
        g.survey_session.commit()

        return jsonify({"success": True, "message": "Поле is_correct успешно обновлено."}), 200

    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при обновлении правильного ответа: {e}")
        return jsonify({"success": False, "message": "Ошибка при обновлении ответа."}), 500

@survey_bp.route('/choices/<int:choice_id>/update', methods=['PUT'])
@login_required
@service_required('survey_bp')
def update_choice(choice_id):
    """
    Обновление текста и состояния правильного ответа (is_correct) для ответа.
    """
    try:
        # Получаем данные из запроса
        data = request.get_json()
        new_text = data.get('text', '').strip()
        is_correct = data.get('is_correct', False)

        # Ищем ответ в базе данных
        choice = g.survey_session.query(Choice).get(choice_id)
        if not choice:
            return jsonify({"success": False, "message": "Ответ не найден."}), 404

        # Обновляем данные
        if new_text:
            choice.text = new_text
        choice.is_correct = is_correct

        # Сохраняем изменения
        g.survey_session.commit()

        return jsonify({"success": True, "message": "Ответ успешно обновлен."}), 200

    except SQLAlchemyError as e:
        g.survey_session.rollback()
        current_app.logger.error(f"Ошибка при обновлении ответа: {e}")
        return jsonify({"success": False, "message": "Ошибка при обновлении ответа."}), 500


@survey_bp.route('/<int:survey_id>/participants', methods=['POST'])
@service_required('survey_bp')
def add_participant(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    data = request.get_json()
    first_name = data.get('first_name')
    last_name = data.get('last_name')
    iin = data.get('iin')
    phone_number = data.get('phone_number')
    auth_type = data.get('auth_type')
    patronymic = data.get('patronymic')
    eds_data = data.get('eds_data')
    manual_form_filled = data.get('manual_form_filled', False)

    if not all([first_name, last_name, iin, phone_number, auth_type]):
        return jsonify({"error": "Missing required fields"}), 400

    new_participant = Participant(
        first_name=first_name,
        last_name=last_name,
        patronymic=patronymic,
        iin=iin,
        phone_number=phone_number,
        auth_type=auth_type,
        eds_data=eds_data,
        manual_form_filled=manual_form_filled,
        survey=survey
    )

    try:
        db.session.add(new_participant)
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        current_app.logger.error(f"IntegrityError при добавлении участника: {e}")
        return jsonify({"error": "Participant with given IIN or phone number already exists"}), 400

    return jsonify({"message": "Participant added", "participant": {"id": new_participant.id, "name": f"{new_participant.first_name} {new_participant.last_name}"}}), 201
    
# Пример маршрута для получения участников опроса
@survey_bp.route('/<int:survey_id>/participants', methods=['GET'])
@service_required('survey_bp')
def get_participants(survey_id):
    survey = Survey.query.get_or_404(survey_id)
    participants = Participant.query.filter_by(survey_id=survey.id).all()
    participants_data = []
    for participant in participants:
        participants_data.append({
            "id": participant.id,
            "first_name": participant.first_name,
            "last_name": participant.last_name,
            "patronymic": participant.patronymic,
            "iin": participant.iin,
            "phone_number": participant.phone_number,
            "auth_type": participant.auth_type,
            "eds_data": participant.eds_data,
            "manual_form_filled": participant.manual_form_filled,
            "created_at": participant.created_at,
            "updated_at": participant.updated_at
        })
    return jsonify({"participants": participants_data}), 200

@survey_bp.route('/result/<string:url>', methods=['GET'])
@login_required
def survey_result(url):
    try:
        survey_session = get_survey_session()
        survey = survey_session.query(Survey).filter_by(url=url).first()

        if not survey:
            return render_template('survey_result.html', show_alert=True, alert_type='error',
                                   alert_title='Опрос не найден.',
                                   alert_text='Проверьте ссылку и попробуйте снова.',
                                   survey=None)

        # Проверка, что текущий пользователь является создателем опроса
        if survey.user_id != current_user.id:
            return render_template('survey_result.html', show_alert=True, alert_type='danger',
                                   alert_title='Доступ запрещён.',
                                   alert_text='У вас нет прав просматривать результаты этого опроса.',
                                   survey=None)

        # Получение параметров фильтрации
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        # Преобразование строк в datetime
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        else:
            start_date = datetime.combine(survey.created_at.date(), datetime.min.time())

        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d') + timedelta(days=1)
        else:
            end_date = datetime.utcnow()

        # Общая статистика опроса
        total_participants = survey_session.query(Participant).filter(
            Participant.survey_id == survey.id,
            Participant.created_at >= start_date,
            Participant.created_at < end_date
        ).count()
        created_at = survey.created_at
        updated_at = survey.updated_at
        survey_type = survey.survey_type
        is_active = survey.is_active
        access_user_ids = json.loads(survey.access_user_ids) if survey.access_user_ids else []

        # Статистика по вопросам
        questions = survey_session.query(Question).filter_by(survey_id=survey.id).all()
        questions_stats = []
        for question in questions:
            total_answers = survey_session.query(Result).filter_by(question_id=question.id).count()

            # Распределение вариантов ответов
            choices = survey_session.query(Choice).filter_by(question_id=question.id).all()
            choice_distribution = []
            for choice in choices:
                count = survey_session.query(Result).filter_by(choice_id=choice.id).count()
                choice_distribution.append({
                    'choice_text': choice.text,
                    'count': count
                })

            # Процент правильных ответов (для тестов)
            if survey_type in ['id_test', 'anon_test']:
                correct_choices = survey_session.query(Choice).filter_by(question_id=question.id, is_correct=True).all()
                total_correct_answers = survey_session.query(Result).filter(
                    Result.question_id == question.id,
                    Result.choice_id.in_([c.id for c in correct_choices])
                ).count()
                percentage_correct = (total_correct_answers / total_participants * 100) if total_participants > 0 else 0
            else:
                percentage_correct = None

            # Популярные текстовые ответы
            text_answers = survey_session.query(Result).filter(
                Result.question_id == question.id,
                Result.answer_text.isnot(None)
            ).all()
            text_answer_counts = {}
            for answer in text_answers:
                text = answer.answer_text.strip().lower()
                if text:
                    text_answer_counts[text] = text_answer_counts.get(text, 0) + 1
            popular_text_answers = sorted(text_answer_counts.items(), key=lambda item: item[1], reverse=True)[:5]

            questions_stats.append({
                'id': question.id,
                'text': question.text,
                'question_type': question.question_type,
                'total_answers': total_answers,
                'choice_distribution': choice_distribution,
                'percentage_correct': percentage_correct,
                'popular_text_answers': popular_text_answers
            })

        # Статистика по участникам
        participants = survey_session.query(Participant).filter(
            Participant.survey_id == survey.id,
            Participant.created_at >= start_date,
            Participant.created_at < end_date
        ).all()
        participant_stats = []
        for participant in participants:
            participant_stats.append({
                'id': participant.id,
                'first_name': participant.first_name,
                'last_name': participant.last_name,
                'patronymic': participant.patronymic,
                'iin': participant.iin,
                'phone_number': participant.phone_number,
                'auth_type': participant.auth_type,
                'eds_data': participant.eds_data,
                'manual_form_filled': participant.manual_form_filled,
                'created_at': participant.created_at,
                'updated_at': participant.updated_at
            })

        # Статистика по вариантам ответов
        choices_stats = []
        for question in questions:
            choices = survey_session.query(Choice).filter_by(question_id=question.id).all()
            for choice in choices:
                count = survey_session.query(Result).filter_by(choice_id=choice.id).count()
                choices_stats.append({
                    'choice_id': choice.id,
                    'question_id': question.id,
                    'choice_text': choice.text,
                    'count': count
                })

        # Общая эффективность опроса
        average_time = 0
        completion_rate = 0
        average_score = 0

        if participants:
            total_time = sum((participant.updated_at - participant.created_at).total_seconds() for participant in participants if participant.updated_at)
            average_time = total_time / len(participants) / 60  # в минутах

            completed = survey_session.query(Participant).filter(
                Participant.survey_id == survey.id,
                Participant.updated_at.isnot(None),
                Participant.updated_at != Participant.created_at
            ).count()
            completion_rate = (completed / total_participants * 100) if total_participants > 0 else 0

            if survey_type in ['id_test', 'anon_test']:
                total_scores = 0
                for participant in participants:
                    correct_results = survey_session.query(Result).join(Question).filter(
                        Result.participant_id == participant.id,
                        Question.id == Result.question_id,
                        Choice.is_correct == True,
                        Result.choice_id == Choice.id
                    ).count()
                    total_correct_questions = survey_session.query(Question).filter_by(survey_id=survey.id).count()
                    if total_correct_questions > 0:
                        total_scores += (correct_results / total_correct_questions) * 100  # Балл в процентах
                average_score = (total_scores / total_participants) if total_participants > 0 else 0

        # Временная статистика: количество участников по датам на основе Participant.created_at
        activity_by_date = {}
        # Query for participants in date range
        participants_in_range = survey_session.query(Participant).filter(
            Participant.survey_id == survey.id,
            Participant.created_at >= start_date,
            Participant.created_at < end_date
        ).all()

        for participant in participants_in_range:
            date_str = participant.created_at.strftime('%Y-%m-%d')
            activity_by_date[date_str] = activity_by_date.get(date_str, 0) + 1

        # Generate all dates in the range
        date_list = []
        count_list = []
        current_date = start_date.date()
        end_date_only = (end_date - timedelta(days=1)).date()

        while current_date <= end_date_only:
            date_str = current_date.strftime('%Y-%m-%d')
            date_list.append(date_str)
            count_list.append(activity_by_date.get(date_str, 0))
            current_date += timedelta(days=1)

        sorted_dates = date_list
        sorted_counts = count_list

        # Логирование отсортированных данных для отладки
        current_app.logger.debug(f"Sorted Dates: {sorted_dates}")
        current_app.logger.debug(f"Sorted Counts: {sorted_counts}")

        return render_template(
            'survey_result.html',
            survey=survey,
            show_alert=False,
            total_participants=total_participants,
            created_at=created_at,
            updated_at=updated_at,
            survey_type=survey_type,
            is_active=is_active,
            access_user_ids=access_user_ids,
            questions_stats=questions_stats,
            participant_stats=participant_stats,
            choices_stats=choices_stats,
            average_time=average_time,
            completion_rate=completion_rate,
            average_score=average_score,
            sorted_dates=sorted_dates,
            sorted_counts=sorted_counts,
            start_date=start_date.strftime('%Y-%m-%d') if start_date_str else '',
            end_date=(end_date - timedelta(days=1)).strftime('%Y-%m-%d') if end_date_str else ''
        )

    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error: {e}")
        return render_template('survey_result.html', show_alert=True, alert_type='error',
                               alert_title='Ошибка сервера',
                               alert_text='Пожалуйста, попробуйте позже.',
                               survey=None)
    finally:
        survey_session.close()
        

@survey_bp.route('/<int:survey_id>/participant_chart', methods=['GET'])
@login_required
@service_required('survey_bp')
def participant_chart(survey_id):
    try:
        # Получаем параметры запроса
        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')
        
        # Преобразуем строки в объекты datetime.date
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None

        # Базовый запрос
        query = g.survey_session.query(
            func.date(Participant.created_at).label('date'),
            func.count(Participant.id).label('count')
        ).filter(
            Participant.survey_id == survey_id
        )
        
        if start_date:
            query = query.filter(Participant.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            end_date_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
            query = query.filter(Participant.created_at < end_date_dt)

        query = query.group_by('date').order_by('date')
        results = query.all()

        # Извлекаем даты и количества
        dates = []
        per_day_counts = []
        for result in results:
            result_date = result.date
            # Проверяем, является ли result_date строкой, и преобразуем при необходимости
            if isinstance(result_date, str):
                result_date = datetime.strptime(result_date, '%Y-%m-%d').date()
            dates.append(result_date.strftime('%Y-%m-%d'))
            per_day_counts.append(result.count)

        # Если нет данных и фильтры не заданы, генерируем полный диапазон дат
        if not results and not (start_date or end_date):
            first_participant = g.survey_session.query(func.min(func.date(Participant.created_at))).filter(Participant.survey_id == survey_id).scalar()
            last_participant = g.survey_session.query(func.max(func.date(Participant.created_at))).filter(Participant.survey_id == survey_id).scalar()

            # Конвертируем строки в объекты datetime.date, если это строки
            if isinstance(first_participant, str):
                first_participant = datetime.strptime(first_participant, '%Y-%m-%d').date()
            if isinstance(last_participant, str):
                last_participant = datetime.strptime(last_participant, '%Y-%m-%d').date()

            if first_participant and last_participant:
                delta = (last_participant - first_participant).days
                all_dates = [first_participant + timedelta(days=i) for i in range(delta + 1)]
                dates = [date.strftime('%Y-%m-%d') for date in all_dates]
                per_day_counts = [0] * len(dates)
            else:
                dates = []
                per_day_counts = []
        
        # Вычисляем накопительные суммы
        cumulative_counts = []
        total = 0
        for count in per_day_counts:
            total += count
            cumulative_counts.append(total)
        
        # Получаем минимальную и максимальную даты
        min_date = g.survey_session.query(func.min(func.date(Participant.created_at))).filter(Participant.survey_id == survey_id).scalar()
        max_date = g.survey_session.query(func.max(func.date(Participant.created_at))).filter(Participant.survey_id == survey_id).scalar()

        # Конвертируем строки в объекты datetime.date, если это строки
        if isinstance(min_date, str):
            min_date = datetime.strptime(min_date, '%Y-%m-%d').date()
        if isinstance(max_date, str):
            max_date = datetime.strptime(max_date, '%Y-%m-%d').date()

        return jsonify({
            'dates': dates,
            'per_day_counts': per_day_counts,  # Фактические количества
            'cumulative_counts': cumulative_counts,  # Накопительные суммы
            'min_date': min_date.strftime('%Y-%m-%d') if min_date else None,
            'max_date': max_date.strftime('%Y-%m-%d') if max_date else None
        }), 200

    except Exception as e:
        current_app.logger.error(f"Ошибка при получении данных для графика: {e}")
        return jsonify({'success': False, 'message': 'Ошибка при получении данных для графика.'}), 500



#Клиентская сторона survey.py


@survey_bp.route('/<string:url>', methods=['GET'])
def view_survey(url):
    try:
        survey_session = get_survey_session()
        survey = survey_session.query(Survey).filter_by(url=url).first()

        if not survey:
            # Используем SweetAlert для уведомления об ошибке
            return render_template('view_survey.html', is_inactive=None, show_alert=True, alert_type='error', alert_title='Опрос не найден.', alert_text='Проверьте ссылку и попробуйте снова.', show_password_modal=False)

        # Проверка активности опроса
        if not survey.is_active:
            # Используем SweetAlert для уведомления о неактивном опросе
            return render_template('view_survey.html', is_inactive=True, show_alert=False, show_password_modal=False)

        # Проверка пароля
        if survey.password:
            show_password_modal = True
        else:
            show_password_modal = False

        questions = survey_session.query(Question).filter_by(survey_id=survey.id).all()
        for question in questions:
            choices = survey_session.query(Choice).filter_by(question_id=question.id).order_by(Choice.order).all()
            question.choices = choices

        return render_template(
            'view_survey.html',
            survey=survey,
            questions=questions,
            show_password_modal=show_password_modal,
            is_inactive=False
        )
    except SQLAlchemyError as e:
        current_app.logger.error(f"Database error: {e}")
        # Используем SweetAlert для уведомления об ошибке сервера
        return render_template('view_survey.html', is_inactive=None, show_alert=True, alert_type='error', alert_title='Ошибка сервера', alert_text='Пожалуйста, попробуйте позже.', show_password_modal=False)
    finally:
        survey_session.close()

@survey_bp.route('/<string:url>/submit', methods=['POST'])
def submit_survey(url):
    try:
        survey_session = get_survey_session()
        survey = survey_session.query(Survey).filter_by(url=url).first()

        if not survey or not survey.is_active:
            return jsonify({'success': False, 'message': 'Опрос не найден или отключен.'}), 404

        data = request.form.to_dict(flat=False)
        
        # Получаем session_id из формы
        session_id = data.get('session_id', [None])[0]
        participant = None

        if session_id:
            # Ищем участника по session_id
            participant = survey_session.query(Participant).filter_by(session_id=session_id, survey_id=survey.id).first()
            if not participant:
                return jsonify({'success': False, 'message': 'Сессия не найдена.'}), 400
        else:
            # Если session_id нет, создаем нового участника (для анонимного опроса)
            if survey.survey_type in ['id_survey', 'id_test']:
                participant = Participant(
                    first_name=data.get('first_name', [''])[0],
                    last_name=data.get('last_name', [''])[0],
                    patronymic=data.get('patronymic', [''])[0],
                    iin=data.get('iin', [''])[0],
                    phone_number=data.get('phone_number', [''])[0],
                    survey_id=survey.id,
                    auth_type='manual_form'
                )
                survey_session.add(participant)
                survey_session.commit()
            else:
                # Для анонимного опроса создаём участника с auth_type='anon_user'
                participant = Participant(
                    survey_id=survey.id,
                    auth_type='anon_user'
                )
                survey_session.add(participant)
                survey_session.commit()

        # Обновляем дополнительные поля участника, если они не установлены
        if participant and survey.survey_type in ['id_survey', 'id_test']:
            # Предполагается, что эти поля могут быть обновлены только один раз
            updated = False
            for field in ['first_name', 'last_name', 'patronymic', 'iin', 'phone_number']:
                value = data.get(field, [None])[0]
                if value and getattr(participant, field) != value:
                    setattr(participant, field, value)
                    updated = True
            if updated:
                # Если были изменения, `updated_at` будет автоматически обновлён
                pass

        # Явно обновляем `updated_at`
        participant.updated_at = datetime.utcnow()

        # Регулярное выражение для поиска полей вида 'question_X_text_Y'
        text_field_pattern = re.compile(r'^question_(\d+)_text_(\d+)$')

        # Сохранение текстовых ответов
        for key, values in data.items():
            match = text_field_pattern.match(key)
            if match:
                question_id = int(match.group(1))
                choice_id = int(match.group(2))
                answer_text = values[0].strip()
                if answer_text:  # Сохраняем только если есть текст
                    result = Result(
                        participant_id=participant.id,
                        survey_id=survey.id,
                        question_id=question_id,
                        choice_id=choice_id,
                        answer_text=answer_text
                    )
                    survey_session.add(result)

        # Сохранение выборов (radio и checkbox)
        for question in survey.questions:
            question_key = f'question_{question.id}'
            if question_key in data:
                answers = data[question_key]
                if not isinstance(answers, list):
                    answers = [answers]
                for answer in answers:
                    if answer.isdigit():
                        # Ответ в виде выбора варианта
                        result = Result(
                            participant_id=participant.id,
                            survey_id=survey.id,
                            question_id=question.id,
                            choice_id=int(answer)
                        )
                        survey_session.add(result)
                    else:
                        # Если ответ не цифровой, предполагаем, что это текстовый ответ без привязки к выбору
                        result = Result(
                            participant_id=participant.id,
                            survey_id=survey.id,
                            question_id=question.id,
                            answer_text=answer
                        )
                        survey_session.add(result)

        survey_session.commit()

        # Возвращаем JSON с успехом
        return jsonify({'success': True, 'message': 'Спасибо за участие!'})
    except SQLAlchemyError as e:
        survey_session.rollback()
        current_app.logger.error(f"Database error: {e}")
        return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера.'}), 500
    finally:
        survey_session.close()


@survey_bp.route('/<string:url>/init', methods=['POST'])
def init_survey(url):
    try:
        survey_session = get_survey_session()
        survey = survey_session.query(Survey).filter_by(url=url).first()

        if not survey or not survey.is_active:
            return jsonify({'success': False, 'message': 'Опрос не найден или отключен.'}), 404

        # Создаём нового участника с session_id и created_at
        participant = Participant(
            survey_id=survey.id,
            auth_type='anon_user' if survey.survey_type in ['anon_survey', 'anon_test'] else 'manual_form'
            # Поля created_at и session_id устанавливаются автоматически
        )
        survey_session.add(participant)
        survey_session.commit()

        return jsonify({'success': True, 'session_id': participant.session_id}), 200
    except SQLAlchemyError as e:
        survey_session.rollback()
        current_app.logger.error(f"Database error: {e}")
        return jsonify({'success': False, 'message': 'Внутренняя ошибка сервера.'}), 500
    finally:
        survey_session.close()

        
@survey_bp.before_request
def open_survey_session():
    g.survey_session = get_survey_session()

@survey_bp.teardown_request
def close_survey_session(exception=None):
    survey_session = g.pop('survey_session', None)
    if survey_session:
        survey_session.close()