"""
Central configuration — local-first, cloud-portable.

Everything that changes between "this PC" and "cloud" is read from
environment variables with safe local defaults. To move to cloud later you
only set env vars (DATABASE_URL, SECRET_KEY, etc.) — no code changes.
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _abs(*parts):
    return os.path.join(BASE_DIR, *parts)


class Config:
    # ---- Security ----
    # On cloud, set SECRET_KEY env var. Locally a stable dev key is fine.
    SECRET_KEY = os.environ.get('SECRET_KEY', 'wiom-recon-local-dev-key-change-on-cloud')

    # ---- Database ----
    # Local default: SQLite file next to the app.
    # Cloud: set DATABASE_URL=postgresql://user:pass@host/db  (no code change needed)
    DATABASE_URL = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + _abs('wiom_recon.db')
    )
    # Railway/Heroku give postgres:// ; SQLAlchemy 1.4+ needs postgresql://
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ---- Folders ----
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', _abs('uploads'))
    OUTPUT_FOLDER = os.environ.get('OUTPUT_FOLDER', _abs('output'))
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB

    # ---- First admin (manager) — created on first launch if no users exist ----
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'saurav.mishra@wiom.in')
    ADMIN_NAME = os.environ.get('ADMIN_NAME', 'Saurav Mishra')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')  # change after first login

    # ---- Server ----
    PORT = int(os.environ.get('PORT', '5000'))
    HOST = os.environ.get('HOST', '0.0.0.0')

    # ---- GST cache (vendor name fallback source) ----
    GST_CACHE_PATH = os.environ.get(
        'GST_CACHE_PATH',
        r'C:\Users\Saurav Mishra\OneDrive\Desktop\Claude\GSTIN CHECK\gst_cache.json'
    )


os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)
os.makedirs(Config.OUTPUT_FOLDER, exist_ok=True)
