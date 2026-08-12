"""
accounts/user_resolution.py
────────────────────────────
Resolve a free-text person reference on an imported row to a real `User`.

THE BUG THIS FIXES
book_event/views.py resolved the sales executive with a chain ending in

    User.objects.filter(first_name__icontains=parts[0],
                        last_name__icontains=parts[-1]).first()

Substring matching on names, decided by `.first()`. Two ways that silently
attributes a booking to the wrong person: a short first name is a substring of a
longer one ("Ana" matches "Anastasia"), and where several users match, the winner
is whichever row Postgres returned first — not a decision, an accident. An
unresolved row then became a silent NULL, so the failure never surfaced as
anything except a number that looked low.

This is the documented failure mode of this codebase. paper_review/notifications.py
records the same trap for Event.speaker_sales_team, and events/models.py:112
matches sales_team by icontains against first/last name. A related field held
email addresses while the querying code matched display names, and every count
read zero.

THE RULE — EXACT ONLY, MOST RELIABLE KEY FIRST
    1. email            (iexact)  — the only globally unique, typo-evident key
    2. username         (iexact)  — including the "first.last" convention
    3. full name        (iexact on first_name + last_name, both sides)
No substring tier, and no `.first()` on an ambiguous set: two users matching the
same name is AMBIGUOUS and is reported, not silently resolved to one of them.

UNRESOLVED IS AN OUTCOME, NOT A NULL
Every miss is recorded with the raw value verbatim and a reason, so a caller can
report the resolution RATE. Every name on a booking is expected to correspond to
a real account, so anything materially below 100% is a defect to investigate
before the data is trusted — not "missing data" to be shrugged at.

WHY A CLASS
A 35,690-row load resolving per row would issue three queries per row. The
resolver loads every user once into dictionaries and answers from memory.
"""
from collections import Counter


# Reasons a value did not resolve. Reported verbatim so the operator can act.
NO_MATCH = "no_match"
AMBIGUOUS = "ambiguous"
EMPTY = "empty"


def _clean(value):
    return str(value or "").strip()


class UserResolver:
    """
    Build once, call resolve() per row, then read the stats.

    Deliberately does NOT cache across instances: a load that ran before a user
    was created must not be reproducible only by restarting the process.
    """

    def __init__(self, users=None):
        from accounts.models import User

        self.users = list(users if users is not None else User.objects.all())

        self._by_email = {}
        self._by_username = {}
        self._by_fullname = {}

        for user in self.users:
            email = _clean(user.email).lower()
            if email:
                self._by_email.setdefault(email, []).append(user)

            username = _clean(user.username).lower()
            if username:
                self._by_username.setdefault(username, []).append(user)

            full = " ".join(
                p for p in (_clean(user.first_name), _clean(user.last_name)) if p
            ).lower()
            if full:
                self._by_fullname.setdefault(full, []).append(user)

        # value -> reason, for reporting. Counter so a value appearing on 400 rows
        # is reported once with its weight rather than 400 times.
        self.unresolved = Counter()
        self.unresolved_reason = {}
        self.resolved_count = 0
        self.attempted_count = 0

    # ── lookup ──────────────────────────────────────────────────────────────
    def _lookup(self, table, key):
        """(user|None, reason|None). Several hits is ambiguous, never .first()."""
        hits = table.get(key)
        if not hits:
            return None, None
        if len(hits) > 1:
            return None, AMBIGUOUS
        return hits[0], None

    def resolve(self, value):
        """
        Resolve one raw value. Returns (User|None, reason|None); reason is None
        exactly when a user was found.
        """
        raw = _clean(value)
        if not raw:
            return None, EMPTY

        self.attempted_count += 1
        lowered = raw.lower()

        for table in (self._by_email, self._by_username, self._by_fullname):
            user, reason = self._lookup(table, lowered)
            if user is not None:
                self.resolved_count += 1
                return user, None
            if reason == AMBIGUOUS:
                self._record(raw, AMBIGUOUS)
                return None, AMBIGUOUS

        # "Victor Venegas" written as the username "victor.venegas".
        dotted = lowered.replace(" ", ".")
        user, reason = self._lookup(self._by_username, dotted)
        if user is not None:
            self.resolved_count += 1
            return user, None
        if reason == AMBIGUOUS:
            self._record(raw, AMBIGUOUS)
            return None, AMBIGUOUS

        self._record(raw, NO_MATCH)
        return None, NO_MATCH

    def _record(self, raw, reason):
        self.unresolved[raw] += 1
        self.unresolved_reason[raw] = reason

    # ── reporting ───────────────────────────────────────────────────────────
    @property
    def unresolved_rows(self):
        return sum(self.unresolved.values())

    @property
    def resolution_rate(self):
        """
        Fraction of NON-EMPTY values that resolved, 0.0-1.0. Empty values are
        excluded from the denominator: a booking with no sales executive named is
        not a resolution failure, and folding it in would flatter the number.

        None when nothing non-empty was attempted — distinct from 0.0, which
        means "every value present failed".
        """
        if not self.attempted_count:
            return None
        return self.resolved_count / self.attempted_count

    def report(self, limit=None):
        """
        Serialisable summary. `limit` caps the listed values for a console
        summary; the count is always the true total, never the truncated one, and
        `truncated` says so explicitly.
        """
        items = self.unresolved.most_common()
        shown = items if limit is None else items[:limit]
        return {
            "attempted": self.attempted_count,
            "resolved": self.resolved_count,
            "unresolved_rows": self.unresolved_rows,
            "unresolved_distinct": len(self.unresolved),
            "resolution_rate": self.resolution_rate,
            "truncated": limit is not None and len(items) > limit,
            "unresolved_values": [
                {"value": value, "rows": count,
                 "reason": self.unresolved_reason.get(value, NO_MATCH)}
                for value, count in shown
            ],
        }
