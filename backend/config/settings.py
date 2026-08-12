"""
Linq CRM — Django Settings
Production-ready configuration with environment variable overrides.
"""
import os
from pathlib import Path
import dj_database_url
from corsheaders.defaults import default_headers
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv
from decouple import AutoConfig

BASE_DIR = Path(__file__).resolve().parent.parent

# Resolve .env from the project layout rather than the current working
# directory, so `manage.py` works from backend/ or from the repo root.
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")
config = AutoConfig(search_path=BASE_DIR)

# ── Security ──────────────────────────────────────────────────────────────────
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default="False", cast=lambda v: v.lower() in ("true", "1"))
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

CORS_ALLOWED_ORIGINS = [
    o.strip() for o in
    os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if o.strip()
]

# Browser-based REST clients used to exercise /api/webhooks/ingest/ by hand send
# their own chrome-extension:// origin. Appended after the env list rather than
# added to .env, because .env is per-environment and this needs to hold wherever
# the app runs; load_dotenv() puts CORS_ALLOWED_ORIGINS into os.environ, so a
# value there would otherwise silently replace anything set here.
CORS_EXTRA_ALLOWED_ORIGINS = [
    "chrome-extension://gmmkjpcadciiokjpikmkkmapphbmdjok",
]
for _origin in CORS_EXTRA_ALLOWED_ORIGINS:
    if _origin not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS.append(_origin)

CORS_ALLOW_CREDENTIALS = True

# The auth headers this API uses are all "non-simple", so a browser will not send
# them unless the CORS preflight names them explicitly. django-cors-headers'
# defaults cover only accept / authorization / content-type / user-agent /
# x-csrftoken / x-requested-with — so a preflighted request arrived with no key
# at all and the endpoint correctly answered 401. All three are listed here:
#   x-crm-api-key    -> /api/webhooks/ingest/      (webhooks/utils.py)
#   x-webhook-secret -> legacy static secret       (webhooks/utils.py)
#   x-api-key        -> /api/invoices/create_from_website/ (book_event/authentication.py)
CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-crm-api-key",
    "x-webhook-secret",
    "x-api-key",
]

# ── Applications ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "corsheaders",
    "django_crontab",
    # Local — order matters for FK migrations
    "teams",
    "accounts",
    "companies",
    "events",
    "book_event",
    "book_delegate",
    "webhooks",
    "google_sync",
    "reports",
    "event_performance",
    "historical_event_registry",
    "ticket_central",
    # paper_review before proposal_submission: ProposalSubmission carries an FK to
    # PaperReview (source_paper_review), so its table has to exist first.
    "paper_review",
    "proposal_submission",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
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
        "DIRS": [BASE_DIR / "templates", BASE_DIR.parent / "frontend" / "build"],
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

# ── Database ──────────────────────────────────────────────────────────────────
# Resolved in order:
#   1. DATABASE_URL — single connection string, used by most hosting platforms
#   2. DB_NAME/DB_USER/DB_PASSWORD/… — explicit PostgreSQL credentials
#   3. SQLite under BASE_DIR — local development only, refused when DEBUG is off
DATABASE_URL = config("DATABASE_URL", default="")
DB_NAME = config("DB_NAME", default="")

if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL, conn_max_age=600)}
elif DB_NAME:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": DB_NAME,
            "USER": config("DB_USER"),
            "PASSWORD": config("DB_PASSWORD"),
            "HOST": config("DB_HOST", default="localhost"),
            "PORT": config("DB_PORT", default="5432"),
        }
    }
elif DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    raise ImproperlyConfigured(
        "No database configured. Set DATABASE_URL, or DB_NAME/DB_USER/DB_PASSWORD."
    )

# ── Custom Auth ───────────────────────────────────────────────────────────────
AUTH_USER_MODEL = "accounts.User"

# ── REST Framework ────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "config.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        # Appends the pk as a final tiebreaker so paginated lists cannot
        # duplicate or skip rows across pages. See accounts/ordering.py.
        "accounts.ordering.StableOrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

if DEBUG:
    REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"].append(
        "rest_framework.renderers.BrowsableAPIRenderer"
    )

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ── Static ────────────────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Only present once the React app has been built (`npm run build` in frontend/).
_frontend_static = BASE_DIR.parent / "frontend" / "build" / "static"
STATICFILES_DIRS = [_frontend_static] if _frontend_static.is_dir() else []
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ── Logging ───────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "book_event": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_BACKEND   = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST      = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT      = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS   = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL  = os.environ.get("DEFAULT_FROM_EMAIL", EMAIL_HOST_USER)
IMPORT_ALERT_EMAIL  = os.environ.get("IMPORT_ALERT_EMAIL", "")

