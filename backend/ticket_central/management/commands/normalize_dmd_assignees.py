"""
normalize_dmd_assignees
───────────────────────
Collapse every DMD assignee name onto ONE spelling per person, the main name.

WHY THIS EXISTS
assign_name and assign_name_lx2 are free CharFields (the D4 decision), and eight
years of hand entry left the same person under many spellings. Two separate
causes, both fixed here.

1. A CODE PREFIX. Roughly 9,000 rows carry one, "SC - Mahek Soni" against
   "Mahek Soni" and "SC- Mahek Soni". SC dominates at 8,944 rows; LX, WH, BX,
   PX, CX and SPEX account for 24 between them. Tested against type_of_ticket,
   against Simple/Complex and against whether the row has an LX-2 second pass,
   the prefix predicts none of them, so it carries no information this schema
   records. It is stripped.

2. TYPO PAIRS. Four are folded by the explicit table below, never by fuzzy
   matching. Each was confirmed the same person by the strongest signal the data
   offers; both spellings appear in many SHARED assignments and never once
   together on the same ticket, which two real colleagues on this team would
   have been within a few hundred joint tickets.

WHAT IS DELIBERATELY NOT MERGED
Riddhi Patel and Siddhi Patel DO appear together on one ticket, so they are two
people; a fuzzy pass at 0.63 similarity would have collapsed 1,181 tickets onto
one of them. Janki and Janvi Solanki stay apart on the same reasoning, different
first names and no evidence of a typo. Anjali Solanki, "Anjali Solanki (New)"
and "Anjali Solanki 2" stay apart because those suffixes were typed by hand to
tell people apart, so merging them would almost certainly merge real colleagues.
Priya H Prajapati stays apart from Priya Prajapati; 81 tickets and 5 shared
assignments is too thin to call.

WHY THE TICKET ROWS MUST CHANGE, NOT JUST THE ACCOUNTS
The link between a user and their tickets is the name STRING and nothing else,
there is no ForeignKey. Give the dropdown "Mahek Soni" while 1,400 rows hold
"SC - Mahek Soni" and picking it matches none of them, every filter on it misses
them, and the next mass update writes a fourth spelling. So the column is
rewritten to the same canonical name the account will carry.

REVERSIBLE
A many-to-one rewrite cannot be undone from its own result, so every old value
is written to an undo file BEFORE anything changes. The path is printed; keep it
until you are satisfied.

    python manage.py normalize_dmd_assignees --dry-run
    python manage.py normalize_dmd_assignees
    python manage.py normalize_dmd_assignees --undo-file C:\\path\\undo.json

Run this BEFORE seed_dmd_assignees. Normalising first is what makes that command
create one clean account per person instead of one per spelling.

AND IT REPAIRS THE CASE WHERE THAT ORDER WAS NOT FOLLOWED, which is why the
accounts pass exists. Seed first and one person holds several accounts,
"SC - Mahek Soni" beside the real "Mahek Soni", both offered by the mass-update
dropdown. This command now derives that work from canonical() rather than the
three hand-listed ACCOUNT_RENAMES: a prefixed account is renamed, and a
duplicate is set inactive, which is what removes it from the dropdown. It is
never deleted, because Ticket.created_by is SET_NULL and deleting would erase
authorship on every row that account raised. When more than one account in a
group can sign in, the group is reported and left alone; that is either two
colleagues sharing a name or one person with two working logins, and neither is
this command's call. Both kinds of account change are recorded in the undo file
alongside the ticket rows.
"""
import json
import logging
import re
from datetime import datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from ticket_central.models import Ticket

logger = logging.getLogger(__name__)
User = get_user_model()

COLUMNS = ("assign_name", "assign_name_lx2")

# Exactly the seven prefixes the live column actually carries, not every code in
# Ticket.TypeOfTicket. Narrow on purpose; a wider pattern risks eating the start
# of a real name that happens to look like a code.
PREFIX = re.compile(r"^\s*(SC|LX|WH|BX|PX|CX|SPEX)\s*-\s*", re.I)
WHITESPACE = re.compile(r"\s+")

