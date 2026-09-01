#!/usr/bin/env python
"""
verify_paper_review_form.py
────────────────────────────
Post-deploy smoke check for the MRE paper review form, run against a LIVE
deployment over HTTP. Needs no database access and no Django settings, so it can
be run from a laptop against production.

    cd backend
    python scripts/verify_paper_review_form.py "<full form link>"
    python scripts/verify_paper_review_form.py "<full form link>" --submit

READ-ONLY BY DEFAULT. Without --submit it only opens the form and probes the
refusals, so it writes nothing and sends no email. --submit adds one real
submission, which is the only way to prove the two ADD workflows actually run in
that environment; it mints a real ProposalSubmission and, unless notifications
are disabled there, sends a real production-team email. The speaker name carries
a SMOKE TEST marker and the script prints the ids to delete afterwards.

WHAT EACH CHECK IS FOR, since a green tick nobody can interpret is worth nothing:

  1  the route is served                the React page and the API both exist at
                                        that host, so the reviewer sees a form
                                        rather than a 404 from a stale build
  2  the link opens                     the key resolves, the reviewer is named,
                                        and their events came back
  3  events are scoped                  the list is not the whole catalogue,
                                        which is what a broken scope looks like
  4  no key is refused                  the endpoint is not open to the world
  5  a wrong key is refused             a revoked or mistyped link fails closed
  6  an unknown event is refused        the validator is wired
  7  (--submit) a review is created     both workflows ran, and the response
                                        names the proposal submission
  8  (--submit) MR field is not echoed  the receipt carries no
                                        internal_footnotes, even on a link whose
                                        reviewer is allowed to write one
"""
from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlparse

import requests

TIMEOUT = 20
KEY_PARAM = "crm_key"

# Marks the row this script writes, so a human reading the paper review table
# knows what it is without having to ask.
# Plain hyphen for the same console reason as the key mask below, and because
# this string is echoed back to a terminal as well as stored on the row.
SMOKE_SPEAKER = "SMOKE TEST - delete me"


def review_body(event_code, footnotes=False):
    payload = {
        "event_code": event_code,
        "paper_submission_date": "2026-01-01",
        "speaker_name": SMOKE_SPEAKER,
        "company_name": "Smoke Test Ltd",
        "email": "smoke.test@example.invalid",
        "linkedin_speaker": "https://linkedin.com/in/smoke-test",
        "linkedin_followers": 1,
        "closeness_to_topic": 1,
        "closeness_to_region": 1,
        "clear_solution_to_challenges": 1,
        "case_study_results_examples": 1,
        "not_obvious_sales_pitch": 1,
        "company_profile_score": 1,
        "session_location_on_agenda": "Day 1, Morning Session",
        "proposal_received": "Smoke test, safe to delete.",
        "theme": "Smoke test",
        "agenda_addition": "Smoke test",
    }
    # Only for a link whose reviewer may write it — config said so. Sending it
    # otherwise is a 400 on the whole submission, which would fail check 7 for
    # the wrong reason. Check 8 proves the receipt still does not echo it.
    if footnotes:
        payload["internal_footnotes"] = "Smoke test footnote, safe to delete."
    return payload


def net_hint(exc):
    """
    A transport failure said in one line, with the cause named.

    requests reports these as a wall of nested exception text, and the two that
    actually happen here are both host mistakes rather than anything about the
    form: a domain missing part of itself (no .com, no www) presents a
    certificate that cannot match, and a domain that does not exist fails to
    resolve. Printing 400 characters of pool traceback buried that.
    """
    text = str(exc)
    if isinstance(exc, requests.exceptions.SSLError) or "CERTIFICATE_VERIFY_FAILED" in text:
        return ("TLS hostname mismatch: no certificate covers this host. The "
                "domain is probably incomplete, e.g. missing .com or www")
    if any(m in text for m in ("NameResolutionError",
                               "Name or service not known",
                               "getaddrinfo failed")):
        return "the host does not resolve: check the domain spelling"
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return "connection timed out: wrong host, or the site is unreachable from here"
    return text[:200]


