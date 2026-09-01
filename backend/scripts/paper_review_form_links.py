#!/usr/bin/env python
"""
paper_review_form_links.py
───────────────────────────
Prints one ready-to-send paper review form link per Market Research reviewer,
minting the key for anyone who has none yet.

    cd backend
    python scripts/paper_review_form_links.py https://crm.example.com
    python scripts/paper_review_form_links.py https://crm.example.com --dry-run

WHY THIS EXISTS ALONGSIDE THE KEYS PAGE
The keys page issues one link at a time, which is the right shape for a single
new reviewer. Handing the whole team their links at once is a different job, and
doing it by clicking Generate nine times then copying nine rows is where a link
ends up sent to the wrong person.

THE HOST IS AN ARGUMENT AND CANNOT BE GUESSED. A link is host plus path plus
key. The key is in the database and the path is in urls.py, but the host is
whatever the reviewers actually reach the CRM through — a proxy hostname today
(see ALLOWED_HOSTS) rather than anything written in this repo. Passing the wrong
one produces links that resolve to nobody's CRM, so it is required rather than
defaulted.

IDEMPOTENT. A reviewer who already has a form key keeps it, and their existing
link is printed. Run it again after adding a reviewer and only the new one is
minted. To rotate a link, regenerate it on the keys page; this script never
replaces a key it did not create.

REVIEWERS WITH NO ASSIGNED EVENTS ARE REPORTED, NOT SKIPPED SILENTLY. Their form
would open on a 409 telling them to ask for event assignments, so the link is
useless until somebody acts, and a silent omission hides that.
"""
from __future__ import annotations

import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model            # noqa: E402
from django.db import connection                          # noqa: E402
from django.urls import reverse                           # noqa: E402

from paper_review.access import permitted_event_codes     # noqa: E402
from webhooks.models import WebhookApiKey                  # noqa: E402

# The front-end route, which is App.jsx's and not Django's — the API path the
# page posts to is reverse()d below only to prove the endpoint is wired.
FORM_ROUTE = "/paper-review/submit"
KEY_PARAM  = "crm_key"
TARGET     = WebhookApiKey.Target.PAPER_REVIEW_FORM


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry  = "--dry-run" in argv

    if len(args) != 1:
        print(__doc__.strip())
        return 2

    origin = args[0].rstrip("/")
    if not origin.startswith(("http://", "https://")):
        print(f"Host must include the scheme, e.g. https://{origin}")
        return 2

    # Fails loudly here rather than printing links to an endpoint that is not
    # routed, which would look like a working link and 404 for the reviewer.
    reverse("paper-review-form-submit")

    # WHICH DATABASE, stated before anything else. The failure this catches is
    # the one that looks like nothing at all: running the script in a shell whose
    # environment points at a different database than the site is served from
    # mints perfectly valid keys that the live site has never heard of, and every
    # printed link then answers "this form link is not valid".
    db = connection.settings_dict
    print(f"\nDatabase: {db.get('NAME')} on {db.get('HOST') or 'localhost'}"
          f":{db.get('PORT') or 'default'}")
    print(f"Existing form keys in it: "
          f"{WebhookApiKey.objects.filter(target=TARGET).count()}")

    User = get_user_model()
    reviewers = User.objects.filter(role="market_research").order_by("username")
    if not reviewers:
        print("No users hold the market_research role; nothing to issue.")
        return 1

    print()
    print(f"{'Reviewer':<26} {'Events':>6}  Link")
    print("-" * 100)

    minted = existing = eventless = 0
    for user in reviewers:
        # The same source the form itself reads — the event's Market Research
        # columns — so this report and the live form can never disagree. Reading
        # user.assigned_events here reported a different list from the one the
        # reviewer actually saw.
        codes = permitted_event_codes(user)
        key = WebhookApiKey.objects.filter(mre=user, target=TARGET).first()

        # Counted before the dry-run branch returns, or the summary would report
        # zero eventless reviewers on exactly the run made to check for them.
        if not codes:
            eventless += 1

        if key is None:
            if dry:
                note = "" if codes else "  [NO EVENTS ASSIGNED]"
                print(f"{user.username:<26} {len(codes):>6}  "
                      f"(would mint a new key){note}")
                minted += 1
                continue
            key = WebhookApiKey.objects.create(
                name=f"{user.username} paper review form",
                api_key=WebhookApiKey.generate_key(),
                target=TARGET, mre=user,
                notes="Issued by scripts/paper_review_form_links.py",
            )
            minted += 1
        else:
            existing += 1

        state = "" if key.is_active else "  [DISABLED, resume it on the keys page]"
        if not codes:
            state += "  [NO EVENTS ASSIGNED, the form will refuse to open]"
        print(f"{user.username:<26} {len(codes):>6}  "
              f"{origin}{FORM_ROUTE}?{KEY_PARAM}={key.api_key}{state}")

    print("-" * 100)
    print(f"{minted} minted, {existing} already had a link, "
          f"{eventless} cannot open the form yet.")
    print("Each link is a credential: whoever holds it can submit reviews as "
          "that reviewer. Send them one to one, not to a group thread.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
