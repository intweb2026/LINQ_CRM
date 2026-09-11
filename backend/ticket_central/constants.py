"""
ticket_central/constants.py
────────────────────────────
Single source of truth for field ownership.
"""

MR_FIELDS = frozenset([
    "purpose", "link_url", "linkedin_keywords",
    "competitor_event_name", "organizer", "event_month_year",
    "event_location", "relationship", "type_of_ticket",
    "priority", "estimate", "mr_comments", "assigned_mr",
])

# The four import-provenance fields that used to be listed here,
# source_spreadsheet_id, source_tab, source_row_number and idempotency_key, are
# NO LONGER PART OF THE MODULE. They were the Zoho migration's own bookkeeping,
# carried into the CRM because the export had them; nobody raises, works or reads
# a ticket by them. Removing them from this set is what takes them out of the
# serializers, the filters and the writable surface, since every one of those is
# derived from here or from the model. The DATABASE COLUMNS are still there,
# holding what the import wrote, so nothing is lost and re-exposing one is a
# one-line change; see IMPORT_HIDDEN_FIELDS in utils.py for the import side.
DMD_FIELDS = frozenset([
    "assign_name", "assign_date", "actual_number", "new_contacts_created",
    "ticket_type", "complete_date", "hubspot_entry_date",
    "mined_count", "dm_comments",
    # Level 2 (LX-2) fields belong to DMD as well
    "assign_name_lx2", "actual_count_lx2", "complete_date_lx2", "dm_comments_lx2",
])

# DMD_FIELDS — every DMD-owned field
# DMD_WORK_FIELDS — the subset that indicates actual DMD activity
DMD_WORK_FIELDS = frozenset([
    "assign_name", "assign_date", "actual_number", "new_contacts_created",
    "ticket_type", "complete_date", "hubspot_entry_date",
    "mined_count", "dm_comments",
    "assign_name_lx2", "actual_count_lx2", "complete_date_lx2", "dm_comments_lx2",
])

# Used for status inference — these signal MR has done their part
MR_ACTIVITY_FIELDS = frozenset([
    "purpose", "link_url", "linkedin_keywords",
    "competitor_event_name", "organizer", "event_month_year",
    "event_location", "relationship", "type_of_ticket",
    "priority", "estimate", "mr_comments", "assigned_mr",
])

SHARED_FIELDS = frozenset([
    "ticket_number", "event_code", "event_name",
])

TICKET_PREFIX = "TC"

# The ONE Data Mining field a Market Research user is served.
#
# MR raises the brief and Actual Number is the answer to it — the count the
# mining actually returned — so it comes back with the ticket. The rest of
# Section B is Data Mining's own working, and the whole LX-2 second pass with it;
# MR was reading every cell of both.
#
# Subtracted from the payload by permissions.hidden_fields_for(), which is read
# by the two read serializers and by TicketViewSet.bulk_update_fields, so the
# columns are not merely hidden in the table, they are not sent and not writable.
# Mirrored in the frontend by MR_SEES in TicketCentralPage.jsx.
MR_VISIBLE_DMD_FIELDS = frozenset(["actual_number"])
MR_HIDDEN_FIELDS = DMD_FIELDS - MR_VISIBLE_DMD_FIELDS
