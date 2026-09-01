"""
paper_review/public_form.py
────────────────────────────
The MRE form link. Two unauthenticated endpoints behind one shared secret:

  GET  /api/paper-review-form/config/?crm_key=<key>   what the form should render
  POST /api/paper-review-form/submit/?crm_key=<key>   one review, form semantics

`crm_key` is one of webhooks/utils.py's existing QUERY_KEY_ALIASES, and the key
may equally arrive in the X-CRM-API-KEY header. A shorter parameter name was not
added for this: that alias set deliberately excludes generic spellings, because a
generic name collides with a parameter a sender may already carry and a collision
is a 401 rather than a harmless miss.

WHAT THIS REPLACES
The Zoho public form URL that MREs filled in without a Zoho login. Same idea
here: the reviewer opens a link, fills the form, and never has a CRM account they
can sign into.

THE LINK IS A WebhookApiKey, target=PAPER_REVIEW_FORM, carrying `mre`
No second credential model, so issuing, revoking, regenerating, deactivating and
counting usage are the buttons that already exist on the keys page. `mre` is what
makes a link personal: the events the form offers, the scope the submission is
checked against, and the created_by stamped on the row all come from that one
user row.

FORM SEMANTICS, NOT IMPORT SEMANTICS — the difference worth stating, because the
sibling endpoint /api/webhooks/paper-review/ made the opposite choice for good
reasons of its own. A person filling in a form is exactly the case both Zoho ADD
workflows exist for, so this calls create_review_with_workflows: the
ProposalSubmission is minted and the production-team email is queued, the same as
a create from inside the CRM. The webhook stays as it is; a sender replaying a
backlog must not send mail per row.

internal_footnotes IS ON THIS FORM, for the reviewers allowed to write it
The link names one reviewer and the submission is saved AS that reviewer, so the
MR rule that governs the field inside the CRM governs it here too: config reports
show_internal = may_see_mr_fields(mre), the page renders the box only when that is
true, and PaperReviewSerializer.validate remains the authority on the write. A
link bound to somebody outside MR/Admin is therefore never shown a field their own
save would refuse. The submit response is hand-built and names no MR field, so
nothing is echoed back to a public browser either way.

WHAT THIS ENDPOINT DELIBERATELY DOES NOT INHERIT

  Full visibility. has_full_visibility() would hand a link bound to an admin, or
  to anyone with all-records on this module, the entire event catalogue and the
  right to file against any of it — publicly. The scope here is always the exact
  assigned-events set, so a link is never wider than the person it names, and a
  reviewer with no assigned events gets a refusal rather than everything.
"""
import logging

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from events.models import Event
from webhooks.models import WebhookApiKey
from webhooks.utils import authenticate_request, extract_ip

from .access import may_see_mr_fields, permitted_event_codes
from .models import CRITERIA, RUBRIC_TOTAL
from .serializers import PaperReviewSerializer
from .views import create_review_with_workflows

logger = logging.getLogger(__name__)

TARGET = WebhookApiKey.Target.PAPER_REVIEW_FORM


class FormThrottle(AnonRateThrottle):
    """
    Per-IP rate limit on the public pair. Rate in settings, scope named here.

    Its own scope rather than DRF's "anon": these are the only unauthenticated
    write endpoints in the project, and sharing a global bucket would mean a
    limit chosen for a form link silently applied to anything else that ever goes
    public. AnonRateThrottle keys on the client IP, which is the only stable
    identity an anonymous poster has; a shared office NAT therefore shares one
    bucket, which is why the rate is set well above what a sitting of reviews
    costs. The key itself is not the bucket on purpose — throttling per key would
    let one leaked link exhaust nothing but itself while the abuse continued from
    the same address on the next link.
    """
    scope = "paper_review_form"


