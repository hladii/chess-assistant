import os
import re
import secrets
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return generate_password_hash(password, method='scrypt')


def verify_password(password_hash: str, password: str) -> bool:
    if not password_hash or password is None:
        return False
    return check_password_hash(password_hash, password)


def normalize_email(email: str) -> str:
    return (email or '').strip().lower()


def validate_email(email: str):
    email = normalize_email(email)
    if not email or not EMAIL_RE.match(email):
        return None, 'Enter a valid email address.'
    return email, None


def validate_new_password(password: str, confirm: str = None):
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        return f'Password must be at least {MIN_PASSWORD_LENGTH} characters.'
    if confirm is not None and password != confirm:
        return 'New password and confirmation do not match.'
    return None


def normalize_display_name(name: str) -> str:
    return ' '.join((name or '').strip().split())


def validate_display_name(name: str):
    name = normalize_display_name(name)
    if not name:
        return None, 'Вкажіть імʼя.'
    if len(name) < 2:
        return None, 'Імʼя має містити щонайменше 2 символи.'
    if len(name) > 40:
        return None, 'Імʼя має бути до 40 символів.'
    return name, None


def user_initials(name: str, email: str = '') -> str:
    name = normalize_display_name(name)
    if name:
        parts = [p for p in name.split(' ') if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return name[:2].upper()
    email = normalize_email(email)
    return (email[:1] or '?').upper()


def seed_credentials():
    email = normalize_email(
        os.environ.get('SEED_USER_EMAIL', 'hladii.b.v@gmail.com')
    )
    password = os.environ.get('SEED_USER_PASSWORD', 'chess123')
    name = normalize_display_name(os.environ.get('SEED_USER_NAME', 'hladii')) or 'hladii'
    return email, password, name


def login_user(user):
    session.clear()
    session['user_id'] = user['id']
    session['user_email'] = user['email']
    session['user_name'] = user.get('display_name') or ''
    session.permanent = True
    rotate_csrf_token()


def logout_user():
    session.clear()


def current_user_id():
    return session.get('user_id')


def current_user_email():
    return session.get('user_email')


def current_user_name():
    return session.get('user_name') or ''


def current_user_initials():
    return user_initials(current_user_name(), current_user_email() or '')


def is_authenticated():
    return current_user_id() is not None


def rotate_csrf_token():
    session['csrf_token'] = secrets.token_urlsafe(32)
    return session['csrf_token']


def get_csrf_token():
    token = session.get('csrf_token')
    if not token:
        token = rotate_csrf_token()
    return token


def validate_csrf():
    sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    expected = session.get('csrf_token')
    if not expected or not sent or not secrets.compare_digest(str(sent), str(expected)):
        return False
    return True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Authentication required.'}), 401
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)

    return wrapped
