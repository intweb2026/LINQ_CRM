"""
webhooks/parsers.py
────────────────────
AnyTypeJSONParser, the last-resort body parser for the ingest endpoint.
"""
import json

from django.conf import settings
from rest_framework.exceptions import ParseError
from rest_framework.parsers import BaseParser


class AnyTypeJSONParser(BaseParser):
    """
    Read the request body as JSON whatever the sender declared it to be.

    This is registered LAST on the ingest view, after JSONParser, FormParser and
    MultiPartParser. DRF picks the first parser whose media_type matches, and
    "*/*" matches everything, so anything earlier in the list still wins for the
    media types it owns; this one is reached only once the specific parsers have
    all declined.

    Its only job is to replace a bare 415 Unsupported Media Type, which names no
    field, no offset and no fix, and so tells the sender nothing it can act on,
    with either a parsed body or a logged 400 that names the actual problem. A
    sender that posts good JSON under text/plain, or under no Content-Type at
    all, is making a header mistake rather than a data mistake, and a header
    mistake should not cost a booking.
    """

    media_type = "*/*"

    def parse(self, stream, media_type=None, parser_context=None):
        parser_context = parser_context or {}
        encoding = parser_context.get("encoding") or settings.DEFAULT_CHARSET
        try:
            return json.loads(stream.read().decode(encoding))
        except (ValueError, LookupError, UnicodeDecodeError) as exc:
            # Carry the underlying message through. "Expecting ',' delimiter,
            # line 1 column 34" is the whole of the sender's debugging; a bare
            # "malformed body" is another support round trip.
            raise ParseError(f"Could not parse request body as JSON: {exc}")
