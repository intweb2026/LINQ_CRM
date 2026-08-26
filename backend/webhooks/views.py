"""
webhooks/views.py
──────────────────
POST /api/webhooks/ingest/          — live booking ingestion  (X-CRM-API-KEY or X-WEBHOOK-SECRET)
GET  /api/webhooks/ingest/          — liveness check, same credentials, writes no log
                                      The key is accepted in the X-CRM-API-KEY header or as an
                                      X-CRM-API-KEY query parameter; the header takes priority.
GET  /api/webhooks/logs/            — paginated log list       (admin only)
GET  /api/webhooks/logs/{id}/       — full log detail          (admin only)
POST /api/webhooks/logs/{id}/retry/ — re-process a failed log  (admin only)
GET  /api/webhooks/keys/            — list API keys            (admin only)
POST /api/webhooks/keys/            — create API key           (admin only)
PATCH/DELETE /api/webhooks/keys/{id}/         — update / delete
POST /api/webhooks/keys/{id}/regenerate/      — regenerate secret
"""
import logging
import traceback
from django.db.models import F, IntegerField, Q, Value
from django.db.models.functions import Coalesce, Round
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.filter_spec import FilterSpecMixin, build_filter_spec_fields
from accounts.permissions import IsAdminRole, IsHPAccount
from .models import WebhookApiKey, WebhookLog
from .parsers import AnyTypeJSONParser
from .serializers import (
    WebhookApiKeySerializer, WebhookApiKeyCreateSerializer,
    WebhookLogSerializer, WebhookLogListSerializer,
)
from .services import WebhookProcessor
from .utils import (
    authenticate_request, coerce_form_wrapped_json, extract_api_key,
    extract_ip, key_transport, looks_like_a_key, safe_headers, unwrap_payload,
)

logger = logging.getLogger(__name__)


# ── Ingestion ─────────────────────────────────────────────────────────────────

