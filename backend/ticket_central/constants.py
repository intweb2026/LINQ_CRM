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

DMD_FIELDS = frozenset([
    "assign_name", "assign_date", "actual_number", "new_contacts_created",
    "source_spreadsheet_id", "source_tab", "source_row_number", "idempotency_key",
    "ticket_type", "complete_date", "hubspot_entry_date",
    "mined_count", "dm_comments",
    # Level 2 (LX-2) fields belong to DMD as well
    "assign_name_lx2", "actual_count_lx2", "complete_date_lx2", "dm_comments_lx2",
])

# DMD_FIELDS — full set (includes source_* and idempotency_key — import metadata)
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
