import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

# After the application, which is what populates the app registry. Serving is
# the only entry point that should schedule anything; management commands must
# not. See services/scheduler.py.
from services import scheduler  # noqa: E402

scheduler.start()