class WebhookIngestionView(APIView):
    """
    POST /api/webhooks/ingest/  is a delivery.
    GET  /api/webhooks/ingest/  is a liveness check.

    Accepts X-CRM-API-KEY (DB-backed) or X-WEBHOOK-SECRET (legacy static). The
    API key may arrive in the header or in the query string; the header wins.

    Every POST outcome leaves exactly one WebhookLog row — success, auth
    failure, unparseable body, and unexpected crash alike. A delivery that
    produced no row is indistinguishable in the UI from a delivery that never
    arrived, so the failures most worth investigating were the ones with
    nothing to show.
    """
    authentication_classes = []
    permission_classes     = [AllowAny]

    # Order is load-bearing. DRF selects the FIRST parser whose media_type
    # matches, and AnyTypeJSONParser declares "*/*", which matches everything,
    # so it must sit last and be reached only once the specific parsers have
    # each declined the media type they own. Put it anywhere earlier and every
    # form and multipart upload would be read as JSON and fail.
    parser_classes = [JSONParser, FormParser, MultiPartParser, AnyTypeJSONParser]

    # How much of an unparseable body to keep. Enough to see what the sender
    # actually sent, short enough not to bloat the row.
    RAW_BODY_LOG_LIMIT = 10_000

    # Appended to WebhookLog.source when the key arrived in the URL, so a
    # URL-key delivery is identifiable in the logs UI without opening the row.
    URL_AUTH_SUFFIX = " [url-auth]"

    # Read off the column rather than hardcoded: WebhookApiKey.name and
    # WebhookLog.source are both max_length 100, so a 100-character key name
    # plus the suffix overflows by exactly the length of the suffix.
    SOURCE_MAX = WebhookLog._meta.get_field("source").max_length

    @classmethod
    def _stamp_source(cls, base, transport):
        """Fit `base` into WebhookLog.source, marking a URL-carried key."""
        base = (base or "").strip()
        if transport == "query":
            # With no base to append to, the separating space has nothing to
            # separate. A cell holding whitespace then a tag reads in the logs
            # table as a rendering fault rather than as the value it is.
            if not base:
                return cls.URL_AUTH_SUFFIX.strip()
            room = cls.SOURCE_MAX - len(cls.URL_AUTH_SUFFIX)
            return base[:room] + cls.URL_AUTH_SUFFIX
        return base[:cls.SOURCE_MAX]

    def get(self, request):
        """
        Liveness check: same credentials, no side effects.

        Deliberately creates no WebhookLog row and does not bump usage_count or
        last_used_at. A GET is not a delivery, and the log is a record of
        deliveries, so a browser prefetch, a link preview or a monitoring ping
        would otherwise appear in Delivery logs as traffic that never happened,
        and would inflate the usage figure the keys page reports.

        A failed credential check must not be completely silent either, though,
        because it leaves no row for anyone to find. So the 401 branch writes a
        warning to the application log, carrying the transport, the client IP,
        and a 12-character prefix of the attempted key. The prefix is enough to
        tell a mistyped key from a stale one when the tester reads the value
        back; the whole value is never logged, since an application log is one
        of the places a URL-carried key is already too easy to find.
        """
        api_key_obj, auth_err = authenticate_request(request, record_usage=False)
        if auth_err:
            attempted, transport = extract_api_key(request)
            # 12 characters is a prefix, not a key. Every key issued here starts
            # with the 9-character "crm_live_", so this shows 3 characters of
            # the secret, enough to match against what the tester has in front
            # of them and useless to anyone else.
            key_hint = f"{attempted[:12]}..." if attempted else "none"
            logger.warning(
                "Webhook liveness check rejected, transport=%s ip=%s key=%s reason=%s",
                transport or "none", extract_ip(request) or "unknown",
                key_hint, auth_err,
            )
            return Response({"success": False, "error": auth_err},
                            status=status.HTTP_401_UNAUTHORIZED)

        return Response({
            "success":   True,
            "message":   "Webhook endpoint is live. POST your JSON booking payload to this same URL.",
            "key_name":  api_key_obj.name if api_key_obj else "legacy-secret",
            "transport": key_transport(request),
        }, status=status.HTTP_200_OK)

    def post(self, request):
        # Bound FIRST, before anything that can raise. The crash handler at the
        # bottom reads `transport`, and it is the handler of last resort; if a
        # crash landed before the real assignment further down, that read would
        # raise UnboundLocalError inside the handler itself and the 500 would go
        # unrecorded, which is precisely the outcome the handler exists to
        # prevent. The real value is still computed once, in its own place below.
        transport = ""

        recv_at = timezone.now()
        ip      = extract_ip(request)
        hdrs    = safe_headers(request.META)
        log     = None

        # Read the raw body BEFORE request.data. Django caches it on the request
        # and DRF parses from that cache, so this is safe — and it is the only
        # order that works: once the parser has drained the stream, request.body
        # raises RawPostDataException and the bytes are gone for good.
        try:
            raw_body = request.body.decode("utf-8", errors="replace")
        except Exception:
            raw_body = ""

        # Parse without raising. A malformed body used to blow up inside the
        # log-creating call itself, so the blanket handler below turned it into
        # a 500 with no row at all.
        try:
            parsed      = request.data
            data        = parsed if isinstance(parsed, dict) else {}
            # Normalise BEFORE anything reads it. What is stored as
            # WebhookLog.payload is what a later retry re-processes, so an
            # un-normalised row would fail again on retry for a reason that no
            # longer exists.
            data        = coerce_form_wrapped_json(data)
            parse_error = None
        except Exception as exc:
            data        = {}
            parse_error = f"Could not parse request body: {exc}"

        # Which carrier the key arrived on. Computed once, before authentication,
        # because the auth-failure branch needs it too.
        transport = key_transport(request)

        try:
            # Authenticate first even when the body is broken: an unauthenticated
            # sender should not be able to write its raw body into our logs.
            api_key_obj, auth_err = authenticate_request(request)

            if auth_err:
                # NAMES ONLY, never values, from either source.
                #
                # Query parameter names are recorded nowhere else on the row.
                # QUERY_STRING is not an HTTP_ key, so the prefix filter in
                # safe_headers excludes it, and a sender that put its key under
                # a parameter name we do not accept therefore leaves no trace of
                # what that name was. Header names survive the same way even
                # when the header itself was skipped as a secret. This is what
                # turns a failed integration into a one-line fix rather than
                # another round of asking the sender what they sent.
                auth_debug = {
                    "header_names": sorted(
                        k[len("HTTP_"):] for k in request.META if k.startswith("HTTP_")
                    ),
                    "query_param_names": sorted(request.query_params.keys()),
                    "key_shaped_value_seen": (
                        any(looks_like_a_key(v) for k, v in request.META.items()
                            if k.startswith("HTTP_"))
                        or any(looks_like_a_key(v) for v in request.query_params.values())
                    ),
                }
                WebhookLog.objects.create(
                    source=self._stamp_source("", transport),
                    ip_address=ip, payload=data, headers=hdrs,
                    response={"error": auth_err, "_auth_debug": auth_debug},
                    status=WebhookLog.Status.FAILED,
                    http_status=401,
                    error_message=auth_err,
                    processing_status=WebhookLog.ProcessingStatus.ERROR,
                    received_at=recv_at,
                )
                return Response({"success": False, "error": auth_err}, status=status.HTTP_401_UNAUTHORIZED)

            if parse_error:
                WebhookLog.objects.create(
                    api_key=api_key_obj,
                    # Stamped for the same reason as the other two, but this row
                    # earns it most. A URL sender authenticates on the first try
                    # and then gets the Content-Type or the body shape wrong, so
                    # this is the row an operator is most often staring at while
                    # diagnosing a failed URL test, and the stamp is what tells
                    # them the URL itself worked.
                    source=self._stamp_source(
                        api_key_obj.name if api_key_obj else "legacy-secret",
                        transport,
                    ),
                    ip_address=ip,
                    payload={"_unparsed_body": raw_body[:self.RAW_BODY_LOG_LIMIT]},
                    headers=hdrs,
                    response={"success": False, "error": parse_error},
                    status=WebhookLog.Status.FAILED,
                    http_status=400,
                    error_message=parse_error,
                    processing_status=WebhookLog.ProcessingStatus.ERROR,
                    received_at=recv_at,
                    processed_at=timezone.now(),
                )
                return Response({"success": False, "error": parse_error},
                                status=status.HTTP_400_BAD_REQUEST)

            payload        = unwrap_payload(data)
            invoice_number = payload.get("InvoiceNumber", "")
            event_code     = payload.get("Eventcode", "")
            event_name     = payload.get("Eventname", "")
            source         = self._stamp_source(
                request.META.get("HTTP_X_WEBHOOK_SOURCE", "")
                or (api_key_obj.name if api_key_obj else "legacy-secret"),
                transport,
            )

            log = WebhookLog.objects.create(
                api_key=api_key_obj,
                source=source,
                ip_address=ip,
                request_method="POST",
                payload=data,
                headers=hdrs,
                response={},
                status=WebhookLog.Status.RECEIVED,
                http_status=202,
                invoice_number=invoice_number,
                event_code=event_code,
                event_name=event_name,
                processing_status=WebhookLog.ProcessingStatus.PENDING,
                received_at=recv_at,
            )

            processor       = WebhookProcessor(log)
            success, result = processor.process()

            log.refresh_from_db()
            resp_body = {"success": success, "log_id": log.id, **result}
            log.response = resp_body
            log.save(update_fields=["response"])

            if success:
                resp_status = status.HTTP_201_CREATED if result.get("db_action") == "inserted" else status.HTTP_200_OK
            elif log.status == WebhookLog.Status.DUPLICATE:
                resp_status = status.HTTP_409_CONFLICT
            elif log.http_status in (400, 409):
                # 409 is an ambiguous event code — two open editions matched and
                # the resolver refuses to guess between them. Honour the status
                # the processor decided rather than flattening it to 400/500.
                resp_status = log.http_status
            else:
                resp_status = status.HTTP_500_INTERNAL_SERVER_ERROR

            return Response(resp_body, status=resp_status)
        except Exception as e:
            logger.exception("CRITICAL Webhook Ingestion Failure")
            detail    = f"{type(e).__name__}: {e}"
            resp_body = {"success": False, "error": "Internal Server Error", "detail": detail}

            # A crash used to return 500 having written nothing, or having left
            # the row parked in `processing` forever — both of which read as "no
            # such delivery" in the logs UI. Record the outcome on the row we
            # already have, or make one if we crashed before creating it.
            try:
                if log is not None:
                    log.status            = WebhookLog.Status.FAILED
                    log.processing_status = WebhookLog.ProcessingStatus.ERROR
                    log.http_status       = 500
                    log.error_message     = detail
                    log.stack_trace       = traceback.format_exc()
                    log.response          = resp_body
                    log.processed_at      = timezone.now()
                    log.save(update_fields=[
                        "status", "processing_status", "http_status",
                        "error_message", "stack_trace", "response", "processed_at",
                    ])
                    resp_body["log_id"] = log.id
                else:
                    crash_log = WebhookLog.objects.create(
                        # This row exists because the request crashed before the
                        # main row did, so it carries no key name and no resolved
                        # booking. The transport is the only thing on it that
                        # says how the sender was authenticating, which makes it
                        # the only clue tying the crash to a URL-based test.
                        source=self._stamp_source("", transport),
                        ip_address=ip,
                        payload=data,
                        headers=hdrs,
                        response=resp_body,
                        status=WebhookLog.Status.FAILED,
                        http_status=500,
                        error_message=detail,
                        stack_trace=traceback.format_exc(),
                        processing_status=WebhookLog.ProcessingStatus.ERROR,
                        received_at=recv_at,
                        processed_at=timezone.now(),
                    )
                    resp_body["log_id"] = crash_log.id
            except Exception:
                # Recording the failure must never replace the failure.
                logger.exception("Could not write a WebhookLog row for the failure above")

            return Response(resp_body, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ── Webhook Logs ──────────────────────────────────────────────────────────────

class WebhookLogViewSet(FilterSpecMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminRole]

    # FilterSpecMixin so the Webhook Logs table can filter and page
    # server-side. This table is 130,287 rows: loading it all took ~261
    # sequential requests at page_size=500, which is not a slow page, it is a
    # hung one. `payload`, `headers`, `response` and `stack_trace` are excluded
    # — they are large text blobs and filtering them would be a table scan
    # over megabytes per row.
    filter_spec_fields = {
        **build_filter_spec_fields(
            WebhookLog,
            exclude={"payload", "headers", "response", "stack_trace",
                     "api_key", "created_booking"},
            labels={"invoice_number": "Invoice Number", "event_code": "Event Code",
                    "db_insert_status": "DB Insert Status"},
        ),
        # ── The three columns the list builds rather than stores ──────────────
        # api/webhooks.js derives all three per row, so none of them is a column
        # build_filter_spec_fields could find, and each was therefore filtered in
        # the browser over the loaded page. On 130,287 deliveries that is a
        # filter answering from whatever the last scroll fetched.
        #
        # Each mirrors its line in api/webhooks.js exactly:
        #   api_key_name  the related key's name (serializers.get_api_key_name)
        #   records       records_inserted + records_updated
        #   duration_ms   processing_duration is SECONDS as a float; the cell
        #                 shows milliseconds, rounded, and 0 where the delivery
        #                 never recorded one — so the filter is written in
        #                 milliseconds too, or "duration_ms gt 500" would compare
        #                 against a number nobody has seen.
        "api_key_name": {"type": "text", "label": "API Key",
                         "source": "api_key__name"},
        "records": {"type": "number", "label": "Records",
                    "expression": lambda: F("records_inserted") + F("records_updated")},
        "duration_ms": {"type": "number", "label": "Duration (ms)",
                        "expression": lambda: Coalesce(
                            Round(F("processing_duration") * Value(1000.0)),
                            Value(0),
                            output_field=IntegerField(),
                        )},
    }
    # Explicit rather than inherited: DRF silently drops an unrecognised
    # ordering term, so anything the frontend may ask for has to be named.
    ordering_fields = ["id", "received_at", "created_at", "status",
                       "processing_status", "db_insert_status", "event_code",
                       "invoice_number", "http_status", "retry_count"]
    ordering = ["-created_at"]

    def get_serializer_class(self):
        return WebhookLogSerializer if self.action == "retrieve" else WebhookLogListSerializer

    def get_queryset(self):
        qs = WebhookLog.objects.select_related("created_booking", "api_key")

        if st := self.request.query_params.get("status"):
            qs = qs.filter(status=st)
        if ps := self.request.query_params.get("processing_status"):
            qs = qs.filter(processing_status=ps)
        if ds := self.request.query_params.get("db_insert_status"):
            qs = qs.filter(db_insert_status=ds)
        if ev := self.request.query_params.get("event_code"):
            qs = qs.filter(event_code=ev)
        if ak := self.request.query_params.get("api_key"):
            qs = qs.filter(api_key_id=ak)

        if search := self.request.query_params.get("search", "").strip():
            qs = qs.filter(
                Q(invoice_number__icontains=search) |
                Q(event_code__icontains=search)     |
                Q(source__icontains=search)         |
                Q(ip_address__icontains=search)
            )

        return qs

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """POST /api/webhooks/logs/{id}/retry/"""
        original = self.get_object()

        if original.status == WebhookLog.Status.SUCCESS:
            return Response(
                {"error": "Cannot retry a successful webhook."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        retry_log = WebhookLog.objects.create(
            api_key=original.api_key,
            source=original.source,
            ip_address=original.ip_address,
            request_method=original.request_method,
            payload=original.payload,
            headers=original.headers,
            response={},
            status=WebhookLog.Status.RECEIVED,
            http_status=202,
            invoice_number=original.invoice_number,
            event_code=original.event_code,
            event_name=original.event_name,
            retry_count=original.retry_count + 1,
            processing_status=WebhookLog.ProcessingStatus.PENDING,
            received_at=timezone.now(),
        )

        original.retry_count += 1
        original.save(update_fields=["retry_count"])

        processor       = WebhookProcessor(retry_log)
        success, result = processor.process()

        retry_log.refresh_from_db()
        resp_body          = {"success": success, "log_id": retry_log.id, **result}
        retry_log.response = resp_body
        retry_log.save(update_fields=["response"])

        resp_status = status.HTTP_200_OK if success else status.HTTP_422_UNPROCESSABLE_ENTITY
        return Response({"success": success, "retry_log_id": retry_log.id, **result}, status=resp_status)


# ── API Key Management ────────────────────────────────────────────────────────

class WebhookApiKeyViewSet(viewsets.ModelViewSet):
    """
    The website's ingest credentials, at /api/webhooks/keys/.

    HP ONLY, and for the same reason as the Data API keys next door (see
    dataapi/views.py DataApiKeyManagementViewSet): this is a credential surface,
    not a data one. It is if anything the sharper of the two, because these keys
    are WRITE — a holder posts bookings straight into the CRM — and the list
    serves the key string itself in the clear on every read, so being able to
    list is being able to use. Regenerate and toggle are on the same viewset and
    can silently break a live website integration.

    That is why the audience is a named account rather than IsAdminRole, which
    admitted every admin, every is_all_access team, and HP. The Webhooks page
    still opens for anyone holding the module — only its API keys tab is gated,
    since delivery logs are operational data that the people running the site
    need. Note the LOGS viewset above deliberately keeps IsAdminRole.
    """
    permission_classes = [IsHPAccount]
    queryset = WebhookApiKey.objects.select_related("created_by").all()

    def get_serializer_class(self):
        if self.action == "create":
            return WebhookApiKeyCreateSerializer
        return WebhookApiKeySerializer

    def get_queryset(self):
        qs = WebhookApiKey.objects.select_related("created_by")
        if active := self.request.query_params.get("is_active"):
            qs = qs.filter(is_active=active.lower() == "true")
        if search := self.request.query_params.get("search", "").strip():
            qs = qs.filter(Q(name__icontains=search) | Q(event__icontains=search))
        return qs

    @action(detail=True, methods=["post"])
    def regenerate(self, request, pk=None):
        """POST /api/webhooks/keys/{id}/regenerate/ — issue a new key string."""
        api_key = self.get_object()
        new_key = api_key.regenerate()
        return Response({"api_key": new_key, "id": api_key.id})

    @action(detail=True, methods=["post"])
    def toggle(self, request, pk=None):
        """POST /api/webhooks/keys/{id}/toggle/ — flip is_active."""
        api_key          = self.get_object()
        api_key.is_active = not api_key.is_active
        api_key.save(update_fields=["is_active"])
        return Response({"id": api_key.id, "is_active": api_key.is_active})
