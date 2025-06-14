#decorators.py
from functools import wraps
from flask import abort, jsonify, current_app, make_response, request, redirect, url_for
from flask_login import current_user, login_required
from models import Service, UserTariff, Tariff, db
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError

def service_required(service_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                # Если это AJAX-запрос, возвращаем JSON-ответ
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"success": False, "message": "Необходима авторизация."}), 401
                # Если это обычный запрос, перенаправляем на страницу авторизации
                return redirect(url_for("auth.login") + "?next=" + request.path)
            
            # Здесь можно добавить логику проверки сервиса
            # Например:
            # if not user_has_service(current_user, service_name):
            #     if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            #         return jsonify({"success": False, "message": "Доступ запрещён."}), 403
            #     return redirect(url_for("auth.no_access"))

            return f(*args, **kwargs)
        return decorated_function
    return decorator
def admin_required(f):
    @wraps(f)
    @login_required  # Гарантирует, что пользователь аутентифицирован
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash("Доступ запрещён. Требуются права администратора.", "danger")
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

def tariff_required(service_name, feature, action):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return jsonify({"success": False, "message": "Требуется аутентификация."}), 401
            
            try:
                service = Service.query.filter_by(blueprint=service_name).first()
                if not service:
                    current_app.logger.error(f"Service with blueprint '{service_name}' not found.")
                    return jsonify({"success": False, "message": "Сервис не найден."}), 403

                user_tariff = UserTariff.query.filter(
                    UserTariff.user_id == current_user.id,
                    UserTariff.service_id == service.id,
                    UserTariff.start_date <= datetime.utcnow(),
                    UserTariff.end_date >= datetime.utcnow()
                ).first()

                if not user_tariff:
                    current_app.logger.error(f"No active tariff found for user {current_user.id} and service {service.name}.")
                    return jsonify({"success": False, "message": "Активный тариф не найден для этого сервиса."}), 403

                tariff = Tariff.query.get(user_tariff.tariff_id)
                if not tariff:
                    current_app.logger.error(f"Tariff with id '{user_tariff.tariff_id}' not found.")
                    return jsonify({"success": False, "message": "Детали тарифа не найдены."}), 403

                features = tariff.features.get('counters', {})
                max_limit = features.get(feature)

                if max_limit is not None:
                    used = user_tariff.counters.get(feature, 0)
                    current_app.logger.info(f"Used {feature}: {used}, Max {feature}: {max_limit}")
                    if used >= max_limit:
                        return jsonify({"success": False, "message": f"Лимит '{feature}' достигнут."}), 403

                response = f(*args, **kwargs)
                response = make_response(response)

                if max_limit is not None and response.status_code < 400:
                    user_tariff.counters[feature] = used + 1
                    user_tariff.updated_at = datetime.utcnow()
                    db.session.commit()

                return response

            except SQLAlchemyError as e:
                current_app.logger.error(f"Database error: {e}")
                return jsonify({"success": False, "message": "Внутренняя ошибка сервера."}), 500

        return decorated_function
    return decorator