# Kill switch for the Smart Import "auto-generated field" alerts sent by
# events/bulk_import/ and invoices/bulk_import/. Defaults FALSE — suppressed
# unless a deployment deliberately turns it on.
#
# Mirrors PAPER_REVIEW_NOTIFICATIONS_ENABLED below, and for the same reason:
# EMAIL_BACKEND is live Brevo SMTP with real credentials, IMPORT_ALERT_EMAIL is a
# real inbox, and both sends are synchronous and inside the import request. Those
# two endpoints alert once per CALL, not once per import, so a chunked load of the
# Zoho export (500 rows per call) would have delivered one message per chunk
# containing any row that was missing an invoice number / event code / name / date
# — dozens of emails from a single load.
#
# Suppression is the DEFAULT rather than a flag the caller passes, because the
# importing code path is the same one the UI's Smart Import uses and nothing in
# the request body could be relied on to carry it.
#
# Read at send time via django.conf.settings, not snapshotted at import, so
# toggling it takes effect without a process restart.
IMPORT_ALERT_EMAILS_ENABLED = os.environ.get(
    "IMPORT_ALERT_EMAILS_ENABLED", "False"
) == "True"

# ── Booking-code classification ───────────────────────────────────────────────
# Markers that put a free-text `booking_code` in the SpEx or speaker-sales
# category. Matching is boundary-anchored (book_event/booking_code.py), so a
# marker only counts where it sits between non-alphanumerics or string edges —
# "SPP" no longer matches "SUPPLEMENT".
#
# THESE VALUES ARE CARRIED FORWARD FROM THE OLD INLINE QUERY, NOT VERIFIED. The
# real distinct-value list is unknown until the Zoho export lands. Run
# `manage.py analyse_zoho_export <file>` against it and correct these lists
# before trusting any figure that depends on them.
BOOKING_CODE_SPEX_MARKERS    = ["spex"]
BOOKING_CODE_SPEAKER_MARKERS = ["speaker", "spp"]
BOOKING_CODE_SPEX_EXACT      = ["Add-Ons"]

# Watchdog address for the Paper Review production-team notification. Receives
# BOTH the "RECIPIENT FALLBACK" alert (nobody resolved from the event) and the
# "SCRIPT ERROR" alert (the send itself blew up). One constant, env-overridable,
# so redirecting it is a deployment change and not a code edit.
PAPER_REVIEW_ALERT_EMAIL = os.environ.get(
    "PAPER_REVIEW_ALERT_EMAIL", "arthur.pina@iq-hub.com"
)

# Kill switch for the Part B production-team notification. Defaults FALSE.
#
# EMAIL_BACKEND above is live Brevo SMTP with real credentials, and the send is
# synchronous (there is no task queue in this project — see B1 in the paper_review
# implementation report), so this is the ONLY thing standing between a UAT
# paper-review create and a real inbox. While False, PaperReviewViewSet still
# resolves recipients and renders the body — so resolve_recipients() can be
# verified against real Event data — but NotificationLog.Status.SUPPRESSED is
# written instead of a send being attempted.
#
# Read at SEND TIME (django.conf.settings, not a module-level constant snapshotted
# at import), so toggling it takes effect without a process restart.
PAPER_REVIEW_NOTIFICATIONS_ENABLED = os.environ.get(
    "PAPER_REVIEW_NOTIFICATIONS_ENABLED", "False"
) == "True"

# ── Website Integration ───────────────────────────────────────────────────────
WEBSITE_API_KEY     = os.environ.get("WEBSITE_API_KEY", "")
WEBHOOK_SECRET_KEY  = os.environ.get("WEBHOOK_SECRET_KEY", "")

# ── Google Sheets Sync ────────────────────────────────────────────────────────
# Look for credentials relative to project root
_creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "config/credentials/google-sheets.json")
GOOGLE_SHEETS_CREDENTIALS = os.path.join(BASE_DIR.parent, _creds_path)
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1x69V6G_qY6H5W_m6P9V6G_qY6H5W_m6P")
GOOGLE_SHEET_EVENTS_TAB = "Events"
GOOGLE_SHEET_BOOKINGS_TAB = "Bookings"

# ── CRM mirror ───────────────────────────────────────────────────────────────
# The "CRM data" spreadsheet: one tab per module, full-replaced on every run.
# Separate from GOOGLE_SHEET_ID above (the bookings/events push target) so the
# two cannot overwrite each other's tabs. Falls back to GOOGLE_SHEET_ID when
# unset. The service account must be an Editor on this sheet.
GOOGLE_SHEET_CRM_ID = os.environ.get("GOOGLE_SHEET_CRM_ID", "")


# ── Scheduled jobs ───────────────────────────────────────────────────────────
# cron strings use server time, and TIME_ZONE above is UTC. Activate on the
# Linux server with `python manage.py crontab add` (no-op on Windows dev).
CRONJOBS = [
    # Ticket Central ticket-number backfill (D5) — 07:00 IST == 01:30 UTC.
    ("30 1 * * *", "django.core.management.call_command", ["backfill_ticket_numbers"]),
    # CRM → "CRM data" spreadsheet mirror — 05:30 IST == 00:00 UTC.
    ("0 0 * * *",  "django.core.management.call_command", ["mirror_crm_to_sheet"]),
]
CRONTAB_LOCK_JOBS = True  # prevent overlap if a previous run is still going
