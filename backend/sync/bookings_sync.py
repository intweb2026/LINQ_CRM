from django.utils import timezone
from django.conf import settings
from book_event.models import BookEvent, SyncLog
from services.google_sheets import google_sheets
import logging

logger = logging.getLogger('book_event')

# Column order matches the report table field mapping spec exactly.
BOOKINGS_HEADERS = [
    # Invoice / Event
    "Payment Status", "Event Code", "Event Name", "Booking Code",
    "Request Date", "Invoice Date", "Invoice Number", "Source",
    # Delegate
    "Delegate Name", "Delegate Email", "Phone", "Position",
    "Ticket Package", "Sponsorship Level", "Attendance", "Delegate Number",
    # Company
    "Delegate Company",
    # Contact
    "Accounts Contact Email",
    # Financial (admin columns — still written; sheet permissions control visibility)
    "Currency", "Paid/Free",
    "Pre-Tax Amount", "Tax Amount", "Total Amount", "Add-Ons Total", "Discount", "Discount Code",
    # Payment
    "Payment Type", "Date Paid", "Ref Number", "Add-Ons Notes",
    # Internal
    "Sales Executive", "Team Leader",
    # Audit
    "Added Time", "Modified Time",
]


def _row(inv, delegate=None):
    se = inv.sales_executive.username if inv.sales_executive else ""
    tl = inv.team_leader.username if inv.team_leader else ""

    if delegate:
        d_name       = delegate.full_name
        d_email      = delegate.email
        d_phone      = delegate.phone_number
        d_position   = delegate.position
        d_ticket_pkg = delegate.ticket_package
        d_sponsor    = delegate.sponsorship_level
        d_attendance = delegate.attendance
        d_number     = delegate.delegate_number
        company_info = delegate.company_display
        if delegate.company:
            parts = []
            if delegate.company.city:    parts.append(delegate.company.city)
            if delegate.company.country: parts.append(delegate.company.country)
            if parts:
                company_info = f"{delegate.company.name} ({', '.join(parts)})"
    else:
        d_name = d_email = d_phone = d_position = ""
        d_ticket_pkg = d_sponsor = d_attendance = ""
        d_number     = 0
        company_info = inv.company_name

    eff_status = (delegate.delegate_payment_status or inv.payment_status) if delegate else inv.payment_status

    return [
        eff_status,
        inv.event_code,
        inv.event_name,
        inv.booking_code,
        str(inv.created_at.date()),
        str(inv.invoice_date)     if inv.invoice_date     else "",
        inv.invoice_number,
        inv.source,
        d_name,
        d_email,
        d_phone,
        d_position,
        d_ticket_pkg,
        d_sponsor,
        d_attendance,
        d_number,
        company_info,
        inv.accounts_contact_email,
        inv.currency,
        inv.paid_free,
        str(inv.pre_tax_amount)       if inv.pre_tax_amount       is not None else "",
        str(inv.tax_amount)           if inv.tax_amount           is not None else "",
        str(inv.total_amount)         if inv.total_amount         is not None else "",
        str(inv.add_ons_total_amount) if inv.add_ons_total_amount is not None else "",
        float(inv.discount),
        inv.discount_code,
        (delegate.delegate_payment_type or inv.payment_type) if delegate else inv.payment_type,
        str(delegate.delegate_payment_date or inv.payment_date) if (delegate and (delegate.delegate_payment_date or inv.payment_date)) else (str(inv.payment_date) if inv.payment_date else ""),
        inv.reference,
        inv.add_ons,
        se,
        tl,
        str(inv.created_at),
        str(inv.updated_at),
    ]


def sync_bookings(full=False):
    if not google_sheets:
        return

    log, _ = SyncLog.objects.get_or_create(dataset="bookings")
    last_sync = log.last_synced_at if not full else None

    try:
        query = BookEvent.objects.all().order_by("updated_at")
        if last_sync:
            query = query.filter(updated_at__gt=last_sync)

        total_count = query.count()
        if total_count == 0 and not full:
            return

        all_rows   = []
        batch_size = 500

        for i in range(0, total_count, batch_size):
            batch = list(
                query[i:i + batch_size]
                .select_related("sales_executive", "team_leader")
                .prefetch_related("delegates", "delegates__company")
            )
            for inv in batch:
                delegates = list(inv.delegates.all())
                if delegates:
                    for d in delegates:
                        all_rows.append(_row(inv, d))
                else:
                    all_rows.append(_row(inv))

        count = google_sheets.replace_data(
            settings.GOOGLE_SHEET_BOOKINGS_TAB, BOOKINGS_HEADERS, all_rows
        )

        log.last_synced_at = timezone.now()
        log.records_synced = count
        log.last_status    = SyncLog.Status.SUCCESS
        log.error_message  = ""
        log.save()
        logger.info("Successfully synced %d booking rows (Full=%s).", count, full)

    except Exception as e:
        log.last_status   = SyncLog.Status.FAILED
        log.error_message = str(e)
        log.save()
        logger.error("Failed to sync bookings: %s", e)
