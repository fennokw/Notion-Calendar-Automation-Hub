# Notion Calendar Automation Hub

An all-in-one automation layer for a Notion-based calendar and task system.

This repository combines **shared calendar / ICS syncing, Canvas assignments and quizzes, GitHub-secret-backed calendar feeds, editable Notion event copies, sync auditing, and Notion Calendar color-feed views** around one canonical Notion database: `Schedule & Tasks`.

## What it does

### Calendar and ICS imports

The sync can import standards-compliant ICS calendars into native Notion pages from three source types:

1. **URL** — a direct public or private ICS URL stored in Notion.
2. **Repository file** — an `.ics` file committed to this repository.
3. **Secret URL** — a shared-calendar URL stored in GitHub Secrets while Notion stores only a safe lookup key.

Each enabled calendar source can define its own:

- default Area
- default Commitment
- default Type
- past/future import window
- update behavior
- missing-event behavior

Recurring ICS events are expanded into individual Notion event occurrences with stable external IDs so later syncs update the correct pages rather than creating duplicates.

### Secret-backed shared calendars

Private or shared calendar links do not need to live in Notion or in repository code.

GitHub stores a single secret named:

`SHARED_CALENDAR_URLS_JSON`

Example:

```json
{
  "SWIM_TEAM_ICS_URL": "https://calendar.google.com/calendar/u/2?cid=...",
  "TEAM_CALENDAR_URL": "https://example.com/team.ics"
}
```

In Notion, a Calendar Source row uses:

- `Source Type = Secret URL`
- `Secret Key = SWIM_TEAM_ICS_URL`

The actual calendar URL never needs to be written into the Notion row.

Google Calendar share URLs containing a `cid=` parameter are normalized into the corresponding public ICS endpoint automatically when possible. If the calendar is not publicly readable, use Google's **Secret address in iCal format** as the secret value instead.

### Canvas assignments and quizzes

The same GitHub Action can import Canvas coursework into `Schedule & Tasks`.

The Canvas sync:

- scans active courses
- imports published assignments with due dates
- imports Canvas quizzes
- identifies assignment-backed quizzes as `Type = Test`
- imports standalone quizzes that do not have a normal Assignment object
- classifies names containing words such as `quiz`, `midterm`, `exam`, `final`, or `test` as tests
- imports ordinary coursework as `Type = Homework`
- tags Canvas items with `Area = School`
- stores Canvas assignment/course IDs for stable updates
- marks submitted or graded items with strikethrough formatting

Canvas sync is optional. If `CANVAS_TOKEN` is not configured, the workflow skips Canvas without blocking ICS imports.

## Architecture

```text
                    GitHub Actions
                         │
            ┌────────────┴────────────┐
            │                         │
       ICS / calendars             Canvas API
            │                         │
     ┌──────┼────────┐          assignments + quizzes
     │      │        │                  │
 Direct   Repo    Secret URL            │
  URL     .ics    GitHub Secret         │
     └──────┴────────┘                  │
            │                           │
            └────────────┬──────────────┘
                         ▼
                 Schedule & Tasks
               single source of truth
                         │
             ┌───────────┼────────────┐
             │           │            │
        My Dashboard  Notion views  Notion Calendar
                                      color feeds

Calendar Sources = calendar configuration
Sync Runs        = ICS audit / health history
```

## Core design principle: one real event database

`Schedule & Tasks` is the canonical event/task database.

The Notion Calendar color feeds are **filtered linked views of that same database**, not copied databases. A title/date/commitment edit made through one of those views modifies the original Notion page immediately.

There is no mirror-database synchronization loop.

## Notion databases

### 1. Schedule & Tasks

#### User-facing properties

| Property | Type | Purpose |
| --- | --- | --- |
| `Name` | Title | Event/task name |
| `Date` | Date | Date or start/end time |
| `Type` | Select | `Event`, `Task`, `Homework`, `Test` |
| `Commitment` | Select | `Committed`, `Maybe`, `No` |
| `Area` | Multi-select | School, Lacrosse, organizations, projects, etc. |
| `Notes` | Text | Personal/context notes |

#### Sync-managed / technical properties