class PaperReviewFormBase(APIView):
    """Shared link resolution. Neither endpoint has a session or a CSRF token."""

    authentication_classes = []
    permission_classes     = [AllowAny]
    throttle_classes       = [FormThrottle]

    def resolve_link(self, request):
        """
        (key, mre, codes, error_response). error_response is None on success.

        record_usage is left at its default on BOTH verbs, unlike the webhook
        liveness GET which deliberately counts nothing: here the GET is the form
        being opened, which is the only signal that a link is in use at all. A
        link nobody has opened and a link nobody has submitted through are
        different problems, and usage_count is where the difference shows.
        """
        api_key, err = authenticate_request(request, target=TARGET)
        if err or api_key is None:
            # api_key None with no error means the legacy static secret matched.
            # That secret is a server-to-server credential for the booking
            # webhook and names no reviewer, so it cannot open a personal form.
            logger.warning(
                "Paper review form link rejected, ip=%s reason=%s",
                extract_ip(request) or "unknown", err or "no key named",
            )
            return None, None, None, Response(
                {"detail": err or "This form link is not valid."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if api_key.target != TARGET or api_key.mre_id is None:
            return None, None, None, Response(
                {"detail": "This key is not a paper review form link."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        mre   = api_key.mre
        codes = sorted(set(permitted_event_codes(mre)))
        if not codes:
            return None, None, None, Response(
                {"detail": "No events are assigned to this reviewer yet. "
                           "Ask the CRM admin to assign the events you review."},
                status=status.HTTP_409_CONFLICT,
            )
        return api_key, mre, codes, None


class PaperReviewFormConfigView(PaperReviewFormBase):
    """
    GET /api/paper-review-form/config/?crm_key=<key>

    Everything the page needs to render itself and nothing else. No review rows,
    no user list, no event the reviewer is not assigned to: this response is
    public to anyone holding the link.
    """

    def get(self, request):
        api_key, mre, codes, error = self.resolve_link(request)
        if error:
            return error

        events = list(
            Event.objects.filter(event_code__in=codes)
            .order_by("event_code")
            .values("event_code", "name", "event_date")
        )
        # A code assigned to the reviewer whose Event row has since gone is not a
        # selectable option, but silently dropping it hides why the list is short.
        missing = sorted(set(codes) - {e["event_code"] for e in events})
        if missing:
            logger.warning(
                "Paper review form link %s: assigned codes absent from the "
                "catalogue: %s", api_key.name, missing,
            )

        return Response({
            "reviewer":     mre.get_full_name() or mre.username,
            "form_name":    api_key.name,
            # The MR-only footnotes box. Shown only to a link whose reviewer
            # would be allowed to write it — see the module docstring.
            "show_internal": may_see_mr_fields(mre),
            "events":       events,
            "rubric":       [{"field": f, "max": m} for f, m in CRITERIA],
            "rubric_total": RUBRIC_TOTAL,
        })


class PaperReviewFormSubmitView(PaperReviewFormBase):
    """
    POST /api/paper-review-form/submit/?crm_key=<key>

    One review per request. Runs both ADD workflows through the same helper the
    CRM form uses — see create_review_with_workflows.
    """

    def post(self, request):
        api_key, mre, codes, error = self.resolve_link(request)
        if error:
            return error

        # The scope check, made here rather than left to validate_event_code.
        # That validator grants everything to a full-visibility user, and a form
        # link must never be wider than the events its reviewer is assigned —
        # see the module docstring. Compared against the catalogue spelling the
        # config endpoint served, so a value the form offered always passes.
        submitted = str(request.data.get("event_code") or "").strip()
        if submitted not in codes:
            return Response(
                {"event_code": [f"'{submitted}' is not one of the events on this "
                                f"form. Assigned events: {codes}"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # DRF's Request.user has a setter, so the reviewer becomes the author for
        # everything downstream: created_by on the review, created_by on the
        # proposal the bridge mints, and the user on the ActionLog entry. Nothing
        # in that path is handed an AnonymousUser.
        request.user = mre

        serializer = PaperReviewSerializer(
            data=request.data, context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        review, proposal, created, _peers = create_review_with_workflows(
            serializer, request,
        )

        return Response({
            "id":            review.id,
            "speaker_name":  review.speaker_name,
            "event_code":    review.event_code,
            "proposal_score": review.proposal_score,
            "grade":         review.grade,
            # Named so the reviewer's browser can show a receipt line, and so a
            # support question ("did it go through?") has an id to quote.
            "proposal_submission": {"id": proposal.id, "created": created},
        }, status=status.HTTP_201_CREATED)
