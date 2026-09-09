"""
seed_dmd_assignees
──────────────────
Create a Data Mining user for every DMD assignee name the tickets table already
holds, so the mass-update "Assign Name" dropdown has something to offer.

WHY THIS EXISTS
assign_name and assign_name_lx2 are CharFields holding a person's display name
(the D4 migration-safety decision), and TicketViewSet.bulk_update_fields now
offers them as a dropdown of active role=data_mining users rather than a
free-text box. On the live data that dropdown starts EMPTY: 37,008 tickets name
about a hundred DMD people across the two columns, and the users table has two
data_mining accounts, neither of whom appears in it once.

WHAT "CONNECTS" A USER TO THEIR TICKETS
The name string, and nothing else. There is no ForeignKey to populate and no
backfill to run, a user whose display name equals the stored text is matched by
every filter, report and dropdown that reads the column. So the names created
here are the stored spellings VERBATIM. Tidying a spelling HERE would produce an
account matching none of that person's rows, which is the one outcome this
command exists to avoid.

RUN normalize_dmd_assignees FIRST. Verbatim seeding is only correct once the
column itself holds one spelling per person. Straight off the raw Zoho data it
produced 92 accounts for about 70 people, three of them for Mahek Soni alone,
because "SC - Mahek Soni", "SC- Mahek Soni" and "Mahek Soni" are three distinct
strings. The other command collapses those in the tickets table; this one then
creates exactly one account per surviving name. Order matters, and running this
one alone is what reintroduces the duplicates.

That also means the reverse holds, and is the answer to "what if I make the
account myself": create a user by hand whose full name equals a stored name and
they appear in the dropdown on the next request, connected to those rows. This
command is only the bulk version of doing that ~90 times.

WHAT IT CREATES
An account that exists but CANNOT SIGN IN: login_access=False, an unusable
password, and a placeholder address on the reserved .invalid TLD (RFC 2606, so a
stray email can never reach a real person). Give it a real address and tick
login access when the person actually needs to log in. status is active because
that is what puts them in the dropdown.

AND IT GOES IN THE DATA MINING TEAM, which is not cosmetic. Almost everything on
the user record is scoped BY TEAM — the Reporting manager picker offers the
chosen team's leads, then that team's appointed manager (`managed_team`), then
the administrators. A teamless account matches none of the first two, so its
Reporting manager field silently falls all the way through to the administrator
list and the person appointed to run Data Mining is never offered. The team is
found by name (role_from_team_name, the same rule User.save() applies), so this
works on any deployment whose DMD team is called something else.

Team membership here grants nothing on its own: a team carries a permission
grid, and Data Mining's is empty. Access still has to be given deliberately.

    python manage.py seed_dmd_assignees --dry-run     # writes nothing
    python manage.py seed_dmd_assignees
    python manage.py seed_dmd_assignees --min-count 50

Idempotent, and it CONVERGES: a name already held by a user is skipped, so
re-running after the data grows only adds what is new, and any account this
command created earlier that is still missing its team is repaired in passing.
An existing user who is NOT data_mining is reported rather than promoted — a
role is a permissions grant and not this command's to hand out.
"""
import logging
import re

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from accounts.models import role_from_team_name
from teams.models import Team
from ticket_central.models import Ticket
from ticket_central.utils import display_name

logger = logging.getLogger(__name__)
User = get_user_model()

# Below this many tickets a value is a typo or a one-off, not a person on the
# team: the live tail is "hushbu Vaidya" (1), "SC - Vrunda Raii" (1), "Delete"
# (1), "Content not found" (1) and four bare dates. Raise it to seed fewer
# accounts; the dry run prints exactly what any threshold would create.
DEFAULT_MIN_COUNT = 20

# A value naming several people at once ("Mahek Soni / Rupal Parmar"). It is a
# real thing the column holds — 348 distinct combinations — but it is not a
# person and must not become a user account.
COMBINATION = " / "

EMAIL_DOMAIN = "dmd.invalid"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def data_mining_team():
    """
    The team whose NAME implies the data_mining role, or None.

    Matched on the name rather than an id or a literal "DMD Team" because that is
    the rule the model itself applies — User.save() derives a role from the team
    name through this same function — so a deployment that calls the team
    something else is already covered by it.
    """
    for team in Team.objects.all():
        if role_from_team_name(team.name) == "data_mining":
            return team
    return None


def local_part(name):
    """'SC - Mahek Soni' -> 'sc.mahek.soni'. Always non-empty for a real name."""
    return _NON_ALNUM.sub(".", name.lower()).strip(".") or "dmd.assignee"


def stored_names(min_count):
    """
    {name: ticket count} for every individual DMD assignee the data names at
    least `min_count` times, across BOTH assignment columns.

    The two columns are summed rather than queried separately because the same
    person appears in both — assign_name is the first pass, assign_name_lx2 the
    second — and a name that clears the threshold only by combining them is
    still that one person.
    """
    counts = {}
    for column in ("assign_name", "assign_name_lx2"):
        rows = (
            Ticket.objects.exclude(**{column: ""})
            .exclude(**{f"{column}__isnull": True})
            # order_by() clears Ticket.Meta.ordering, whose columns would
            # otherwise join the SELECT list and defeat the grouping — the same
            # trap map_added_users documents.
            .order_by().values(column).annotate(n=Count("id"))
        )
        for row in rows:
            name = (row[column] or "").strip()
            if not name or COMBINATION in name:
                continue
            counts[name] = counts.get(name, 0) + row["n"]
    return {n: c for n, c in counts.items() if c >= min_count}