| Property | Purpose |
| --- | --- |
| `Calendar Source` | Relation to the originating Calendar Sources row |
| `External Event ID` | Stable deduplication/update key |
| `Source Name` | Current source-calendar title |
| `Source Date` | Current source-calendar date/time |
| `Source URL` | Original event/link URL when available |
| `Source Last Seen` | Last time the source occurrence was observed |
| `Source Status` | `Active`, `Cancelled`, `Missing`, or `Error` |
| `Source Fingerprint` | Source change metadata |
| `Sync Managed` | Distinguishes automated imports from manual items |
| `Import Source` | `Manual`, `ICS`, or `Canvas` |
| `Canvas Assignment ID` | Canvas update/deduplication key |
| `Canvas Course ID` | Canvas course identifier |

### 2. Calendar Sources

One row represents one ICS/shared-calendar source.

| Property | Purpose |
| --- | --- |
| `Name` | Human-readable source name |
| `Source Type` | `URL`, `Repository file`, or `Secret URL` |
| `ICS URL` | Direct calendar URL when using `URL` |
| `File Path` | Repository-relative `.ics` file path |
| `Secret Key` | Lookup key inside `SHARED_CALENDAR_URLS_JSON` |
| `Enabled` | Whether this source is synced |
| `Default Area` | Initial Area assigned to imported events |
| `Default Commitment` | Initial `Maybe` or `Committed` value |
| `Default Type` | Usually `Event` |
| `Import Past Days` | How far backward to import |
| `Import Future Days` | How far forward to import |
| `Update Mode` | `Preserve personal edits` or `Always follow source` |
| `Missing Policy` | `Mark missing`, `Archive unchanged`, or `Ignore` |
| `Last Sync` | Last attempted sync |
| `Last Result` | `Never`, `Success`, `Partial`, or `Error` |
| `Last Event Count` | Number of occurrences processed |
| `Last Message` | Latest source-level health/error message |

### 3. Sync Runs

Optional audit log for ICS workflow runs.

It records:

- start/end timestamps
- overall result
- sources processed
- occurrences seen
- pages created
- pages updated
- personal edits preserved
- missing occurrences marked
- errors
- run details

## ICS edit-preservation behavior

ICS-imported events track the source value separately from the editable user value.

Suppose the shared calendar says:

```text
Practice · 4:00–6:00 PM
```

You edit the Notion page to:

```text
Practice — leaving early · 4:00–5:15 PM
```

If the source later changes to 4:30–6:30 PM:

- `Source Name` / `Source Date` update to the new official values.
- Your edited `Name` / `Date` remain unchanged when the source is configured for **Preserve personal edits**.

If the source row uses **Always follow source**, the editable fields follow the source instead.

For ICS rows, personal `Commitment`, `Area`, `Type`, and `Notes` are not reset after initial creation.

## Missing-event behavior

Each calendar source controls what happens if an occurrence that was previously imported disappears from the feed:

- **Mark missing** — keep the page and set `Source Status = Missing`
- **Archive unchanged** — archive it only when the user has not modified its editable name/date
- **Ignore** — take no action

This avoids silently deleting personal edits.

## Notion Calendar color feeds

Notion Calendar assigns color at the calendar/view level rather than by individual database row.

The recommended setup is therefore several filtered views of the same `Schedule & Tasks` data source.

Suggested filters:

| Feed | Filter | Suggested color |
| --- | --- | --- |
| Committed | `Type in (Event, Task)` + `Commitment = Committed` | Green / teal |
| Maybe | `Type in (Event, Task)` + `Commitment = Maybe` | Gray |
| Declined | `Commitment = No` | Dark gray |
| Homework & Tests | `Type in (Homework, Test)` + `Commitment != No` | Red |

The display names can be changed to whatever is convenient in Notion Calendar. The important part is that every view points to the original `Schedule & Tasks` data source.

Changing an item's `Commitment` or `Type` causes it to move between these filtered views automatically.

## GitHub Actions

The workflow supports both:

- **manual runs** with `workflow_dispatch`
- **daily scheduled runs**

One failing ICS source does not prevent the remaining ICS sources from being attempted.

Canvas runs independently after the calendar step as long as the workflow has not been cancelled and `CANVAS_TOKEN` exists.

