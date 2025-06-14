# payment.py
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import current_user, login_required
from models import (
    Tariff, Service, UserTariff, PaymentInfo, db, 
    ServiceTariff, User
)
from datetime import datetime, timedelta
import uuid  # Для генерации уникальных номеров транзакций

payment_bp = Blueprint('payment_bp', __name__, url_prefix='/payment')

@payment_bp.route('/')
@login_required
def payment():
    tariff_id = request.args.get('tariff_id', type=int)

    if not tariff_id:
        return abort(400, description="Недостаточно данных для оплаты.")

    # Получаем данные тарифа
    tariff = Tariff.query.get_or_404(tariff_id)

    # Получаем user_id из текущего пользователя
    user_id = current_user.id

    # Получаем соответствующий service_id для данного тарифа
    service_tariff = ServiceTariff.query.filter_by(tariff_id=tariff_id).first()
    service_id = service_tariff.service_id if service_tariff else None

    if not service_id:
        return abort(400, description="Сервис для данного тарифа не найден.")

    # Передаем данные в шаблон
    return render_template(
        'payment.html', 
        user_id=user_id, 
        tariff_id=tariff_id, 
        service_id=service_id, 
        tariff=tariff
    )

@payment_bp.route('/confirm', methods=['POST'])
@login_required
def confirm_payment():
    data = request.get_json()
    tariff_id = data.get('tariff_id')
    service_id = data.get('service_id')
    payment_method = data.get('payment_method')

    if not tariff_id or not service_id or not payment_method:
        return jsonify({"success": False, "message": "Недостаточно данных."}), 400

    # Получаем тариф и проверяем, что он существует
    tariff = Tariff.query.get(tariff_id)
    if not tariff:
        return jsonify({"success": False, "message": "Тариф не найден."}), 404

    # Проверяем, что service_id соответствует тарифу
    service_tariff = ServiceTariff.query.filter_by(tariff_id=tariff_id, service_id=service_id).first()
    if not service_tariff:
        return jsonify({"success": False, "message": "Сервис не соответствует данному тарифу."}), 400

    # Определение даты окончания тарифа
    if tariff.duration_days and tariff.duration_days > 0:
        end_date = datetime.utcnow() + timedelta(days=tariff.duration_days)
    else:
        end_date = datetime.max  # Бессрочно

    # Инициализация счетчиков из tariff.features
    if tariff.features:
        counters = tariff.get_counters()
    else:
        counters = {}

    # Создание записи UserTariff
    user_tariff = UserTariff(
        user_id=current_user.id,
        tariff_id=tariff_id,
        service_id=service_id,
        start_date=datetime.utcnow(),
        end_date=end_date,
        counters=counters
    )

    # Генерация уникального номера транзакции
    transaction_number = str(uuid.uuid4())

    # Создание записи PaymentInfo
    payment_info = PaymentInfo(
        user_id=current_user.id,
        service_id=service_id,
        tariff_id=tariff_id,
        payment_method=payment_method,
        transaction_number=transaction_number,
        status='completed'  # или 'pending', в зависимости от логики
    )

    # Сохранение в базе данных
    try:
        db.session.add(user_tariff)
        db.session.add(payment_info)
        db.session.commit()
        return jsonify({"success": True, "message": "Оплата успешно подтверждена."})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Ошибка при подтверждении оплаты: {e}")
        return jsonify({"success": False, "message": "Не удалось подтвердить оплату."}), 500