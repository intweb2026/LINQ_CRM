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
records the same trap for the event team columns, and events/models.py:112
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


# ─────────────────────────────────────────────────────────────────────────────
# Owner columns on an event
# ─────────────────────────────────────────────────────────────────────────────
# Everything below is additive. UserResolver above is unchanged and every one of
# its callers keeps its exact behaviour; this extends it for the ONE field whose
# resolution decides row VISIBILITY rather than a display label.
#
# WHY THE THREE TIERS ABOVE NEEDED MORE
# Event.sales_executive is the FK that events/views.py get_queryset and
# accounts.User.assigned_event_codes scope a non admin by, so a name that fails
# to resolve does not merely leave a column blank, it makes every event that
# person owns invisible to them, on the Events page, on Bookings and in the New
# Booking dropdown, with nothing anywhere reporting a fault. Four accounts added
# in production hit exactly that.
#
# The three tiers above are right and are tried first. What they do not cover is
# the spelling variation a hand maintained SCA column actually contains; "Peck,
# Harrison", double spaces, a hyphen, a middle name on one side only, and the em
# dash NewEventModal writes for an unassigned owner. Each of those is still an
# EXACT match on a key the person genuinely goes by. None of them is a substring
# tier, and the rule that two candidates means AMBIGUOUS rather than a coin toss
# is untouched.

# Values that LOOK like a name but mean "nobody is assigned". Mirrors
# _BLANK_OWNER_VALUES in events/serializers.py and BLANK in
# frontend/src/lib/owners.js. Counted as EMPTY, not as a failure to resolve, so
# they do not depress a resolution rate an operator is meant to act on.
BLANK_NAME_VALUES = {"", "-", "–", "—", "n/a", "na", "none", "tbc", "tbd"}


def normalise_name(value):
    """
    The comparison form of a name. Case folded, separators reduced to spaces,
    whitespace collapsed, and "Last, First" rewritten as "First Last" so the two
    orders people type become one key.
    """
    import re

    text = _clean(value)
    if "," in text:
        parts = [p.strip() for p in text.split(",", 1)]
        if len(parts) == 2 and all(parts):
            text = parts[1] + " " + parts[0]
    text = re.sub(r"[._\-’']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.casefold().strip()


def is_blank_name(value):
    return normalise_name(value) in BLANK_NAME_VALUES


class OwnerResolver(UserResolver):
    """
    UserResolver, plus the key forms an event owner column actually holds.

    Only ACTIVE users are candidates by default. Deactivation in this product is
    `status`, which User.save() drives is_active from, so a leaver stops
    collecting events without anybody having to edit the events.

    `preferred_role` breaks a tie that would otherwise be ambiguous. The SCA
    column names a sales person, so where exactly one candidate is in sales, that
    is an answer rather than a coin toss. It is a TIE BREAK only; it never makes a
    non sales account unreachable, because User.save() derives role from the team
    NAME and a genuine sales person can end up labelled otherwise.
    """

    def __init__(self, users=None, preferred_role=None, active_only=True):
        from accounts.models import User

        if users is None:
            qs = User.objects.all()
            if active_only:
                qs = qs.filter(is_active=True)
            users = list(qs.order_by("id"))
        super().__init__(users=users)

        self.preferred_role = preferred_role or User.Role.SALES

        # Whole-name keys and single tokens are kept in SEPARATE tables, so a full
        # name can never be beaten by somebody else's first name.
        self._by_norm = {}
        self._by_token = {}

        for user in self.users:
            first = _clean(user.first_name)
            last = _clean(user.last_name)
            full = " ".join(p for p in (first, last) if p)

            keys = {normalise_name(user.username), normalise_name(user.email),
                    normalise_name(full)}
            if user.email and "@" in user.email:
                # Exports frequently carry "harrison.peck" where the CRM holds
                # "Harrison Peck".
                keys.add(normalise_name(user.email.split("@", 1)[0]))
            if first and last:
                keys.add(normalise_name(last + " " + first))

            for key in keys:
                if key and key not in BLANK_NAME_VALUES:
                    self._by_norm.setdefault(key, []).append(user)

            for token in {normalise_name(first), normalise_name(user.username)}:
                if token and token not in BLANK_NAME_VALUES:
                    self._by_token.setdefault(token, []).append(user)

    def _prefer(self, hits):
        """
        One user out of several, or None. Only a SINGLE preferred-role candidate
        counts; two sales people with one name is still ambiguous.
        """
        preferred = [u for u in hits if u.role == self.preferred_role]
        return preferred[0] if len(preferred) == 1 else None

    def _resolve_table(self, table, key):
        hits = table.get(key) or []
        if not hits:
            return None, None
        if len(hits) == 1:
            return hits[0], None
        chosen = self._prefer(hits)
        return (chosen, None) if chosen else (None, AMBIGUOUS)

    def resolve(self, value):
        """
        Resolve one owner name. Returns (User|None, reason|None), the same shape
        as UserResolver.resolve so a caller can treat both alike; reason is None
        exactly when a user was found.

        A placeholder resolves to (None, EMPTY), never to NO_MATCH. Ambiguity and
        an unknown name are both recorded for reporting, and callers MUST leave
        the field they were filling ALONE in both cases; overwriting a stored
        owner with a guess is the failure this module exists to prevent.
        """
        if is_blank_name(value):
            return None, EMPTY

        raw = _clean(value)
        self.attempted_count += 1
        query = normalise_name(raw)

        # The inherited tiers first, on the raw value, exactly as they were.
        for table in (self._by_email, self._by_username, self._by_fullname):
            user, reason = self._resolve_table(table, raw.lower())
            if user is not None:
                self.resolved_count += 1
                return user, None
            if reason == AMBIGUOUS:
                self._record(raw, AMBIGUOUS)
                return None, AMBIGUOUS

        # Then the same keys, normalised, which is what admits the spellings a
        # human actually types.
        user, reason = self._resolve_table(self._by_norm, query)
        if user is not None:
            self.resolved_count += 1
            return user, None
        if reason == AMBIGUOUS:
            self._record(raw, AMBIGUOUS)
            return None, AMBIGUOUS

        # "Victor Venegas" written as the username "victor.venegas".
        user, reason = self._resolve_table(self._by_username, query.replace(" ", "."))
        if user is not None:
            self.resolved_count += 1
            return user, None

        # Only now, with every whole-name key exhausted in every table, the looser
        # forms. A bare first name where exactly one person answers to it, and the
        # first and last token of a longer name where a middle name is present on
        # one side only. Both are unique or nothing; neither is a substring test.
        tokens = query.split()
        looser = []
        if len(tokens) == 1:
            looser.append((self._by_token, tokens[0]))
        elif len(tokens) > 2:
            looser.append((self._by_norm, tokens[0] + " " + tokens[-1]))

        for table, key in looser:
            user, reason = self._resolve_table(table, key)
            if user is not None:
                self.resolved_count += 1
                return user, None
            if reason == AMBIGUOUS:
                self._record(raw, AMBIGUOUS)
                return None, AMBIGUOUS

        self._record(raw, NO_MATCH)
        return None, NO_MATCH


def resolve_owner(value, resolver=None):
    """
    (User|None, reason|None) for one owner name.

    Builds a resolver per call when one is not supplied, which is right for a
    single save and wrong for a loop; pass an OwnerResolver when resolving a
    batch, so the user table is read once rather than once per row.
    """
    return (resolver or OwnerResolver()).resolve(value)
