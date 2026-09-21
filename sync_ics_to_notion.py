#!/usr/bin/env python3
"""Sync every enabled Notion Calendar Source into editable Notion events."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import recurring_ical_events
import requests
from icalendar import Calendar

from config import (
    ARCHIVE_UNCHANGED,
    DEFAULT_IMPORT_FUTURE_DAYS,
    DEFAULT_IMPORT_PAST_DAYS,
    DEFAULT_TIMEZONE,
    FOLLOW_SOURCE,
    HTTP_TIMEOUT_SECONDS,
    IGNORE_MISSING,
    MARK_MISSING,
    NOTION_VERSION,
    PRESERVE_EDITS,
    RUNS,
    SCHEDULE,
    SOURCES,
)

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
SCHEDULE_DATABASE_ID = os.environ["SCHEDULE_DATABASE_ID"]
CALENDAR_SOURCES_DATABASE_ID = os.environ["CALENDAR_SOURCES_DATABASE_ID"]
SYNC_RUNS_DATABASE_ID = os.getenv("SYNC_RUNS_DATABASE_ID", "").strip()


@dataclass
class SourceConfig:
    page_id: str
    name: str
    ics_url: str
    area: Optional[str]
    commitment: str
    item_type: str
    past_days: int
    future_days: int
    update_mode: str
    missing_policy: str
    source_type: str = "URL"


@dataclass
class Event:
    external_id: str
    name: str
    start: str
    end: Optional[str]
    event_url: Optional[str]
    status: str
    fingerprint: str


@dataclass
class Stats:
    sources: int = 0
    seen: int = 0
    created: int = 0
    updated: int = 0
    preserved: int = 0
    missing: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)


class NotionClient:
    def __init__(self, token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            }
        )

    def request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(5):
            try:
                response = self.session.request(
                    method, url, timeout=HTTP_TIMEOUT_SECONDS, **kwargs
                )
                if response.status_code == 429:
                    delay = float(response.headers.get("Retry-After", "1"))
                    time.sleep(min(delay, 10))
                    continue
                if response.status_code >= 500:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(2**attempt)
        raise RuntimeError(f"Notion request failed: {method} {url}: {last_error}")

    def data_source_id(self, database_or_source_id: str) -> str:
        value = database_or_source_id.replace("collection://", "").strip()
        try:
            data = self.request("GET", f"https://api.notion.com/v1/databases/{value}")
        except RuntimeError:
            # The value may already be a data source ID.
            self.request("GET", f"https://api.notion.com/v1/data_sources/{value}")
            return value
        sources = data.get("data_sources") or []
        if not sources:
            raise RuntimeError(f"Database {value} has no data source")
        return sources[0]["id"]

    def query(
        self, data_source_id: str, filter_obj: Optional[dict[str, Any]] = None
    ) -> list[dict[str, Any]]:
        url = f"https://api.notion.com/v1/data_sources/{data_source_id}/query"
        results: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if filter_obj:
                payload["filter"] = filter_obj
            if cursor:
                payload["start_cursor"] = cursor
            data = self.request("POST", url, json=payload)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            cursor = data.get("next_cursor")

    def create_page(self, data_source_id: str, properties: dict[str, Any]) -> dict[str, Any]:
        return self.request(
            "POST",
            "https://api.notion.com/v1/pages",
            json={
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": properties,
            },
        )

    def update_page(
        self,
        page_id: str,
        properties: Optional[dict[str, Any]] = None,
        archived: Optional[bool] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if properties is not None:
            payload["properties"] = properties
        if archived is not None:
            payload["archived"] = archived
        return self.request(
            "PATCH", f"https://api.notion.com/v1/pages/{page_id}", json=payload
        )


def title_value(value: str, strikethrough: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {"type": "text", "text": {"content": value[:2000]}}
    if strikethrough:
        item["annotations"] = {"strikethrough": True}
    return {"title": [item]}


def text_value(value: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": value[:2000]}}]}


def date_value(start: str, end: Optional[str] = None) -> dict[str, Any]:
    value: dict[str, Any] = {"start": start}
    if end:
        value["end"] = end
    return {"date": value}


def select_value(value: str) -> dict[str, Any]:
    return {"select": {"name": value}}


def status_value(value: str) -> dict[str, Any]:
    return {"status": {"name": value}}


def relation_value(page_id: str) -> dict[str, Any]:
    return {"relation": [{"id": page_id}]}


def checkbox_value(value: bool) -> dict[str, Any]:
    return {"checkbox": value}


def number_value(value: int) -> dict[str, Any]:
    return {"number": value}


def url_value(value: Optional[str]) -> dict[str, Any]:
    return {"url": value}


def get_title(page: dict[str, Any], prop: str) -> str:
    parts = page.get("properties", {}).get(prop, {}).get("title", [])
    return "".join(x.get("plain_text", "") for x in parts).strip()


def get_text(page: dict[str, Any], prop: str) -> str:
    parts = page.get("properties", {}).get(prop, {}).get("rich_text", [])
    return "".join(x.get("plain_text", "") for x in parts).strip()


def get_select(page: dict[str, Any], prop: str) -> Optional[str]:
    item = page.get("properties", {}).get(prop, {}).get("select")
    return item.get("name") if item else None


def get_status(page: dict[str, Any], prop: str) -> Optional[str]:
    item = page.get("properties", {}).get(prop, {}).get("status")
    return item.get("name") if item else None


def get_url(page: dict[str, Any], prop: str) -> Optional[str]:
    return page.get("properties", {}).get(prop, {}).get("url")


def get_checkbox(page: dict[str, Any], prop: str) -> bool:
    return bool(page.get("properties", {}).get(prop, {}).get("checkbox"))


def get_number(page: dict[str, Any], prop: str, default: int) -> int:
    value = page.get("properties", {}).get(prop, {}).get("number")
    return int(value) if value is not None else default


def get_date(page: dict[str, Any], prop: str) -> Optional[dict[str, Any]]:
    return page.get("properties", {}).get(prop, {}).get("date")


def get_multi_select(page: dict[str, Any], prop: str) -> list[str]:
    values = page.get("properties", {}).get(prop, {}).get("multi_select", [])
    return [x.get("name", "") for x in values if x.get("name")]


def canonical_date(value: Optional[dict[str, Any]]) -> tuple[Optional[str], Optional[str]]:
    if not value:
        return (None, None)
    return (value.get("start"), value.get("end"))


def normalize_text(value: str) -> str:
    return " ".join((value or "").split())


def dates_match(left: Optional[dict[str, Any]], right: Optional[dict[str, Any]]) -> bool:
    return canonical_date(left) == canonical_date(right)


def calendar_color(item_type: str, commitment: str) -> str:
    if item_type == "Test":
        return "Test"
    if item_type == "Homework":
        return "Homework"
    if commitment == "No":
        return "No"
    if commitment == "Done":
        return "Done"
    return "Committed" if commitment == "Committed" else "Maybe"


def load_sources(notion: NotionClient, sources_id: str) -> list[SourceConfig]:
    pages = notion.query(
        sources_id,
        {"property": SOURCES["enabled"], "checkbox": {"equals": True}},
    )
    sources: list[SourceConfig] = []
    for page in pages:
        source_type = get_select(page, SOURCES["source_type"]) or "URL"
        ics_url = get_url(page, SOURCES["ics_url"]) or get_text(page, SOURCES["file_path"])
        if not ics_url:
            continue
        sources.append(
            SourceConfig(
                page_id=page["id"],
                name=get_title(page, SOURCES["name"]) or "Unnamed calendar",
                ics_url=ics_url,
                source_type=source_type,
                area=get_select(page, SOURCES["default_area"]),
                commitment=get_select(page, SOURCES["default_commitment"]) or "Maybe",
                item_type=get_select(page, SOURCES["default_type"]) or "Event",
                past_days=get_number(
                    page, SOURCES["past_days"], DEFAULT_IMPORT_PAST_DAYS
                ),
                future_days=get_number(
                    page, SOURCES["future_days"], DEFAULT_IMPORT_FUTURE_DAYS
                ),
                update_mode=get_select(page, SOURCES["update_mode"])
                or PRESERVE_EDITS,
                missing_policy=get_select(page, SOURCES["missing_policy"])
                or MARK_MISSING,
            )
        )
    return sources


def fetch_ics(url: str, source_type: str = "URL") -> bytes:
    if source_type == "Repository file":
        root = Path(__file__).resolve().parent
        path = (root / url).resolve()
        if root not in path.parents or not path.is_file():
            raise RuntimeError(f"Calendar file not found inside repository: {url}")
        body = path.read_bytes()
        if b"BEGIN:VCALENDAR" not in body[:10000]:
            raise RuntimeError("Repository file is not an ICS calendar")
        return body
    response = requests.get(
        url,
        timeout=HTTP_TIMEOUT_SECONDS,
        headers={"User-Agent": "editable-ics-notion-sync/1.0"},
    )
    response.raise_for_status()
    body = response.content
    if b"BEGIN:VCALENDAR" not in body[:10000]:
        raise RuntimeError("Feed did not return an ICS calendar")
    return body


def window_datetime(value: Any, zone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        return (value.replace(tzinfo=zone) if value.tzinfo is None else value).astimezone(
            timezone.utc
        )
    if isinstance(value, date):
        return datetime.combine(value, dt_time.min, tzinfo=zone).astimezone(timezone.utc)
    raise TypeError(f"Unsupported calendar date: {type(value)!r}")


def notion_datetime(value: Any, zone: ZoneInfo) -> str:
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=zone) if value.tzinfo is None else value
        return aware.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Unsupported calendar date: {type(value)!r}")


def event_external_id(
    source_id: str, component: Any, uid: str, start: str, recurring: bool
) -> str:
    recurrence = component.get("RECURRENCE-ID")
    if recurrence:
        raw = recurrence.dt
        anchor = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
        occurrence = f"recurrence:{anchor}"
    elif recurring:
        occurrence = f"start:{start}"
    else:
        occurrence = "single"
    uid_part = uid or hashlib.sha256(f"{start}:{component.get('SUMMARY', '')}".encode()).hexdigest()[:20]
    return f"ics:{source_id}:{uid_part}:{occurrence}"


def parse_events(source: SourceConfig, body: bytes) -> list[Event]:
    calendar = Calendar.from_ical(body)
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=source.past_days)
    window_end = now + timedelta(days=source.future_days)
    expanded = recurring_ical_events.of(calendar).between(window_start, window_end)
    events: list[Event] = []
    for component in expanded:
        start_property = component.get("DTSTART")
        if not start_property:
            continue
        start = notion_datetime(start_property.dt, zone)
        end_property = component.get("DTEND")
        end = notion_datetime(end_property.dt, zone) if end_property else None
        name = normalize_text(str(component.get("SUMMARY", "") or "Untitled Event"))
        uid = normalize_text(str(component.get("UID", "") or ""))
        status = normalize_text(str(component.get("STATUS", "") or "ACTIVE")).upper()
        event_url_raw = component.get("URL")
        event_url = str(event_url_raw).strip() if event_url_raw else None
        recurring = bool(component.get("RRULE"))
        external_id = event_external_id(source.page_id, component, uid, start, recurring)
        canonical = json.dumps(
            {"name": name, "start": start, "end": end, "url": event_url, "status": status},
            sort_keys=True,
            separators=(",", ":"),
        )
        events.append(
            Event(
                external_id=external_id,
                name=name,
                start=start,
                end=end,
                event_url=event_url,
                status=status,
                fingerprint=hashlib.sha256(canonical.encode()).hexdigest(),
            )
        )
    return events


def existing_for_source(
    notion: NotionClient, schedule_id: str, source_page_id: str
) -> dict[str, dict[str, Any]]:
    pages = notion.query(
        schedule_id,
        {
            "and": [
                {
                    "property": SCHEDULE["calendar_source"],
                    "relation": {"contains": source_page_id},
                },
                {
                    "property": SCHEDULE["sync_managed"],
                    "checkbox": {"equals": True},
                },
            ]
        },
    )
    return {
        get_text(page, SCHEDULE["external_id"]): page
        for page in pages
        if get_text(page, SCHEDULE["external_id"])
    }


def new_event_properties(source: SourceConfig, event: Event, now: str) -> dict[str, Any]:
    source_status = "Cancelled" if event.status == "CANCELLED" else "Active"
    props: dict[str, Any] = {
        SCHEDULE["name"]: title_value(event.name),
        SCHEDULE["date"]: date_value(event.start, event.end),
        SCHEDULE["type"]: select_value(source.item_type),
        SCHEDULE["commitment"]: select_value(source.commitment),
        SCHEDULE["calendar_source"]: relation_value(source.page_id),
        SCHEDULE["external_id"]: text_value(event.external_id),
        SCHEDULE["source_name"]: text_value(event.name),
        SCHEDULE["source_date"]: date_value(event.start, event.end),
        SCHEDULE["source_url"]: url_value(event.event_url),
        SCHEDULE["source_last_seen"]: date_value(now),
        SCHEDULE["source_status"]: select_value(source_status),
        SCHEDULE["source_fingerprint"]: text_value(event.fingerprint),
        SCHEDULE["sync_managed"]: checkbox_value(True),
        SCHEDULE["import_source"]: select_value("ICS"),
        SCHEDULE["calendar_color"]: select_value(
            calendar_color(source.item_type, source.commitment)
        ),
    }
    if source.area:
        props[SCHEDULE["area"]] = {"multi_select": [{"name": source.area}]}
    return props


def update_event_properties(
    source: SourceConfig, event: Event, page: dict[str, Any], now: str
) -> tuple[dict[str, Any], int]:
    old_source_name = get_text(page, SCHEDULE["source_name"])
    old_source_date = get_date(page, SCHEDULE["source_date"])
    personal_name = get_title(page, SCHEDULE["name"])
    personal_date = get_date(page, SCHEDULE["date"])
    next_source_date = {"start": event.start, "end": event.end}
    preserved = 0

    name_is_unedited = not old_source_name or personal_name == old_source_name
    date_is_unedited = old_source_date is None or dates_match(personal_date, old_source_date)
    follow = source.update_mode == FOLLOW_SOURCE

    props: dict[str, Any] = {
        SCHEDULE["source_name"]: text_value(event.name),
        SCHEDULE["source_date"]: date_value(event.start, event.end),
        SCHEDULE["source_url"]: url_value(event.event_url),
        SCHEDULE["source_last_seen"]: date_value(now),
        SCHEDULE["source_status"]: select_value(
            "Cancelled" if event.status == "CANCELLED" else "Active"
        ),
        SCHEDULE["source_fingerprint"]: text_value(event.fingerprint),
        SCHEDULE["calendar_source"]: relation_value(source.page_id),
        SCHEDULE["sync_managed"]: checkbox_value(True),
        SCHEDULE["import_source"]: select_value("ICS"),
    }

    commitment = get_select(page, SCHEDULE["commitment"]) or source.commitment
    if not get_select(page, SCHEDULE["commitment"]):
        props[SCHEDULE["commitment"]] = select_value(commitment)
    if follow or name_is_unedited or commitment == "No":
        props[SCHEDULE["name"]] = title_value(event.name, strikethrough=commitment == "No")
    elif personal_name != event.name:
        preserved += 1

    if follow or date_is_unedited:
        props[SCHEDULE["date"]] = date_value(
            next_source_date["start"], next_source_date["end"]
        )
    elif not dates_match(personal_date, next_source_date):
        preserved += 1

    item_type = get_select(page, SCHEDULE["type"]) or source.item_type
    props[SCHEDULE["calendar_color"]] = select_value(
        calendar_color(item_type, commitment)
    )
    return props, preserved


def iso_in_window(value: Optional[str], start: datetime, end: datetime) -> bool:
    if not value:
        return False
    try:
        if len(value) == 10:
            parsed = datetime.combine(date.fromisoformat(value), dt_time.min, tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
        return start <= parsed <= end
    except ValueError:
        return False


def handle_missing(
    notion: NotionClient,
    source: SourceConfig,
    existing: dict[str, dict[str, Any]],
    seen_ids: set[str],
    stats: Stats,
) -> None:
    if source.missing_policy == IGNORE_MISSING:
        return
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=source.past_days)
    window_end = now + timedelta(days=source.future_days)
    for external_id, page in existing.items():
        if external_id in seen_ids:
            continue
        source_date = get_date(page, SCHEDULE["source_date"])
        if not iso_in_window((source_date or {}).get("start"), window_start, window_end):
            continue
        unchanged = (
            get_title(page, SCHEDULE["name"]) == get_text(page, SCHEDULE["source_name"])
            and dates_match(get_date(page, SCHEDULE["date"]), source_date)
        )
        if source.missing_policy == ARCHIVE_UNCHANGED and unchanged:
            notion.update_page(page["id"], archived=True)
        else:
            notion.update_page(
                page["id"],
                {SCHEDULE["source_status"]: select_value("Missing")},
            )
        stats.missing += 1


def update_source_health(
    notion: NotionClient,
    source: SourceConfig,
    result: str,
    message: str,
    count: int,
    now: str,
) -> None:
    notion.update_page(
        source.page_id,
        {
            SOURCES["last_sync"]: date_value(now),
            SOURCES["last_result"]: select_value(result),
            SOURCES["last_message"]: text_value(message),
            SOURCES["last_event_count"]: number_value(count),
        },
    )


def log_run(
    notion: NotionClient,
    runs_id: Optional[str],
    started: str,
    finished: str,
    result: str,
    stats: Stats,
) -> None:
    if not runs_id:
        return
    notion.create_page(
        runs_id,
        {
            RUNS["name"]: title_value(f"ICS sync · {finished[:16].replace('T', ' ')} UTC"),
            RUNS["started"]: date_value(started),
            RUNS["finished"]: date_value(finished),
            RUNS["result"]: select_value(result),
            RUNS["sources"]: number_value(stats.sources),
            RUNS["seen"]: number_value(stats.seen),
            RUNS["created"]: number_value(stats.created),
            RUNS["updated"]: number_value(stats.updated),
            RUNS["preserved"]: number_value(stats.preserved),
            RUNS["missing"]: number_value(stats.missing),
            RUNS["errors"]: number_value(stats.errors),
            RUNS["details"]: text_value("\n".join(stats.details)[-2000:]),
        },
    )


def sync_source(
    notion: NotionClient,
    schedule_id: str,
    source: SourceConfig,
    stats: Stats,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    events = parse_events(source, fetch_ics(source.ics_url, source.source_type))
    existing = existing_for_source(notion, schedule_id, source.page_id)
    seen_ids: set[str] = set()
    for event in events:
        seen_ids.add(event.external_id)
        stats.seen += 1
        page = existing.get(event.external_id)
        if page is None:
            notion.create_page(schedule_id, new_event_properties(source, event, now))
            stats.created += 1
            continue
        props, preserved = update_event_properties(source, event, page, now)
        # Avoid a write only when absolutely nothing changed; health fields normally do.
        notion.update_page(page["id"], props)
        stats.updated += 1
        stats.preserved += preserved
    handle_missing(notion, source, existing, seen_ids, stats)
    update_source_health(
        notion,
        source,
        "Success",
        f"Synced {len(events)} event occurrences",
        len(events),
        now,
    )


def validate_required_schema(notion: NotionClient, data_source_id: str, names: Iterable[str]) -> None:
    data = notion.request(
        "GET", f"https://api.notion.com/v1/data_sources/{data_source_id}"
    )
    properties = data.get("properties", {})
    missing = [name for name in names if name not in properties]
    if missing:
        raise RuntimeError(f"Database is missing required properties: {', '.join(missing)}")


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    notion = NotionClient(NOTION_TOKEN)
    schedule_id = notion.data_source_id(SCHEDULE_DATABASE_ID)
    sources_id = notion.data_source_id(CALENDAR_SOURCES_DATABASE_ID)
    runs_id = notion.data_source_id(SYNC_RUNS_DATABASE_ID) if SYNC_RUNS_DATABASE_ID else None
    validate_required_schema(
        notion,
        schedule_id,
        (name for key, name in SCHEDULE.items() if key != "status"),
    )
    validate_required_schema(notion, sources_id, SOURCES.values())

    sources = load_sources(notion, sources_id)
    stats = Stats(sources=len(sources))
    if not sources:
        stats.details.append("No enabled Calendar Sources with an ICS URL.")

    for source in sources:
        try:
            sync_source(notion, schedule_id, source, stats)
            stats.details.append(f"{source.name}: success")
            print(f"SUCCESS {source.name}")
        except Exception as exc:  # continue so one broken feed does not block the rest
            stats.errors += 1
            message = f"{source.name}: {type(exc).__name__}: {exc}"
            stats.details.append(message)
            print(f"ERROR {message}", file=sys.stderr)
            try:
                update_source_health(
                    notion,
                    source,
                    "Error",
                    str(exc)[:1900],
                    0,
                    datetime.now(timezone.utc).isoformat(),
                )
            except Exception as health_exc:
                print(f"ERROR could not update source health: {health_exc}", file=sys.stderr)

    finished = datetime.now(timezone.utc).isoformat()
    result = "Success" if stats.errors == 0 else ("Partial" if stats.errors < stats.sources else "Error")
    try:
        log_run(notion, runs_id, started, finished, result, stats)
    except Exception as exc:
        print(f"ERROR could not create Sync Runs entry: {exc}", file=sys.stderr)
        stats.errors += 1

    print(
        json.dumps(
            {
                "result": result,
                "sources": stats.sources,
                "seen": stats.seen,
                "created": stats.created,
                "updated": stats.updated,
                "preserved": stats.preserved,
                "marked_missing": stats.missing,
                "errors": stats.errors,
            },
            indent=2,
        )
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