# Typo pairs, keyed on the UPPER-CASED losing spelling. Confirmed same person by
# the shared-assignment test described above; the winner is the spelling the
# house already uses elsewhere in the roster, which is why Aastha beats the
# more numerous Aashtha, Aastha Panchal and Aastha Bhatt are both spelled that
# way.
TYPO_MERGES = {
    "SHRADHA JADAV":    "Shraddha Jadav",
    "BHARATI MAHAJAN":  "Bharti Mahajan",
    "AASHTHA SINGH":    "Aastha Singh",
    "DRASHTI PANCHAL":  "Dhrashti Panchal",
    # The tail, folded because leaving it out would strand rows rather than
    # because the counts demand it. The first two would otherwise be orphaned by
    # the account rename below, which moves 'Bharati Chauhan' onto the spelling
    # 405 rows use; these are the 2 rows that spell her the old way. 'Vrunda
    # Raii' is one row and one keystroke off a name with 1,242.
    "BHARATI CHAUHAN":  "Bharti Chauhan",
    "BHARATI C":        "Bharti Chauhan",
    "VRUNDA RAII":      "Vrunda Rai",
}

# Accounts whose display name matches almost nothing in the tickets, renamed onto
# the spelling the data actually uses. Email, login and every permission stay
# untouched; only the name the dropdown keys on changes. M T and R P are absent
# deliberately, neither matches any assignee name, so there is nothing to align
# them to and the command reports them instead of guessing.
ACCOUNT_RENAMES = {
    "Bharati Chauhan": "Bharti Chauhan",
    "Neha S":          "Neha Shinde",
    "K R":             "KR",
}


# ── The accounts side ────────────────────────────────────────────────────────
#
# WHY THIS EXISTS ALONGSIDE ACCOUNT_RENAMES
# ACCOUNT_RENAMES above is a hand-written table of three, for accounts whose name
# matches nothing in the tickets and which no rule could derive. It does NOT
# cover the case seed_dmd_assignees creates: that command builds accounts from
# the stored spellings VERBATIM, prefix included, so seeding a table that has not
# been normalised yet mints "SC - Mahek Soni" beside the real "Mahek Soni" and
# one person ends up holding several accounts. Renaming the tickets afterwards
# does not clean that up, and the mass-update dropdown then offers the same
# person two or three times.
#
# So the pass below derives the account work from canonical() instead of listing
# it, which is the same rule the ticket columns get.
#
# WHAT IT WILL NOT DO
# It never deletes an account; Ticket.created_by is SET_NULL, so deleting would
# quietly erase authorship on every row that account raised. A duplicate is set
# INACTIVE, which is what removes it from the dropdown (User.save syncs
# is_active from status, and bulk_update_fields filters on is_active), and is
# reversible from the admin.
#
# It also refuses to choose when BOTH accounts look real, meaning the loser can
# sign in or has signed in. That is either two colleagues who genuinely share a
# name or a person with two working logins, and neither is this command's to
# decide; the group is reported and left alone.
# Imported rather than retyped; seed_dmd_assignees owns the placeholder domain,
# and a second copy of it here would go stale the day that one changes.
def _seed_email_suffix():
    from .seed_dmd_assignees import EMAIL_DOMAIN

    return "@" + EMAIL_DOMAIN


def stored_and_canonical(user):
    """
    (the name as stored, the canonical form of it).

    ACCOUNT_RENAMES is consulted here, NOT only in the loop that applies it.
    THE BUG THIS FIXES, seen on live data: the hand-written pass renamed
    'Neha S' to 'Neha Shinde' while this pass independently kept a different
    account already called 'Neha Shinde', so the run ENDED with two active
    accounts under one name and the dropdown still showed the person twice. It
    hit 'Bharti Chauhan', 'Neha Shinde' and 'KR'. One grouping has to see both
    kinds of rename, or they collide by construction.

    canonical() first, since it handles 'Bharati Chauhan' through TYPO_MERGES;
    the table is then checked against both spellings, because its keys are the
    raw stored names.
    """
    name = (user.get_full_name() or user.username).strip()
    canon = canonical(name)
    return name, ACCOUNT_RENAMES.get(name, ACCOUNT_RENAMES.get(canon, canon))


def _keeper_rank(user):
    """
    Higher is more real. Signing in beats everything, then an address that is
    not the seeder's placeholder, then having actually signed in, then being
    active, then being the older record.
    """
    return (
        1 if user.login_access else 0,
        0 if (user.email or "").lower().endswith(_seed_email_suffix()) else 1,
        1 if user.last_login else 0,
        1 if user.status == "active" else 0,
        -user.id,
    )


