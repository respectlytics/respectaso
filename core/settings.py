import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Current version — update on each release
VERSION = "2.3.2"

# Native macOS app vs Docker detection
IS_NATIVE_APP = os.environ.get("RESPECTASO_NATIVE") == "1" or getattr(sys, "frozen", False)

# Data directory: ~/Library/Application Support/RespectASO/ (native) or ./data (Docker)
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load .env from project root (dev) or DATA_DIR (production/Docker)
env_file = BASE_DIR / ".env"
if not env_file.exists():
    env_file = DATA_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file)

SECRET_KEY = os.environ.get("SECRET_KEY", "") or "django-insecure-dev-key-change-me-in-production"

DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "respectaso.private",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "aso",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.version",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# CSRF trusted origins for local access
CSRF_TRUSTED_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://respectaso.private",
    "http://localhost:9090",
    "http://127.0.0.1:9090",
    "http://respectaso.private:9090",
]

# Native app: allow any localhost port (Gunicorn binds to a random port)
if IS_NATIVE_APP:
    import re
    # Add a wildcard-like set of common ports for CSRF trust
    for p in range(8000, 8100):
        CSRF_TRUSTED_ORIGINS.append(f"http://127.0.0.1:{p}")
        CSRF_TRUSTED_ORIGINS.append(f"http://localhost:{p}")

# Logging — write to a file in the data directory for the native app
if IS_NATIVE_APP:
    _log_file = DATA_DIR / "respectaso.log"
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "file": {
                "level": "WARNING",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(_log_file),
                "maxBytes": 1_048_576,  # 1 MB
                "backupCount": 1,
                "formatter": "simple",
            },
        },
        "loggers": {
            "aso": {
                "handlers": ["file"],
                "level": "WARNING",
            },
        },
    }
