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
                f"DRY RUN. Would change {len(undo)} ticket(s) and rename "
                f"{len(renames)} account(s). Nothing written."
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
                    ],
                    "tickets": undo,
                }, fh, indent=1)
            with transaction.atomic():
                Ticket.objects.bulk_update(edits, COLUMNS, batch_size=1000)
                for user, _, _, first, last in renames:
                    user.first_name, user.last_name = first, last
                    user.role_is_explicit = True
                    user.save(update_fields=["first_name", "last_name"])
            self.stdout.write(self.style.SUCCESS(
                f"Changed {len(undo)} ticket(s), renamed {len(renames)} "
                f"account(s). Undo file: {undo_path}"
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