Repository permissions are read-only for workflow contents.

## Required GitHub configuration

Open:

**Settings → Secrets and variables → Actions**

### Repository secrets

| Secret | Required? | Purpose |
| --- | --- | --- |
| `NOTION_TOKEN` | Yes | Notion integration token |
| `CANVAS_TOKEN` | No | Enables Canvas assignment/quiz sync |
| `SHARED_CALENDAR_URLS_JSON` | No | Secret-backed shared calendar URL map |

Example shared-calendar secret:

```json
{
  "SWIM_TEAM_ICS_URL": "https://calendar.google.com/calendar/u/2?cid=...",
  "CLUB_CALENDAR_URL": "https://calendar.google.com/calendar/ical/.../basic.ics"
}
```

### Repository variables

| Variable | Required? | Purpose |
| --- | --- | --- |
| `SCHEDULE_DATABASE_ID` | Yes | Schedule & Tasks database/data-source ID |
| `CALENDAR_SOURCES_DATABASE_ID` | Yes | Calendar Sources database/data-source ID |
| `SYNC_RUNS_DATABASE_ID` | No | Sync Runs database/data-source ID |
| `DEFAULT_TIMEZONE` | Recommended | Timezone used for calendar parsing; defaults to `America/New_York` |

The Notion integration must have access to the **original databases/data sources**, not only linked views.

## Adding calendar sources

### Direct ICS URL

In `Calendar Sources`:

1. Set `Source Type = URL`.
2. Put the feed in `ICS URL`.
3. Choose defaults.
4. Enable the source.
5. Run the workflow.

### Repository ICS file

1. Commit the `.ics` file into this repository.
2. Set `Source Type = Repository file`.
3. Put its repository-relative path in `File Path`, for example:
   `class_schedule.ics`.
4. Choose defaults and enable it.

Repository-file access is restricted to files inside the repository.

### Secret-backed shared calendar

1. Add its URL to `SHARED_CALENDAR_URLS_JSON`.
2. In Notion set `Source Type = Secret URL`.
3. Set `Secret Key` to the matching JSON key.
4. Leave the secret URL itself out of the Notion row.
5. Choose defaults and enable the source.

This model supports any number of shared calendars without adding a new workflow variable for every calendar.

## Canvas setup

1. Generate a Canvas API token.
2. Add it as the GitHub Actions secret `CANVAS_TOKEN`.
3. Confirm `SCHEDULE_DATABASE_ID` points to the shared `Schedule & Tasks` database.
4. Run the workflow manually once.

The default Canvas host is:

`https://canvas.mit.edu`

It can be changed with `CANVAS_BASE_URL` when running the script outside the provided workflow.

## Local development

Python 3.12 is used by GitHub Actions.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest -q
```

Set the environment values described in `.env.example`, then run:

```bash
python sync_ics_to_notion.py
python sync_canvas_to_notion.py
```

Canvas requires `CANVAS_TOKEN`; the ICS script does not.

## Files

| File | Role |
| --- | --- |
| `sync_ics_to_notion.py` | ICS/shared-calendar import, recurrence expansion, edit preservation, missing-event handling, source health, run logging |
| `sync_canvas_to_notion.py` | Canvas assignments + quiz/test import |
| `config.py` | Notion schema property names and defaults |
| `class_schedule.ics` | Example/current repository-backed calendar source |
| `.github/workflows/daily-sync.yml` | Unified GitHub Actions workflow |
| `.env.example` | Local environment configuration example |
| `tests/test_sync_logic.py` | Core sync behavior tests |

## Reliability and privacy safeguards

- Stable external IDs prevent duplicate calendar occurrences.
- Recurring ICS events are expanded into individually tracked occurrences.
- One broken ICS feed does not stop other calendar sources.
- Personal ICS name/date edits can be preserved across upstream changes.
- Missing source events are not automatically destroyed by default.
- Secret-backed feed URLs stay in GitHub Actions Secrets.
- The workflow does not print the shared-calendar secret map.
- Canvas quiz endpoint failures degrade gracefully instead of blocking assignment import.
- Concurrent GitHub workflow executions are serialized.
