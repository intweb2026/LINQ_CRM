"""
attendance/qr.py
─────────────────
What is inside a badge, and how little it is trusted.

A badge carries a signed triple, "<type>:<id>:<event code>", wrapped by
django.core.signing under a purpose-scoped salt and prefixed LINQ1: so a future
format can be told from this one rather than guessed at.

MINTING IS DETERMINISTIC, and that is load-bearing rather than incidental.
One person on one event has ONE badge string, for ever, so generating the
codes for an event twice produces byte-identical images and there is no such
thing as a duplicate badge to reconcile. A reprint matches the card already in
somebody's pocket, and two differently-shaped codes for one delegate can never
both be in circulation.

That is why this uses signing.Signer and not signing.dumps. dumps wraps a
TimestampSigner, so it mints a DIFFERENT string every call; both strings would
scan to the same person, so nothing would break, but "regenerate" would quietly
mean "issue a second, unequal badge" and the sheet could not promise otherwise.
Dropping the timestamp also shortens the payload from 92 characters to 74, which
takes the QR from version 6 down to version 5; a less dense code is an easier
code to read at a door.

Nothing expires. loads() was called without max_age, so the timestamp was never
enforced in the first place and none is lost by removing it.

DECODING IS NOT THE ACCESS DECISION. Everything read here is a CLAIM. The
database decides whether that person exists, is on the confirmed roster, and is
on the event the door is working; see views.scan, which asks in that order and
never trusts the event code inside the payload. So a valid signature buys
exactly one thing, confidence that the numbers were not typed by hand.

THREE ACCEPTED SHAPES, tried in this order:

    LINQ1:<signed>          what this module mints
    <type>:<id>:<code>      a bare triple, unsigned
    {"type":…,"id":…,…}     JSON, long or short keys

The bare triple is deliberate rather than a loophole. Badges get printed by
whoever prints badges, and a third-party badge printer cannot compute our HMAC.
Accepting the plain triple means an event can run on badges we did not produce,
and costs nothing, because the signature was never the gate.
"""
import json

from django.core import signing

# Purpose scoped: a token minted here cannot be replayed against any other
# signing.loads in the codebase, and vice versa.
SALT = "attendance.badge.v1"
PREFIX = "LINQ1:"

# Cap BEFORE parsing. A QR code can hold a couple of kilobytes and a camera will
# happily hand over every byte of it; nothing legitimate here is longer than a
# signed triple, so the parse never sees an oversized string.
MAX_LEN = 512

VALID_TYPES = ("delegate", "speaker")


def _signer():
    return signing.Signer(salt=SALT)


def mint(delegate_id, event_code, attendee_type="delegate"):
    """
    The string that goes into a printed QR code. Same inputs, same output, always.

    Signer rather than dumps; see the module docstring for why that matters.
    Signer.sign appends ":<signature>" and Signer.unsign takes it off with an
    rsplit, so a colon inside the event code is safe in both directions.
    """
    payload = f"{attendee_type}:{int(delegate_id)}:{(event_code or '').strip()}"
    return PREFIX + _signer().sign(payload)


def read(raw):
    """
    {"type", "id", "event"} from a scanned payload, or None.

    None means "unreadable", and it is deliberately the SAME answer for a
    tampered token, a token signed with another key, a truncated one and a
    scribble. Distinguishing them would tell whoever is holding the camera which
    part of the format they got right.
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > MAX_LEN:
        return None

    if raw.startswith(PREFIX):
        return _read_signed(raw[len(PREFIX):])
    return _parse(raw)


def _read_signed(signed):
    """The inside of a LINQ1 token, verified, or None."""
    try:
        value = _signer().unsign(signed)
    except signing.BadSignature:
        return None

    found = _parse(value)
    if found:
        return found

    # ponytail: a compatibility path, and a deletable one. It reads a badge
    # minted by the old signing.dumps form, before minting became
    # deterministic. Worth keeping only because a badge is a PRINTED artifact
    # and nobody controls what is already in a drawer; drop it once no card
    # from before that change can still turn up at a door.
    #
    # The unsign above ACCEPTS such a token, because dumps signs with the same
    # key and salt through a TimestampSigner. What it hands back is base64 JSON
    # plus a timestamp rather than the triple, so _parse returns None on it and
    # loads() is what finishes the job.
    try:
        legacy = signing.loads(signed, salt=SALT)
    except signing.BadSignature:
        return None
    return _parse(legacy) if isinstance(legacy, str) else None


def _parse(value):
    """An unwrapped payload in either accepted shape, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.startswith("{"):
        return _from_json(value)
    return _from_triple(value)


def _from_triple(raw):
    # maxsplit=2, because an event code may itself contain a colon and the
    # rest of the string is the code, not a fourth field.
    parts = raw.split(":", 2)
    if len(parts) != 3:
        return None
    return _normalise(parts[0], parts[1], parts[2])


def _from_json(raw):
    try:
        body = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    return _normalise(
        body.get("type", body.get("t")),
        body.get("id", body.get("i")),
        body.get("event", body.get("e")),
    )


def _normalise(attendee_type, ident, event_code):
    """The three claims, checked for shape only."""
    # isinstance(x, bool) FIRST. True is an int in Python, so int(True) is 1 and
    # a payload of {"id": true} would otherwise read as delegate number one.
    if isinstance(ident, bool):
        return None
    try:
        ident = int(str(ident).strip())
    except (TypeError, ValueError):
        return None
    # A row id is positive. Zero and negatives cannot match anything, and
    # letting them through only widens what the lookup below has to consider.
    if ident <= 0:
        return None

    if not isinstance(attendee_type, str) or not isinstance(event_code, str):
        return None
    attendee_type = attendee_type.strip().lower()
    if attendee_type not in VALID_TYPES:
        return None
    event_code = " ".join(event_code.split())
    if not event_code:
        return None

    return {"type": attendee_type, "id": ident, "event": event_code}
