"""
Linq CRM — Django Settings
Production-ready configuration with environment variable overrides.
"""
import base64
import os
import tempfile
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
ALLOWED_HOSTS = [
    h.strip() for h in
    os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# Django 4+ checks the Origin header on every unsafe request and rejects it
# unless the exact scheme+host appears here. Reaching the app through anything
# other than localhost — a tunnel, an sslip.io proxy, a staging domain — sends
# that host as the Origin, so a login POST came back as "CSRF verification
# failed" even with a perfectly valid token. Nothing was set at all before, so
# only same-origin localhost requests could ever pass.
#
# Entries must carry a scheme; bare hostnames are rejected by Django's own
# system check. Anything already in ALLOWED_HOSTS is trusted under both schemes,
# so a host only has to be named once, and CSRF_EXTRA_TRUSTED_ORIGINS covers
# origins whose host differs from the one Django is served under (a proxy that
# rewrites Host to localhost, which is why the sslip.io hostname never reached
# ALLOWED_HOSTS in the first place).
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in
    os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
for _host in ALLOWED_HOSTS:
    # A leading-dot wildcard is legal in both settings; "*" is not a valid
    # origin, so it is skipped rather than passed through to the check.
    if _host == "*":
        continue
    # ALLOWED_HOSTS spells a wildcard as a leading dot (".example.com"), while an
    # origin has to spell it "*.example.com". Passing the dotted form straight
    # through produced "https://.example.com", which matches nothing.
    _pattern = f"*{_host}" if _host.startswith(".") else _host
    for _scheme in ("https", "http"):
        _origin = f"{_scheme}://{_pattern}"
        if _origin not in CSRF_TRUSTED_ORIGINS:
            CSRF_TRUSTED_ORIGINS.append(_origin)

# A TLS-terminating proxy speaks plain HTTP to Django, so request.is_secure()
# is False and Django builds "http://<host>" when comparing against an Origin
# that arrived as "https://<host>". Honouring X-Forwarded-Proto makes that
# comparison agree. It is opt-in because trusting the header when no proxy sets
# it would let a client claim its own connection is secure.
if config("TRUST_PROXY_SSL_HEADER", default="False", cast=lambda v: v.lower() in ("true", "1")):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

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
#   x-data-api-key   -> /api/data/*                      (dataapi/authentication.py)
CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-crm-api-key",
    "x-webhook-secret",
    "x-api-key",
    "x-data-api-key",
]

# ── Applications ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Required by AddIndexConcurrently, used by the 00XX_perf_indexes migrations.
    "django.contrib.postgres",
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
    # Mining Resource Matrix. Reads ticket_central and events, owns no table
    # of its own, so it must sit AFTER both -- there are no migrations here to
    # order, but the service imports both models at module scope.
    "mining_matrix",
    # paper_review before proposal_submission: ProposalSubmission carries an FK to
    # PaperReview (source_paper_review), so its table has to exist first.
    "paper_review",
    "proposal_submission",
    # Read-only export surface for external consumers (Google Sheets). Its
    # authenticator is wired per-view in dataapi/views.py and must NEVER be
    # added to REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] below.
    "dataapi",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # ── GZip ──────────────────────────────────────────────────────────────────
    # Compresses every response the API sends, for clients that advertise gzip,
    # which every browser does. Nothing here was compressed before, and these are
    # long, highly repetitive JSON documents: measured on the current database, a
    # 50-row page of paper-reviews is 264 KB raw and 43 KB gzipped, tickets 56 KB
    # to 3 KB, delegates 65 KB to 6 KB. On API responses of tens to hundreds of KB
    # of JSON this is typically a 5x to 8x reduction off the wire, for one line and
    # a few milliseconds of CPU.
    #
    # POSITION IS LOAD-BEARING, and it is two constraints at once.
    #
    #   AFTER SecurityMiddleware. Security middleware decides the response's
    #   security headers — HSTS, referrer policy, the SSL redirect — and a redirect
    #   it issues short-circuits the rest of the chain. Letting it settle those
    #   first means gzip never compresses a body that was about to be replaced by a
    #   301, and the headers that govern the response are chosen before anything
    #   touches its bytes.
    #
    #   BEFORE everything that contributes to the body. Response middleware runs
    #   bottom-up, so sitting above SessionMiddleware, CommonMiddleware and the
    #   view itself is what makes gzip see the FINISHED document rather than a
    #   partial one. Django's own documentation states the same rule: place it
    #   before any middleware that may change or use the content.
    #
    # CorsMiddleware stays first: it short-circuits preflights, which have no body
    # to compress.
    #
    # ON BREACH. Compressing a response that carries a secret alongside
    # attacker-influenced text can leak the secret by length. Django masks the
    # CSRF token per response specifically so that gzip is safe here, and this API
    # authenticates with a Token header rather than a cookie, so there is no
    # session secret in these bodies to begin with.
    "django.middleware.gzip.GZipMiddleware",
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
            # Reuse the connection across requests, matching the DATABASE_URL
            # branch above, which has always passed conn_max_age=600. Without it
            # this branch opened a NEW PostgreSQL connection for every request and
            # closed it at the end — the handshake, authentication and TLS
            # negotiation paid again on each one. That is small against a local
            # socket and large against a managed database over the network, which
            # is exactly where this branch is used, so the two branches disagreeing
            # made the slower environment the one without pooling.
            #
            # 600 seconds, not persistent: a connection that lives forever holds a
            # backend slot after the worker goes idle, and Postgres has a fixed
            # max_connections. Django checks the connection's health before reuse,
            # so a link dropped by the server in between is reopened rather than
            # handed out broken.
            "CONN_MAX_AGE": 600,
            "CONN_HEALTH_CHECKS": True,
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

