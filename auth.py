"""
Authentication + user management (Flask-Login based).
Roles: 'user' (Book-Keeping), 'admin' (Tax Team/Manager), 'superadmin' (Taxation Manager).
"""
from functools import wraps
from datetime import timedelta
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from flask_login import (LoginManager, login_user, logout_user,
                         login_required, current_user)
from models import db, User, LoginEvent, now_ist

LOCKOUT_THRESHOLD = 5      # failed attempts
LOCKOUT_WINDOW_MIN = 15    # within this many minutes

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to continue.'

auth_bp = Blueprint('auth', __name__)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(f):
    """Restrict to manager level (admin or superadmin): approve, upload, audit."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def superadmin_required(f):
    """Restrict to top level (superadmin): manage users, Zoho/Settings."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_superadmin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


def write_required(f):
    """Block viewer role from any write/action route."""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        if current_user.is_viewer:
            from flask import jsonify
            return jsonify(ok=False, error='View-only access — no actions allowed.'), 403
        return f(*args, **kwargs)
    return wrapper


def _safe_next(nxt):
    """Only allow same-origin relative redirect targets (prevents open redirect)."""
    if not nxt or not nxt.startswith('/') or nxt.startswith('//'):
        return url_for('dashboard')
    from urllib.parse import urlparse
    if urlparse(nxt).netloc:
        return url_for('dashboard')
    return nxt


def _recent_failures(email):
    since = now_ist() - timedelta(minutes=LOCKOUT_WINDOW_MIN)
    return LoginEvent.query.filter(LoginEvent.email == email,
                                   LoginEvent.success == False,
                                   LoginEvent.created_at >= since).count()


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        pw = request.form.get('password', '')
        ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
        ua = request.headers.get('User-Agent', '')[:300]

        if _recent_failures(email) >= LOCKOUT_THRESHOLD:
            db.session.add(LoginEvent(email=email, success=False, ip=ip, user_agent=ua))
            db.session.commit()
            flash(f'Too many failed attempts. Try again in {LOCKOUT_WINDOW_MIN} minutes.', 'error')
            return render_template('login.html')

        user = User.query.filter_by(email=email).first()
        ok = bool(user and user.active and user.check_password(pw))
        db.session.add(LoginEvent(email=email, success=ok, ip=ip, user_agent=ua))
        db.session.commit()
        if ok:
            login_user(user, remember=True)
            return redirect(_safe_next(request.args.get('next')))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


# ----------------------------------------------------------------------
# USER MANAGEMENT (super admin only)
# ----------------------------------------------------------------------
VALID_ROLES = ('user', 'admin', 'superadmin', 'viewer')


@auth_bp.route('/users')
@superadmin_required
def users():
    all_users = User.query.order_by(User.created_at).all()
    return render_template('users.html', users=all_users)


@auth_bp.route('/users/create', methods=['POST'])
@superadmin_required
def create_user():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    pw = request.form.get('password', '')
    role = request.form.get('role', 'user')
    title = request.form.get('title', '').strip()
    states = request.form.get('states', '').strip()

    if role not in VALID_ROLES:
        role = 'user'
    if not (name and email and pw):
        flash('Name, email and password are required.', 'error')
        return redirect(url_for('auth.users'))
    if User.query.filter_by(email=email).first():
        flash(f'A user with email {email} already exists.', 'error')
        return redirect(url_for('auth.users'))

    u = User(name=name, email=email, role=role, title=title, states=states)
    u.set_password(pw)
    db.session.add(u)
    db.session.commit()
    flash(f'User {name} created.', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/users/<int:uid>/update', methods=['POST'])
@superadmin_required
def update_user(uid):
    u = db.session.get(User, uid)
    if not u:
        abort(404)
    u.name = request.form.get('name', u.name).strip()
    new_role = request.form.get('role', u.role)
    if new_role in VALID_ROLES:
        u.role = new_role
    u.title = request.form.get('title', u.title).strip()
    u.states = request.form.get('states', u.states).strip()
    u.active = request.form.get('active') == 'on'
    # email edit (with uniqueness check)
    new_email = request.form.get('email', '').strip().lower()
    if new_email and new_email != u.email:
        if User.query.filter(User.email == new_email, User.id != u.id).first():
            flash(f'Email {new_email} is already in use.', 'error')
            return redirect(url_for('auth.users'))
        u.email = new_email
    # admin force-reset: set a new password WITHOUT needing the old one
    new_pw = request.form.get('password', '').strip()
    if new_pw:
        u.set_password(new_pw)
    db.session.commit()
    flash(f'User {u.name} updated.', 'success')
    return redirect(url_for('auth.users'))


@auth_bp.route('/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    """Self-service password change — requires the current (old) password."""
    if request.method == 'POST':
        old = request.form.get('old', '')
        new = request.form.get('new', '')
        confirm = request.form.get('confirm', '')
        if not current_user.check_password(old):
            flash('Current password is incorrect.', 'error')
        elif len(new) < 6:
            flash('New password must be at least 6 characters.', 'error')
        elif new != confirm:
            flash('New password and confirmation do not match.', 'error')
        else:
            current_user.set_password(new)
            db.session.commit()
            flash('Password changed successfully.', 'success')
            return redirect(url_for('dashboard'))
    return render_template('change_password.html')
