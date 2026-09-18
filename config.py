"""Schema names and safe defaults for the editable ICS → Notion sync."""

from __future__ import annotations

import os

NOTION_VERSION = "2025-09-03"
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")
HTTP_TIMEOUT_SECONDS = int(os.getenv("HTTP_TIMEOUT_SECONDS", "30"))

# A source row can override these window defaults.
DEFAULT_IMPORT_PAST_DAYS = int(os.getenv("DEFAULT_IMPORT_PAST_DAYS", "7"))
DEFAULT_IMPORT_FUTURE_DAYS = int(os.getenv("DEFAULT_IMPORT_FUTURE_DAYS", "180"))

SCHEDULE = {
    "name": "Name",
    "date": "Date",
    "type": "Type",
    "commitment": "Commitment",
    "area": "Area",
    "notes": "Notes",
    "calendar_source": "Calendar Source",
    "external_id": "External Event ID",
    "source_name": "Source Name",
    "source_date": "Source Date",
    "source_url": "Source URL",
    "source_last_seen": "Source Last Seen",
    "source_status": "Source Status",
    "source_fingerprint": "Source Fingerprint",
    "sync_managed": "Sync Managed",
    "import_source": "Import Source",
    "calendar_color": "Calendar Color",
}

SOURCES = {
    "name": "Name",
    "ics_url": "ICS URL",
    "enabled": "Enabled",
    "default_area": "Default Area",
    "default_commitment": "Default Commitment",
    "default_type": "Default Type",
    "past_days": "Import Past Days",
    "future_days": "Import Future Days",
    "update_mode": "Update Mode",
    "missing_policy": "Missing Policy",
    "last_sync": "Last Sync",
    "last_result": "Last Result",
    "last_message": "Last Message",
    "last_event_count": "Last Event Count",
}

RUNS = {
    "name": "Name",
    "started": "Started At",
    "finished": "Finished At",
    "result": "Result",
    "sources": "Sources",
    "seen": "Seen",
    "created": "Created",
    "updated": "Updated",
    "preserved": "Preserved",
    "missing": "Marked Missing",
    "errors": "Errors",
    "details": "Details",
}

PRESERVE_EDITS = "Preserve personal edits"
FOLLOW_SOURCE = "Always follow source"
MARK_MISSING = "Mark missing"
ARCHIVE_UNCHANGED = "Archive unchanged"
IGNORE_MISSING = "Ignore"