# Persistent connections and pre-use health checks, applied to WHICHEVER branch
# resolved above rather than only to DATABASE_URL. The DB_NAME branch previously
# had neither, so a deployment configured with explicit credentials opened and
# authenticated a fresh PostgreSQL connection on every request, including every
# 30-second table poll. CONN_HEALTH_CHECKS makes a recycled or dropped connection
# reconnect silently instead of surfacing as a burst of 500s after a DB restart.
# SQLite ignores both keys, so the DEBUG branch is unaffected.
DATABASES["default"].setdefault("CONN_MAX_AGE", 600)
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

# ── Cache ─────────────────────────────────────────────────────────────────────
# Redis when REDIS_URL is set; per-process LocMem otherwise. The fallback is
# deliberately real rather than DummyCache: the cached-count paginator and the
# dashboard cache added in this workstream must exercise the same code path in
# every environment, and a per-process cache is merely weaker (each gunicorn
# worker holds its own copy), never wrong — every entry here is a value that is
# allowed to be up to its TTL stale.
REDIS_URL = os.environ.get("REDIS_URL", "")
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
        "TIMEOUT": 300,
    } if REDIS_URL else {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "linq-crm-default",
        "OPTIONS": {"MAX_ENTRIES": 5000},
    },
}

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
    # ONE scope, for the public MRE paper review form, and nothing else. There is
    # deliberately no "anon" or "user" rate here: adding either would throttle
    # every authenticated view in the project as a side effect of rate-limiting
    # one pair of unauthenticated endpoints. See paper_review/public_form.py.
    "DEFAULT_THROTTLE_RATES": {
        "paper_review_form": "60/hour",
    },
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

# Port 465 wants an implicit TLS socket rather than STARTTLS on 587. Mutually
# exclusive with EMAIL_USE_TLS; Django raises if both are on.
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "False") == "True"

# Seconds. Django's SMTP backend passes None by default, which means the socket
# can hang until the OS gives up, and this send is SYNCHRONOUS inside the request
# that created the paper review; there is no task queue in this project. A relay
# that stops answering would otherwise hold a worker open indefinitely. The
# notification catches its own failures, so a timeout ends as a logged `failed`
# row rather than a 500.
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "20"))
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

# The STANDING Cc on the Paper Review production-team notification — copied on
# every send whoever the event's sales executive turns out to be. The other Cc is
# the MRE who filled the form, which is per-review and comes from the review's
# created_by, not from here (see paper_review/notifications.py).
#
# This replaced a role-walk over Event.assigned_users, which copied a different
# set of people per event. Comma-separated env override, so changing who is
# standing-copied is a deployment change and not a code edit.
#
# Read at send time via django.conf.settings, like the two constants either side.
PAPER_REVIEW_CC_EMAILS = [
    a.strip() for a in os.environ.get(
        "PAPER_REVIEW_CC_EMAILS", "harry.jonas@iq-hub.com",
    ).split(",") if a.strip()
]

# Where the "Open the review record" button in the paper review handoff email
# points. The frontend has no per-record route (App.jsx routes "paper-review" as
# a list page), so the link lands on the list; deep-linking is a frontend change,
# not a setting.
CRM_BASE_URL = os.environ.get("CRM_BASE_URL", "http://localhost:3000").rstrip("/")

# TESTING ONLY. When set, EVERY email paper_review/notifications.py sends — the
# handoff notification and both watchdog alerts — goes to this one address
# instead of its real recipients, with the intended To: named in the subject.
#
# This is not the kill switch below and does not replace it. The switch answers
# "should anything be sent at all"; this answers "while it is on, who is allowed
# to receive it", which is the question a UAT box actually has. Recipient
# RESOLUTION is untouched, so NotificationLog still records who the message was
# addressed to and report_paper_review_recipients still tells the truth.
#
# EMPTY IN PRODUCTION. A non-empty value here silently swallows every paper
# review email, which is exactly what you want on a test box and a severity-one
# incident anywhere else.
PAPER_REVIEW_REDIRECT_ALL_EMAIL = os.environ.get(
    "PAPER_REVIEW_REDIRECT_ALL_EMAIL", ""
).strip()

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