def account_plan(users, dmd_role="data_mining", already_renamed_ids=()):
    """
    (renames, merges, skipped) for the accounts.

    renames  [(user, stored, canonical)]            one account, wrong spelling
    merges   [(keeper, [loser, ...], canonical)]    one person, several accounts
    skipped  [(canonical, [user, ...], reason)]     not ours to decide

    Grouping spans every role, so a seeded Data Mining duplicate of a real
    account in another team is still caught, but only a data_mining account is
    ever renamed or deactivated.
    """
    groups = {}
    for user in users:
        stored, canon = stored_and_canonical(user)
        # A cell naming two people is not a person, and no account should be one.
        if not canon or "/" in stored:
            continue
        groups.setdefault(canon.upper(), []).append(user)

    renames, merges, skipped = [], [], []
    for canon_key in sorted(groups):
        members = groups[canon_key]
        ranked = sorted(members, key=_keeper_rank, reverse=True)
        keeper = ranked[0]
        losers = ranked[1:]
        _, canon = stored_and_canonical(keeper)

        real_losers = [u for u in losers if u.login_access or u.last_login]
        if real_losers:
            skipped.append((canon, members, "more than one account looks real"))
            continue

        wrong_role = [u for u in losers if u.role != dmd_role]
        if wrong_role:
            skipped.append((canon, wrong_role, "duplicate is not a Data Mining account"))
        losers = [u for u in losers if u.role == dmd_role]
        if losers:
            merges.append((keeper, losers, canon))

        stored_keeper, _ = stored_and_canonical(keeper)
        if (stored_keeper != canon and keeper.role == dmd_role
                and keeper.id not in already_renamed_ids):
            renames.append((keeper, stored_keeper, canon))
    return renames, merges, skipped


def canonical(name):
    """
    'SC- Mahek Soni' -> 'Mahek Soni'; 'Shradha Jadav' -> 'Shraddha Jadav'.

    Prefix off, inner whitespace collapsed, then the typo table. Casing is left
    alone rather than title-cased, because title-casing turns 'KR' into 'Kr' and
    'Priya H Prajapati' into something nobody typed.
    """
    stripped = WHITESPACE.sub(" ", PREFIX.sub("", (name or "").strip())).strip()
    return TYPO_MERGES.get(stripped.upper(), stripped)


