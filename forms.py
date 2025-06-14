from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, FileField, HiddenField
from wtforms.validators import DataRequired, Email, Optional, EqualTo, Length, Regexp
from flask_wtf.file import FileAllowed, FileRequired

class LoginForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[DataRequired(message="Введите имя пользователя.")])
    password = PasswordField('Пароль', validators=[DataRequired(message="Введите пароль.")])
    submit = SubmitField('Войти в аккаунт')

class RegisterForm(FlaskForm):
    username = StringField('Имя пользователя', validators=[
        DataRequired(message="Введите имя пользователя."),
        Length(min=4, max=25, message="Имя пользователя должно быть от 4 до 25 символов.")
    ])
    email = StringField('Электронная почта', validators=[
        DataRequired(message="Введите электронную почту."),
        Email(message="Введите корректный адрес электронной почты.")
    ])
    phone = StringField('Номер телефона', validators=[Optional(), Regexp(r'^\+?\d{10,15}$', message="Введите корректный номер телефона.")])
    first_name = StringField('Имя', validators=[Optional()])
    last_name = StringField('Фамилия', validators=[Optional()])
    middle_name = StringField('Отчество', validators=[Optional()])
    password = PasswordField('Пароль', validators=[
        DataRequired(message="Введите пароль."),
        Length(min=8, message="Пароль должен быть не менее 8 символов."),
        Regexp(
            r'^(?=.*[A-Z])(?=.*\d)[A-Za-z\d]{8,}$',  # Убрано требование специального символа
            message="Пароль должен содержать минимум одну заглавную букву и одну цифру."
        )
    ])
    confirm_password = PasswordField('Подтвердите пароль', validators=[
        DataRequired(message="Подтвердите пароль."),
        EqualTo('password', message="Пароли должны совпадать.")
    ])
    agreement = BooleanField('Я согласен с пользовательским соглашением и подтверждаю, что мне не менее 18 лет.', validators=[DataRequired(message="Вы должны согласиться с пользовательским соглашением.")])
    submit = SubmitField('Создать аккаунт')
    
class ProfileForm(FlaskForm):
    edit_mode = HiddenField('edit_mode')
    first_name = StringField('Имя', validators=[DataRequired()])
    last_name = StringField('Фамилия', validators=[Optional()])
    middle_name = StringField('Отчество', validators=[Optional()])
    email = StringField('Электронная почта', validators=[DataRequired(), Email()])
    phone = StringField('Телефон', validators=[Optional()])
    avatar = FileField('Аватар')
    submit = SubmitField('Сохранить изменения')

class AvatarUploadForm(FlaskForm):
    avatar = FileField('Выберите изображение', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Только изображения!')
    ])
    submit = SubmitField('Загрузить')