class Checks:
    def __init__(self):
        self.failed = 0

    def ok(self, label, detail=""):
        print(f"  PASS  {label}" + (f"  ({detail})" if detail else ""))

    def bad(self, label, detail=""):
        self.failed += 1
        print(f"  FAIL  {label}" + (f"  ({detail})" if detail else ""))

    def expect(self, condition, label, detail=""):
        (self.ok if condition else self.bad)(label, detail)
        return bool(condition)


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    do_submit = "--submit" in argv

    if len(args) != 1:
        print(__doc__.strip())
        return 2

    link = args[0]
    parsed = urlparse(link)
    key = (parse_qs(parsed.query).get(KEY_PARAM) or [""])[0]
    if not parsed.scheme or not parsed.netloc or not key:
        print(f"That does not look like a form link. Expected "
              f"https://host/paper-review/submit?{KEY_PARAM}=crm_live_...")
        return 2

    origin = f"{parsed.scheme}://{parsed.netloc}"
    config_url = f"{origin}/api/paper-review-form/config/"
    submit_url = f"{origin}/api/paper-review-form/submit/"
    c = Checks()

    print(f"\nHost: {origin}")
    # ASCII, not an ellipsis character: this prints to a Windows console whose
    # codepage cannot encode U+2026, and a mangled key mask reads as a corrupt
    # key rather than as a console limitation.
    print(f"Key:  {key[:14]}...{key[-4:]}\n")

    # 1 — the page itself. A deployment that shipped the backend but not a
    # rebuilt frontend answers here with the old bundle, which renders the
    # authenticated shell and bounces the reviewer to /login.
    print("Page")
    try:
        page = requests.get(link, timeout=TIMEOUT)
        c.expect(page.status_code == 200, "the form route is served",
                 f"HTTP {page.status_code}")
        c.expect("<div id=\"root\"" in page.text or "root" in page.text,
                 "the React shell came back")
    except requests.RequestException as exc:
        c.bad("the form route is served", net_hint(exc))

    # 2, 3 — the link itself.
    print("\nLink")
    events = []
    show_internal = False
    try:
        r = requests.get(config_url, params={KEY_PARAM: key}, timeout=TIMEOUT)
        detail = "" if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:160]}"
        if r.history:
            final = urlparse(r.url).netloc
            c.bad("the host is the canonical one",
                  f"redirected to {final}; put that host in the links, since a "
                  f"302 turns the submit POST into a GET")
        else:
            c.ok("the host is the canonical one")
        if c.expect(r.status_code == 200, "the link opens", detail):
            data = r.json()
            events = data.get("events") or []
            show_internal = bool(data.get("show_internal"))
            c.expect(bool(data.get("reviewer")), "the reviewer is named",
                     data.get("reviewer"))
            c.expect(len(events) > 0, "events came back", f"{len(events)} event(s)")
            # A scope failure does not error, it over-serves. 100+ codes on one
            # reviewer's form means the exact-assignment scope is not being used.
            c.expect(len(events) < 100, "the event list is scoped, not the whole "
                                        "catalogue", f"{len(events)} event(s)")
            c.expect(data.get("rubric_total") == 45, "the rubric totals 45",
                     str(data.get("rubric_total")))
    except requests.RequestException as exc:
        c.bad("the link opens", net_hint(exc))

    # 4, 5 — it fails closed. Both must be 401 and neither may be a redirect to
    # /login, which is what the shared axios client used to do to a reviewer.
    print("\nRefusals")
    try:
        bare = requests.get(config_url, timeout=TIMEOUT, allow_redirects=False)
        c.expect(bare.status_code == 401, "no key is refused",
                 f"HTTP {bare.status_code}")
        wrong = requests.get(config_url, params={KEY_PARAM: "crm_live_not_a_key"},
                             timeout=TIMEOUT, allow_redirects=False)
        c.expect(wrong.status_code == 401, "a wrong key is refused",
                 f"HTTP {wrong.status_code}")
    except requests.RequestException as exc:
        c.bad("the refusal probes ran", net_hint(exc))

    # 6 — the validator, proved without writing anything.
    try:
        r = requests.post(submit_url, params={KEY_PARAM: key},
                          json=review_body("ZZZ - NOT AN EVENT"), timeout=TIMEOUT)
        c.expect(r.status_code == 400, "an event outside the form is refused",
                 f"HTTP {r.status_code}")
    except requests.RequestException as exc:
        c.bad("an event outside the form is refused", net_hint(exc))

    # 7, 8 — the write path, opt-in.
    if do_submit and events:
        code = events[0]["event_code"]
        print(f"\nSubmission  (writes one review against {code})")
        try:
            r = requests.post(submit_url, params={KEY_PARAM: key},
                              json=review_body(code, footnotes=show_internal),
                              timeout=TIMEOUT)
            if c.expect(r.status_code == 201, "the review is created",
                        f"HTTP {r.status_code}: {r.text[:200]}"):
                out = r.json()
                proposal = (out.get("proposal_submission") or {}).get("id")
                c.expect(proposal is not None,
                         "the proposal submission was minted",
                         f"proposal #{proposal}")
                c.expect("internal_footnotes" not in out,
                         "internal_footnotes is not echoed")
                c.expect(out.get("proposal_score") == 6,
                         "the score is derived server side",
                         f"{out.get('proposal_score')} of 45, grade {out.get('grade')}")
                print(f"\n  DELETE THESE: paper review #{out.get('id')} and "
                      f"proposal submission #{proposal}.")
                print("  Search the paper review table for "
                      f"\"{SMOKE_SPEAKER}\".")
                print("  The production-team email fired unless notifications "
                      "are disabled in that environment; check Notification "
                      "logs for the row either way.")
        except requests.RequestException as exc:
            c.bad("the review is created", net_hint(exc))
    elif do_submit:
        c.bad("the review is created", "no events came back, nothing to file against")
    else:
        print("\nSubmission  (skipped, pass --submit to write one test review)")

    print()
    if c.failed:
        print(f"{c.failed} check(s) FAILED.")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