def existing_by_name():
    """
    {lower-cased display name: user} for every user, active or not.

    Keyed on the display name because that is the only thing linking a user to
    these columns. Inactive users are included deliberately: seeding a duplicate
    account for someone who was merely deactivated would be worse than skipping
    them and saying so.
    """
    return {
        (display_name(u) or "").strip().lower(): u
        for u in User.objects.all()
    }


class Command(BaseCommand):
    help = "Create data_mining users from the DMD assignee names in tickets."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be created but write nothing.",
        )
        parser.add_argument(
            "--min-count", type=int, default=DEFAULT_MIN_COUNT,
            help=f"Skip names used on fewer tickets than this "
                 f"(default {DEFAULT_MIN_COUNT}).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        min_count = options["min_count"]

        names = stored_names(min_count)
        existing = existing_by_name()
        team = data_mining_team()
        if team is None:
            self.stdout.write(self.style.WARNING(
                "No team whose name implies Data Mining — accounts will be "
                "created without one, and their Reporting manager field will "
                "offer only administrators. Create the team, then re-run to "
                "place them."
            ))
        # Seeded local parts have to be unique against each other as well as
        # against the table, or two names that slugify alike collide mid-run.
        taken = set(
            User.objects.values_list("username", flat=True)
        ) | set(
            e.lower() for e in User.objects.values_list("email", flat=True) if e
        )

        created, skipped, wrong_role, placed = [], [], [], []

        with transaction.atomic():
            # Repairing runs FIRST and off its own query, not inside the loop
            # over `names` below. Those names come from the tickets table, which
            # can be empty — a module wipe, a fresh environment — or trimmed by
            # --min-count, while accounts an earlier run created still need their
            # team. Tying the repair to the ticket data would make it silently
            # do nothing in exactly the cases somebody re-runs this to fix.
            #
            # Narrowed to the placeholder domain, so a real person who happens to
            # share a name is never moved out of a team somebody put them in.
            if team is not None:
                stranded = User.objects.filter(
                    email__endswith=f"@{EMAIL_DOMAIN}", team__isnull=True,
                )
                for user in stranded:
                    user.team = team
                    user.role_is_explicit = True
                    if not dry_run:
                        user.save(update_fields=["team", "role"])
                    placed.append((display_name(user), 0))

            for name, count in sorted(names.items(), key=lambda kv: -kv[1]):
                user = existing.get(name.lower())
                if user:
                    if user.role != User.Role.DATA_MINING or not user.is_active:
                        wrong_role.append((name, user, count))
                        continue
                    skipped.append((name, count))
                    continue

                base = local_part(name)
                email = f"{base}@{EMAIL_DOMAIN}"
                n = 2
                while email.lower() in taken or base in taken:
                    base = f"{local_part(name)}{n}"
                    email = f"{base}@{EMAIL_DOMAIN}"
                    n += 1
                taken.add(base)
                taken.add(email.lower())

                first, _, last = name.partition(" ")
                new = User(
                    username=base,
                    email=email,
                    first_name=first,
                    last_name=last,
                    role=User.Role.DATA_MINING,
                    status=User.Status.ACTIVE,
                    # Exists, appears in the dropdown, cannot sign in until
                    # somebody gives it a real address and ticks the box.
                    login_access=False,
                    # The Reporting manager picker is scoped to the chosen
                    # team's leads and that team's appointed manager; a teamless
                    # account matches neither and is offered administrators
                    # alone. See the module docstring.
                    team=team,
                )
                # save() derives role from the team's name on a new row unless
                # the caller says the role was chosen. The Data Mining team
                # derives to exactly this role anyway, so this only matters if
                # the team is later renamed to something that does not.
                new.role_is_explicit = True
                new.set_unusable_password()
                if not dry_run:
                    new.save()
                created.append((name, email, count))

            if dry_run:
                transaction.set_rollback(True)

        verb = "Would create" if dry_run else "Created"
        for name, email, count in created:
            self.stdout.write(f"  + {name:<24} {email:<34} {count:>6} tickets")
        for name, user, count in wrong_role:
            self.stdout.write(self.style.WARNING(
                f"  ! {name:<24} exists as {user.email} "
                f"(role={user.role}, active={user.is_active}) — {count} tickets. "
                f"Set role to Data Mining and activate to list them."
            ))
        if placed:
            self.stdout.write(self.style.SUCCESS(
                f"  ~ {len(placed)} seeded account(s) with no team "
                f"{'would be' if dry_run else 'were'} moved into {team.name} — "
                f"their Reporting manager field now offers that team's leads "
                f"and its appointed manager, not just the administrators."
            ))
        if skipped:
            self.stdout.write(
                f"  = {len(skipped)} name(s) already have a Data Mining user."
            )

        self.stdout.write(self.style.SUCCESS(
            f"{verb} {len(created)} user(s) from {len(names)} name(s) used "
            f"{min_count}+ times. {len(placed)} placed in a team, "
            f"{len(wrong_role)} need attention."
        ))
        logger.info(
            "seed_dmd_assignees: dry_run=%s min_count=%d names=%d created=%d "
            "skipped=%d placed=%d wrong_role=%d team=%s",
            dry_run, min_count, len(names), len(created), len(skipped),
            len(placed), len(wrong_role), team.name if team else None,
        )
