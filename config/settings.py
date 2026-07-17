from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "itm-workshop-django-secret-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "workshop",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "workshop.middleware.IdleLogoutMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "workshop" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "workshop.context_processors.workshop_settings",
            ],
        },
    }
]

WSGI_APPLICATION = "config.wsgi.application"

# Prefer explicit path; default next to project for easy backup/copy on updates.
DB_PATH = os.getenv("IT_MASTER_DB_PATH")
if DB_PATH:
    sqlite_name = str(Path(DB_PATH).expanduser().resolve())
else:
    sqlite_name = str(BASE_DIR / "db.sqlite3")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": sqlite_name,
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# Workshop auth (simple shared credentials for LAN managers)
WORKSHOP_USERNAME = os.getenv("IT_MASTER_USER", "ITM")
WORKSHOP_PASSWORD = os.getenv("IT_MASTER_PASSWORD", "pass")
WORKSHOP_IDLE_SECONDS = int(os.getenv("IT_MASTER_IDLE_SECONDS", str(6 * 60 * 60)))
SESSION_COOKIE_AGE = WORKSHOP_IDLE_SECONDS
SESSION_SAVE_EVERY_REQUEST = True

COMPANY_NAME = "ИТ- Мастерская"
COMPANY_PHONE = "+7 (918) 802 - 87 - 67"
QUALITY_PHONE = "+7 (962) 550 - 78 - 32"
COMPANY_ADDRESS = "р. Татарстан, д.Куюки, ул. 24 квартал дом 1"
MASTER_SIGN = "Григорьев Д.В"

from django.contrib.messages import constants as message_constants

MESSAGE_TAGS = {
    message_constants.DEBUG: "secondary",
    message_constants.INFO: "info",
    message_constants.SUCCESS: "success",
    message_constants.WARNING: "warning",
    message_constants.ERROR: "danger",
}