def rewrite(value):
    """
    The canonical form of one stored cell, or None when it is already canonical.

    A cell naming several people is handled segment by segment, so
    'SC - Bharti Chauhan / Himani Gandhi' loses the prefix and keeps both names.
    Rejoining normalises the separator to ' / ', which is why a few rows change
    on spacing alone.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    if "/" in raw:
        parts = [canonical(p) for p in raw.split("/")]
        parts = [p for p in parts if p]
        new = " / ".join(parts)
    else:
        new = canonical(raw)
    return new if new and new != value else None


class Command(BaseCommand):
    help = "Collapse DMD assignee spellings onto one canonical name per person."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report every change but write nothing.",
        )
        parser.add_argument(
            "--undo-file", default=None,
            help="Where to record the old values. Defaults to a timestamped "
                 "file in the working directory.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        undo_path = options["undo_file"] or (
            f"dmd_normalize_undo_{datetime.now():%Y%m%d_%H%M%S}.json"
        )

        # Only rows that could possibly change, rather than all 47,356.
        rows = Ticket.objects.exclude(
            Q(assign_name="") & Q(assign_name_lx2="")
        ).only("id", *COLUMNS).order_by("id")

        undo = []
        edits = []
        folded = {}
        combos = 0
        for ticket in rows.iterator(chunk_size=2000):
            changed = {}
            for column in COLUMNS:
                old = getattr(ticket, column) or ""
                new = rewrite(old)
                if new is None:
                    continue
                changed[column] = new
                folded.setdefault(new, set()).add(old)
                if "/" in old:
                    combos += 1
            if changed:
                undo.append({
                    "id": ticket.id,
                    "old": {c: getattr(ticket, c) for c in changed},
                })
                for column, new in changed.items():
                    setattr(ticket, column, new)
                edits.append(ticket)

        standalone = sum(
            1 for e in undo for v in e["old"].values() if "/" not in v
        )
        self.stdout.write(
            f"{len(undo)} ticket(s) to change; {standalone} standalone value(s), "
            f"{combos} inside a shared assignment."
        )

        # The merges worth reading, one line per canonical name that absorbed
        # more than just itself.
        for new in sorted(folded):
            olds = {o for o in folded[new] if o != new}
            if olds:
                self.stdout.write(f"  {new:<22} <- " + " | ".join(sorted(olds)))

        renames = []
        for old_name, new_name in ACCOUNT_RENAMES.items():
            first, _, last = new_name.partition(" ")
            for user in User.objects.all():
                if (user.get_full_name() or user.username).strip() != old_name:
                    continue
                renames.append((user, old_name, new_name, first, last))

        for _, old_name, new_name, _, _ in renames:
            self.stdout.write(self.style.WARNING(
                f"  account rename: {old_name!r} -> {new_name!r}"
            ))

        # The derived accounts pass. Everything above is the hand-written table;
        # this is the part that catches what seed_dmd_assignees minted from
        # un-normalised ticket names.
        # EVERY user, including the ones ACCOUNT_RENAMES above already covers.
        # Excluding those was the collision bug; stored_and_canonical applies
        # the table, so a hand-renamed account groups with the name it is
        # becoming and is either kept or deactivated, never left as a second
        # active copy. Their rename is already queued, hence already_renamed_ids.
        derived_renames, merges, skipped = account_plan(
            User.objects.all(),
            dmd_role=User.Role.DATA_MINING,
            already_renamed_ids={u.id for u, _, _, _, _ in renames},
        )
        self.stdout.write(
            f"\naccounts: {len(derived_renames)} to rename, {len(merges)} person(s) "
            f"holding more than one account, {len(skipped)} group(s) left alone."
        )
        for _user, stored, canon in derived_renames:
            self.stdout.write(f"  rename   {stored!r} -> {canon!r}")
        for keeper, losers, canon in merges:
            self.stdout.write(
                f"  keep     id={keeper.id} {(keeper.get_full_name() or keeper.username)!r}"
                f" as {canon!r}")
            for loser in losers:
                name = (loser.get_full_name() or loser.username).strip()
                self.stdout.write(self.style.WARNING(
                    f"    deactivate id={loser.id} {name!r} email={loser.email!r}"))
        for canon, members, reason in skipped:
            ids = ", ".join(f"id={u.id}" for u in members)
            self.stdout.write(self.style.WARNING(
                f"  skipped  {canon!r}, {reason}, {ids}"))

        unmatched = [
            (user.get_full_name() or user.username).strip()
            for user in User.objects.filter(role=User.Role.DATA_MINING)
            if not Ticket.objects.filter(
                Q(assign_name=(user.get_full_name() or user.username).strip())
                | Q(assign_name_lx2=(user.get_full_name() or user.username).strip())
            ).exists()
            and (user.get_full_name() or user.username).strip()
            not in ACCOUNT_RENAMES
        ]

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f"DRY RUN. Would change {len(undo)} ticket(s), rename "
                f"{len(renames) + len(derived_renames)} account(s) and deactivate "
                f"{sum(len(l) for _, l, _ in merges)}. Nothing written."
            ))
        else:
            # The undo file is written and flushed BEFORE the transaction, so a
            # rewrite can never land without a way back.
            with open(undo_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "created": datetime.now().isoformat(timespec="seconds"),
                    "account_renames": [
                        {"id": u.id, "from": o, "to": n}
                        for u, o, n, _, _ in renames
                    ] + [
                        {"id": u.id, "from": o, "to": n}
                        for u, o, n in derived_renames
                    ],
                    "account_deactivations": [
                        {"id": u.id,
                         "name": (u.get_full_name() or u.username).strip(),
                         "status": u.status, "login_access": u.login_access,
                         "duplicate_of": keeper.id}
                        for keeper, losers, _ in merges for u in losers
                    ],
                    "tickets": undo,
                }, fh, indent=1)
            with transaction.atomic():
                Ticket.objects.bulk_update(edits, COLUMNS, batch_size=1000)
                for user, _, _, first, last in renames:
                    user.first_name, user.last_name = first, last
                    user.role_is_explicit = True
                    user.save(update_fields=["first_name", "last_name"])
                for user, _stored, canon in derived_renames:
                    first, _, last = canon.partition(" ")
                    user.first_name, user.last_name = first, last
                    user.role_is_explicit = True
                    user.save(update_fields=["first_name", "last_name"])
                deactivated = 0
                for _keeper, losers, _canon in merges:
                    for loser in losers:
                        # status only. User.save syncs is_active from it, which
                        # is what takes the duplicate out of the dropdown, and
                        # login_access is left as it was so the undo file is
                        # enough to put it back.
                        loser.status = "inactive"
                        loser.role_is_explicit = True
                        loser.save(update_fields=["status", "is_active"])
                        deactivated += 1
            self.stdout.write(self.style.SUCCESS(
                f"Changed {len(undo)} ticket(s), renamed "
                f"{len(renames) + len(derived_renames)} account(s), deactivated "
                f"{deactivated} duplicate(s). Undo file: {undo_path}"
            ))

        if unmatched:
            self.stdout.write(self.style.WARNING(
                "Data Mining accounts matching no assignee name, left alone: "
                + ", ".join(sorted(unmatched))
                + ". Either they have raised no tickets yet, or the ticket data "
                  "names them differently."
            ))

        logger.info(
            "normalize_dmd_assignees: dry_run=%s tickets=%d standalone=%d "
            "combos=%d renames=%d unmatched=%d",
            dry_run, len(undo), standalone, combos, len(renames), len(unmatched),
        )
