from datetime import datetime, timezone

import pytest


@pytest.fixture(autouse=True)
def required_environment(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "test")
    monkeypatch.setenv("SCHEDULE_DATABASE_ID", "schedule")
    monkeypatch.setenv("CALENDAR_SOURCES_DATABASE_ID", "sources")


def test_canvas_assignment_type_detects_quiz_links_and_names():
    from sync_canvas_to_notion import assignment_type

    assert assignment_type({"id": 42, "name": "Week 3 Check"}, {42}) == "Test"
    assert assignment_type({"id": 43, "name": "Midterm 1"}, set()) == "Test"
    assert assignment_type({"id": 44, "name": "Problem Set 4"}, set()) == "Homework"


def test_dates_match_ignores_unrelated_keys():
    from sync_ics_to_notion import dates_match

    left = {"start": "2026-09-18T10:00:00-04:00", "end": None, "time_zone": None}
    right = {"start": "2026-09-18T10:00:00-04:00", "end": None}
    assert dates_match(left, right)


def test_window_check_for_all_day_and_datetime():
    from sync_ics_to_notion import iso_in_window

    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert iso_in_window("2026-09-18", start, end)
    assert iso_in_window("2026-09-18T10:00:00-04:00", start, end)
    assert not iso_in_window("2026-11-01", start, end)


def test_personal_name_and_date_are_preserved():
    from sync_ics_to_notion import Event, SourceConfig, update_event_properties

    source = SourceConfig(
        page_id="source-1",
        name="Team",
        ics_url="https://example.com/team.ics",
        area="Lacrosse",
        commitment="Committed",
        item_type="Event",
        past_days=7,
        future_days=180,
        update_mode="Preserve personal edits",
        missing_policy="Mark missing",
    )
    page = {
        "properties": {
            "Name": {"title": [{"plain_text": "Practice — leaving early"}]},
            "Date": {"date": {"start": "2026-09-18T16:00:00-04:00", "end": "2026-09-18T17:00:00-04:00"}},
            "Source Name": {"rich_text": [{"plain_text": "Practice"}]},
            "Source Date": {"date": {"start": "2026-09-18T16:00:00-04:00", "end": "2026-09-18T18:00:00-04:00"}},
            "Type": {"select": {"name": "Event"}},
            "Commitment": {"select": {"name": "Committed"}},
        }
    }
    event = Event(
        external_id="ics:1",
        name="Practice (field changed)",
        start="2026-09-18T16:30:00-04:00",
        end="2026-09-18T18:30:00-04:00",
        event_url=None,
        status="ACTIVE",
        fingerprint="abc",
    )
    props, preserved = update_event_properties(
        source, event, page, "2026-09-18T12:00:00+00:00"
    )
    assert "Name" not in props
    assert "Date" not in props
    assert props["Source Name"]["rich_text"][0]["text"]["content"] == event.name
    assert preserved == 2


def test_unedited_personal_fields_follow_source_change():
    from sync_ics_to_notion import Event, SourceConfig, update_event_properties

    source = SourceConfig(
        page_id="source-1",
        name="Team",
        ics_url="https://example.com/team.ics",
        area="Lacrosse",
        commitment="Committed",
        item_type="Event",
        past_days=7,
        future_days=180,
        update_mode="Preserve personal edits",
        missing_policy="Mark missing",
    )
    old_date = {"start": "2026-09-18T16:00:00-04:00", "end": "2026-09-18T18:00:00-04:00"}
    page = {
        "properties": {
            "Name": {"title": [{"plain_text": "Practice"}]},
            "Date": {"date": old_date},
            "Source Name": {"rich_text": [{"plain_text": "Practice"}]},
            "Source Date": {"date": old_date},
            "Type": {"select": {"name": "Event"}},
            "Commitment": {"select": {"name": "Committed"}},
        }
    }
    event = Event(
        external_id="ics:1",
        name="Practice (new field)",
        start="2026-09-18T17:00:00-04:00",
        end="2026-09-18T19:00:00-04:00",
        event_url=None,
        status="ACTIVE",
        fingerprint="def",
    )
    props, preserved = update_event_properties(
        source, event, page, "2026-09-18T12:00:00+00:00"
    )
    assert props["Name"]["title"][0]["text"]["content"] == event.name
    assert props["Date"]["date"]["start"] == event.start
    assert preserved == 0


def test_recurring_occurrences_receive_distinct_ids():
    from sync_ics_to_notion import SourceConfig, parse_events

    source = SourceConfig(
        page_id="source-1",
        name="Team",
        ics_url="https://example.com/team.ics",
        area="Lacrosse",
        commitment="Committed",
        item_type="Event",
        past_days=5000,
        future_days=5000,
        update_mode="Preserve personal edits",
        missing_policy="Mark missing",
    )
    body = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:practice@example.com\r
DTSTART:20260918T200000Z\r
DTEND:20260918T220000Z\r
RRULE:FREQ=DAILY;COUNT=2\r
SUMMARY:Practice\r
END:VEVENT\r
END:VCALENDAR\r
"""
    events = parse_events(source, body)
    assert len(events) == 2
    assert len({event.external_id for event in events}) == 2


def test_all_day_event_stays_all_day():
    from sync_ics_to_notion import SourceConfig, parse_events

    source = SourceConfig(
        page_id="source-1",
        name="Holidays",
        ics_url="https://example.com/holidays.ics",
        area=None,
        commitment="Maybe",
        item_type="Event",
        past_days=5000,
        future_days=5000,
        update_mode="Preserve personal edits",
        missing_policy="Mark missing",
    )
    body = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:holiday@example.com\r
DTSTART;VALUE=DATE:20260918\r
DTEND;VALUE=DATE:20260919\r
SUMMARY:Holiday\r
END:VEVENT\r
END:VCALENDAR\r
"""
    events = parse_events(source, body)
    assert events[0].start == "2026-09-18"
    assert events[0].end == "2026-09-19"
