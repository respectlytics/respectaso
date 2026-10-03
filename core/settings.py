import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Current version — update on each release
VERSION = "3.0.0"

# Native macOS app vs Docker detection
IS_NATIVE_APP = os.environ.get("RESPECTASO_NATIVE") == "1" or getattr(sys, "frozen", False)

# Data directory: ~/Library/Application Support/RespectASO/ (native) or ./data (Docker)
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))

# Test isolation: the test runner must NEVER see the real data dir - a test
# without an explicit DATA_DIR override would otherwise read real Apple Ads
# credentials from settings.json and could hit Apple's live API.
# Days are counted in the reader's time zone (aso/local_day.py); unset, the
# zone the browser reports or else the machine's own.
LOCAL_DAY_FALLBACK_ZONE = None

if "test" in sys.argv:
    import tempfile

    DATA_DIR = Path(tempfile.mkdtemp(prefix="respectaso-test-data-"))
    # A test counts days in UTC, whatever the machine running it.
    LOCAL_DAY_FALLBACK_ZONE = "UTC"

# And the test runner refuses every connection outside this machine, so a
# test that forgot to mock Apple fails at once instead of hanging whenever
# Apple throttles (core/test_runner.py).
TEST_RUNNER = "core.test_runner.NoNetworkTestRunner"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load .env from project root (dev) or DATA_DIR (production/Docker)
env_file = BASE_DIR / ".env"
if not env_file.exists():
    env_file = DATA_DIR / ".env"
if env_file.exists():
    load_dotenv(env_file)

SECRET_KEY = os.environ.get("SECRET_KEY", "") or "django-insecure-dev-key-change-me-in-production"

# On by default for development. The Mac app (desktop/main.py) and the Docker
# image (Dockerfile) turn it off, so a user sees RespectASO's own error pages
# (aso/error_views.py), never Django's debug page with its traceback.
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
    # Dates render in the reader's own time zone (aso/local_day.py)
    "aso.local_day.UserTimeZoneMiddleware",
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
                "aso.context_processors.popularity_source",
                "aso.context_processors.whats_new",
                "aso.context_processors.ui_state",
                "aso.context_processors.job_strip",
                "aso.context_processors.country_catalog",
                "aso.context_processors.classification_legend",
                "aso.context_processors.difficulty_factors",
                "aso.context_processors.pro_button",
                "aso.context_processors.nav",
                "aso.context_processors.back",
            ],
        },
    },
]

# A development server started without the autoreloader (scripts/dev_scratch.sh
# and the owner's test servers use --noreload) keeps Django's cached templates
# for its whole life, while the stylesheet is read fresh from disk: after a
# change, its pages mixed old templates with the new stylesheet and lost their
# padding (2026-10-02). In development, templates are read at every request.
# The Mac app and Docker run with DEBUG off and keep the cached loader.
if DEBUG:
    TEMPLATES[0]["APP_DIRS"] = False
    TEMPLATES[0]["OPTIONS"]["loaders"] = [
        "django.template.loaders.filesystem.Loader",
        "django.template.loaders.app_directories.Loader",
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
# WhiteNoise also serves straight from STATICFILES_DIRS with DEBUG off, the
# way it does with DEBUG on, so a stale or missing collectstatic copy can
# never leave a page unstyled.
WHITENOISE_USE_FINDERS = True

# What the app remembers for a visitor between pages (aso/ui_memory.py, the
# Dashboard selection, the Top Search Terms filters) lives in the session.
# In the Mac app every launch starts without cookies, so it lasts until the
# app quits. In a browser it replaced storage the browser kept for good, so
# it lasts a year from the last change instead of Django's two weeks
# (browsers cap a cookie near 400 days).
SESSION_COOKIE_AGE = 365 * 24 * 60 * 60

# A request that fails the CSRF check gets RespectASO's own page, or JSON
# for the app's scripts (aso/error_views.py).
CSRF_FAILURE_VIEW = "aso.error_views.csrf_failure"

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
                "level": "INFO",
                "class": "logging.handlers.RotatingFileHandler",
                "filename": str(_log_file),
                "maxBytes": 1_048_576,  # 1 MB
                "backupCount": 1,
                "formatter": "simple",
            },
        },
        "loggers": {
            # With DEBUG off, Django writes a crash's traceback only through
            # these: without them it would reach no one.
            "django.request": {
                "handlers": ["file"],
                "level": "ERROR",
                "propagate": False,
            },
            "django.security": {
                "handlers": ["file"],
                "level": "WARNING",
                "propagate": False,
            },
            "aso": {
                "handlers": ["file"],
                "level": "WARNING",
            },
            # Sign-in/verification trail at INFO - essential for diagnosing
            # Apple connection issues from a user's log.
            "aso.apple_ads": {
                "handlers": ["file"],
                "level": "INFO",
                "propagate": False,
            },
            # One line when the daily ranking refresh starts and one when
            # it ends, with how many keywords it refreshed: without them a
            # user's log cannot say whether the refresh ran.
            "aso.scheduler": {
                "handlers": ["file"],
                "level": "INFO",
                "propagate": False,
            },
            # The Mac app's native side (desktop/mac_integration.py): how it
            # was launched, the login item, sleep and wake, notifications.
            "desktop": {
                "handlers": ["file"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
else:
    # Docker and development: a crash's traceback and security warnings go to
    # the console (docker compose logs). With DEBUG off, Django's own default
    # would drop them.
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "console": {"class": "logging.StreamHandler"},
        },
        "loggers": {
            "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
            "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        },
    }
