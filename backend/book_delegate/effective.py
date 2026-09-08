"""
book_delegate/effective.py
───────────────────────────
What a delegate's payment fields ACTUALLY say.

Five columns on BookDelegate are overrides, and a null or blank one means
"inherit the invoice". So there are two answers to "what is this delegate's
payment status", the stored one and the effective one, and every read that
matters wants the second.

The rule lived inline in BookDelegateFilter._effective_filter and nowhere else,
which was fine while the filter set was the only caller. Pre-Event Docs is a
second caller and needs the same rule in BOTH shapes, as a Q for the queryset
that selects who is attending, and in Python for the rows already fetched. Three
spellings of one rule is two places for it to drift, so both live here and the
filter set calls the first of them.

BLANK COUNTS AS UNSET. The columns are declared null=True with default None, and
a blank string reaches them anyway through form and serializer input, so both
have to read as "inherit". Testing truthiness would be the same thing for these
five fields, and is spelled out rather than relied on.
"""
from django.db.models import Q


def effective_q(delegate_field, invoice_field, values):
    """
    Q matching rows whose EFFECTIVE value is any of `values`.

    Carried over verbatim from BookDelegateFilter._effective_filter, including
    the three way OR per value: the delegate's own column matches, or it is null
    and the invoice matches, or it is blank and the invoice matches.
    """
    q = Q()
    for value in values:
        q |= (
            Q(**{f"{delegate_field}__iexact": value})
            | Q(**{f"{delegate_field}__isnull": True,
                   f"{invoice_field}__iexact": value})
            | Q(**{delegate_field: "", f"{invoice_field}__iexact": value})
        )
    return q


def effective_value(delegate, delegate_attr, invoice_attr):
    """
    The same answer for a row already in memory.

    Callers are expected to have select_related the invoice; this reads the
    related object rather than querying, so a caller that has not will pay for
    it one row at a time and should fix the queryset rather than this.
    """
    own = getattr(delegate, delegate_attr, None)
    if own not in (None, ""):
        return own
    if not delegate.invoice_id:
        return None
    return getattr(delegate.invoice, invoice_attr, None)