# ── Google Sign-In ────────────────────────────────────────────────────────────
# Web client ID from Google Cloud Console (OAuth 2.0 -> Web application). The
# same ID is handed to the browser as REACT_APP_GOOGLE_CLIENT_ID; the ID token flow
# uses no client secret, so there is nothing else to configure. Without this,
# POST /api/auth/google/ answers 500 rather than silently letting anyone in.
GOOGLE_OAUTH_CLIENT_ID = config("GOOGLE_OAUTH_CLIENT_ID", default="")
# Only these email domains may sign in. Emptying the list disables the check.
GOOGLE_OAUTH_ALLOWED_DOMAINS = [
    d.strip().lower()
    for d in config(
        "GOOGLE_OAUTH_ALLOWED_DOMAINS",
        default="iq-hub.com,linq-corporate.com",
    ).split(",")
    if d.strip()
]

# ── Google Sheets Sync ────────────────────────────────────────────────────────
# Look for credentials relative to project root
_creds_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "config/credentials/google-sheets.json")
GOOGLE_SHEETS_CREDENTIALS = os.path.join(BASE_DIR.parent, _creds_path)

# Production fallback: if the credentials FILE does not exist on disk but the
# entire JSON is available as a base64 env var, decode it to a temp file and
# repoint the setting. Runs once at startup. Local dev is unaffected because
# the file exists and this block never fires.
if not os.path.exists(GOOGLE_SHEETS_CREDENTIALS) and os.environ.get("GOOGLE_SHEETS_CREDENTIALS_B64"):
    _decoded = base64.b64decode(os.environ["GOOGLE_SHEETS_CREDENTIALS_B64"])
    _tmp = os.path.join(tempfile.gettempdir(), "google-sheets-credentials.json")
    with open(_tmp, "wb") as _f:
        _f.write(_decoded)
    GOOGLE_SHEETS_CREDENTIALS = _tmp

GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1x69V6G_qY6H5W_m6P9V6G_qY6H5W_m6P")
GOOGLE_SHEET_EVENTS_TAB = "Events"
GOOGLE_SHEET_BOOKINGS_TAB = "Bookings"

# ── CRM mirror ───────────────────────────────────────────────────────────────
# The "CRM data" spreadsheet: one tab per module, full-replaced on every run.
# Separate from GOOGLE_SHEET_ID above (the bookings/events push target) so the
# two cannot overwrite each other's tabs. Falls back to GOOGLE_SHEET_ID when
# unset. The service account must be an Editor on this sheet.
GOOGLE_SHEET_CRM_ID = os.environ.get("GOOGLE_SHEET_CRM_ID", "")


# ── Log retention ────────────────────────────────────────────────────────────
# Windows for `manage.py prune_logs`, in days, per "app_label.ModelName". They
# live here rather than in the command so changing one is a deployment change
# and not a code edit, and every one is env-overridable for the same reason.
# Defaults are deliberately conservative — a window that is too long costs disk,
# a window that is too short destroys evidence.
#
# accounts.ActionLog is the audit trail and is deliberately NOT listed. It is
# the largest table by growth rate and the one whose deletion needs a human
# decision, not a default. prune_logs reports what it WOULD delete at a given
# window via --report-action-logs, and deletes nothing.
LOG_RETENTION_DAYS = {
    "webhooks.WebhookLog":          int(os.environ.get("RETAIN_WEBHOOK_LOGS_DAYS", 90)),
    "teams.TeamActivityLog":        int(os.environ.get("RETAIN_TEAM_LOGS_DAYS", 730)),
    "paper_review.NotificationLog": int(os.environ.get("RETAIN_NOTIF_LOGS_DAYS", 365)),
}

# ── Scheduled jobs ───────────────────────────────────────────────────────────
# cron strings use server time, and TIME_ZONE above is UTC. Activate on the
# Linux server with `python manage.py crontab add` (no-op on Windows dev).
CRONJOBS = [
    # Ticket Central ticket-number backfill (D5) — 07:00 IST == 01:30 UTC.
    ("30 1 * * *", "django.core.management.call_command", ["backfill_ticket_numbers"]),
    # CRM → "CRM data" spreadsheet mirror — 05:30 IST == 00:00 UTC.
    ("0 0 * * *",  "django.core.management.call_command", ["mirror_crm_to_sheet"]),
    # Weekly, Sunday 02:00 UTC. --commit because cron has no operator to confirm;
    # the windows in LOG_RETENTION_DAYS are the safety margin, and ActionLog is
    # excluded by design.
    ("0 2 * * 0",  "django.core.management.call_command", ["prune_logs", "--commit"]),
]
CRONTAB_LOCK_JOBS = True  # prevent overlap if a previous run is still going
