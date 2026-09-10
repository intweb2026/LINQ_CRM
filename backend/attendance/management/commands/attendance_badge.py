"""
A scannable badge for a REAL confirmed attendee, so the door can be tested.

    python manage.py attendance_badge --event ACU --edition 2025 --out badge.png
    python manage.py attendance_badge --event ACU --speaker --out speaker.png

IT WRITES NOTHING TO THE DATABASE, and that is the point of it rather than a
caveat. The attendee is CHOSEN from the module's own admissible set --
attendance.roster.confirmed(), the same function the roster endpoint and scan/
both read -- so if this command can produce a badge, the door will accept it,
and if it cannot, the door would have refused. A command that created a
synthetic booking to photograph would prove the opposite: that a badge works for
a person who only exists because the test made them.

The token is printed as well as drawn, so it can be pasted straight into the
scan endpoint or the manual field without a camera.

WHY segno. Pure Python, no C extension and no system library, which matters on
the Windows checkouts this runs on. Pinned in requirements.txt, and imported
INSIDE handle() so that neither the app registry nor any test that never asks
for a PNG depends on it being installed.
"""
from django.core.management.base import BaseCommand, CommandError

from attendance import qr, roster


class Command(BaseCommand):
    help = "Write a scannable QR badge PNG for a real confirmed attendee."

    def add_arguments(self, parser):
        parser.add_argument("--event", required=True,
                            help="Delegate-side event code, e.g. ACU.")
        parser.add_argument("--edition", type=int, default=None,
                            help="Edition, e.g. 2025. Omit for an event that "
                                 "carries none.")
        parser.add_argument("--speaker", action="store_true",
                            help="Pick a speaker rather than a delegate.")
        parser.add_argument("--out", default="badge.png",
                            help="Where to write the PNG.")

    def handle(self, *args, **options):
        try:
            import segno
        except ImportError as exc:
            raise CommandError(
                "segno is not installed. pip install -r requirements.txt"
            ) from exc

        event_code, edition = options["event"], options["edition"]
        wanted = "speaker" if options["speaker"] else "delegate"

        confirmed = roster.confirmed(event_code, edition)
        if not confirmed:
            raise CommandError(
                f"No confirmed attendee on {event_code} {edition or ''}. "
                "Either the event code is spelled the CATALOGUE's way rather "
                "than the delegates table's way (the trailing year lives in "
                "--edition), or nobody on it is on the check-in sheet."
            )

        picked = next((d for d in confirmed
                       if roster.attendee_type(d) == wanted), None)
        if picked is None:
            raise CommandError(
                f"{len(confirmed)} confirmed attendee(s) on that event, none of "
                f"them a {wanted}."
            )

        row = roster.row(picked)
        segno.make(row["token"], error="m").save(options["out"], scale=6, border=2)

        self.stdout.write(self.style.SUCCESS(f"Wrote {options['out']}"))
        self.stdout.write(f"  attendee : {row['attendee_name']} "
                          f"({row['attendee_type']})")
        self.stdout.write(f"  company  : {row['company_name'] or '-'}")
        self.stdout.write(f"  event    : {row['event_code']} "
                          f"{row['edition'] or ''}".rstrip())
        self.stdout.write(f"  token    : {row['token']}")
        # A last assertion that the thing written is the thing the server reads.
        # It re-decodes the payload through the SAME reader scan/ uses, so a
        # change to the codec that broke the round trip fails here rather than
        # at a turnstile.
        decoded = qr.read(row["token"])
        if not decoded or decoded["id"] != picked.id:
            raise CommandError("The minted token does not read back. "
                               "See attendance/qr.py.")
