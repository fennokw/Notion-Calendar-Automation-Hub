# Editable ICS → Notion Calendar Sync

Turn any Google Calendar or standards-compliant ICS feed into **native, editable Notion events**. Calendar feeds are configured in Notion instead of hard-coded in the repository, so adding a calendar is one database row.

## Why this exists

Showing Google Calendar inside Notion Calendar does not make a read-only shared event editable. This project creates a personal Notion copy and tracks the official source values separately.

On a later sync:

- If you did not edit `Name` or `Date`, a source change flows through automatically.
- If you changed either field, your personal version is preserved while `Source Name` and `Source Date` update.
- `Commitment`, `Area`, `Type`, and `Notes` are never reset after creation.
- Missing or cancelled source events are marked instead of silently destroying your personal copy.

## Architecture

```text
Google / ICS feeds
        ↓
Calendar Sources (private Notion backend)
        ↓ daily GitHub Action
Schedule & Tasks (editable personal calendar)
        ↓
Notion Calendar / My Dashboard

Sync Runs (private audit log)
```

## Required databases

### Schedule & Tasks

Visible user fields:

| Property | Type |
| --- | --- |
| `Name` | Title |
| `Date` | Date |
| `Type` | Select: Event, Task, Homework, Test |
| `Commitment` | Status: Maybe, Committed, Done |
| `Area` | Multi-select |
| `Notes` | Text |

Hidden sync fields:

| Property | Type | Purpose |
| --- | --- | --- |
| `Calendar Source` | Relation | Which feed owns the copy |
| `External Event ID` | Text | Deduplication key |
| `Source Name` | Text | Current official title |
| `Source Date` | Date | Current official time |
| `Source URL` | URL | Original event link when available |
| `Source Last Seen` | Date | Feed health and missing-event logic |
| `Source Status` | Select | Active, Cancelled, Missing, Error |
| `Source Fingerprint` | Text | Change detection metadata |
| `Sync Managed` | Checkbox | Separates imports from manual rows |
| `Import Source` | Select | Manual, ICS, Canvas |
| `Calendar Color` | Select | Committed, Maybe, Homework, Test, Done |

### Calendar Sources

Create one row per calendar. The repository reads every enabled row.

| Property | Meaning |
| --- | --- |
| `Name` | Human-readable calendar name |
| `ICS URL` | Direct feed URL returning `BEGIN:VCALENDAR` |
| `Enabled` | Whether the daily job imports it |
| `Default Area` | Area assigned only when creating an event |
| `Default Commitment` | Maybe or Committed, only on creation |
| `Default Type` | Usually Event |
| `Import Past Days` | Defaults to 7 when blank |
| `Import Future Days` | Defaults to 180 when blank |
| `Update Mode` | Preserve edits or always follow source |
| `Missing Policy` | Mark missing, archive unchanged, or ignore |
| `Last Sync / Result / Message` | Operational health |

Treat private Google Calendar ICS addresses as secrets. Keeping them in this private backend database prevents them from appearing in repository files or Actions logs.

### Sync Runs

Optional audit database recording counts and errors for every workflow run.

## GitHub configuration

Open **Settings → Secrets and variables → Actions**.

Add one secret:

| Secret | Value |
| --- | --- |
| `NOTION_TOKEN` | Internal Notion integration token |

Add variables:

| Variable | Value |
| --- | --- |
| `SCHEDULE_DATABASE_ID` | Source database or data source ID |
| `CALENDAR_SOURCES_DATABASE_ID` | Source database or data source ID |
| `SYNC_RUNS_DATABASE_ID` | Optional audit database ID |
| `DEFAULT_TIMEZONE` | `America/New_York` for Boston/Cambridge |

Share all three source databases with the Notion integration. Linked views alone are insufficient; the integration needs access to the original databases.

## Add a Google Calendar

1. In Google Calendar, open the calendar's **Settings and sharing** page.
2. Copy its **Secret address in iCal format** or public iCal address.
3. Open the hidden `Calendar Sources` database in Notion.
4. Add a row, paste the address into `ICS URL`, choose defaults, and check `Enabled`.
5. Run **Actions → Editable ICS to Notion Sync → Run workflow** once to verify it.

You do not need to edit this repository for the second, third, or twentieth calendar.

## Edit-preservation behavior

Suppose the source says:

```text
Practice · 4:00–6:00 PM
```

You change the Notion copy to:

```text
Practice — leaving early · 4:00–5:15 PM
```

If the shared calendar later moves practice to 4:30–6:30 PM, the row becomes:

- `Name`: Practice — leaving early
- `Date`: 4:00–5:15 PM
- `Source Name`: Practice
- `Source Date`: 4:30–6:30 PM

The sync detects the personal override by comparing the editable fields to the previous source baseline.

## Calendar color

The sync maintains `Calendar Color` with this priority:

1. Test → red
2. Homework → yellow
3. Done → blue
4. Committed → green/teal
5. Maybe → gray

In the Notion UI, set the Calendar view's **Color by** property to `Calendar Color`. The current API can create and configure views but cannot set that color-by control, so this is the one manual view setting.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
```

Copy `.env.example` values into your shell environment, then run:

```bash
python sync_ics_to_notion.py
```

## Safety defaults

- One bad feed does not block other calendars.
- The default missing policy marks an event instead of deleting it.
- A source URL never appears in normal dashboard views.
- The workflow has read-only repository permissions.
- Concurrent workflow runs are serialized to prevent duplicate imports.

