"""
Django settings for fuel_route_planner project.
Django 6.0.x with PostGIS, DRF, and Redis caching.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-key-change-in-production-abc123xyz",
)

DEBUG = os.environ.get("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "*").split(",")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    # Third-party
    "rest_framework",
    # Project apps
    "stations",
    "trip_planner",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database — PostGIS
# ---------------------------------------------------------------------------
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgis://fuel_user:fuel_password@localhost:5432/fuel_route_db",
)

# Parse DATABASE_URL manually for PostGIS engine
def _parse_database_url(url: str) -> dict:
    """Parse a postgis:// URL into Django DATABASES dict entry."""
    from urllib.parse import urlparse

    parsed = urlparse(url.replace("postgis://", "postgresql://"))
    return {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": parsed.path.lstrip("/"),
        "USER": parsed.username or "",
        "PASSWORD": parsed.password or "",
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
    }


DATABASES = {
    "default": _parse_database_url(DATABASE_URL),
}

# ---------------------------------------------------------------------------
# Cache — Redis
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 3600,  # 1 hour default
        "KEY_PREFIX": "frp",
    }
}

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "EXCEPTION_HANDLER": "trip_planner.exceptions.custom_exception_handler",
}

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Application-specific settings
# ---------------------------------------------------------------------------
FUEL_PLANNER = {
    "VEHICLE_MPG": 10,
    "MAX_RANGE_MILES": 500,
    "TANK_CAPACITY_GALLONS": 50,
    "SAFETY_RESERVE_MILES": 10,
    "FEASIBLE_LEG_LIMIT_MILES": 490,
    "ENDING_RESERVE_GALLONS": 1,
    "ROUTE_CORRIDOR_MILES": 5,
    "OSRM_BASE_URL": os.environ.get(
        "OSRM_BASE_URL", "https://router.project-osrm.org"
    ),
    "OSRM_TIMEOUT_SECONDS": 5,
    "CENSUS_GEOCODER_URL": (
        "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
    ),
    "CENSUS_GEOCODER_TIMEOUT_SECONDS": 5,
    "CENSUS_BATCH_URL": (
        "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
    ),
    "GEOCODE_CACHE_TIMEOUT": 86400 * 30,  # 30 days
    "ROUTE_CACHE_TIMEOUT": 3600,  # 1 hour
    "FUEL_PLAN_CACHE_TIMEOUT": 1800,  # 30 minutes
}
