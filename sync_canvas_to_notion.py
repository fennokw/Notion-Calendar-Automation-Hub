#!/usr/bin/env python3
"""Import active Canvas assignments into the shared Schedule & Tasks data source."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

from config import NOTION_VERSION, SCHEDULE
from sync_ics_to_notion import (
    NotionClient,
    checkbox_value,
    date_value,
    get_select,
    get_status,
    select_value,
    status_value,
    text_value,
    title_value,
    url_value,
)

CANVAS_BASE_URL = os.getenv("CANVAS_BASE_URL", "https://canvas.mit.edu").rstrip("/")
CANVAS_TOKEN = os.environ["CANVAS_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
SCHEDULE_DATABASE_ID = os.environ["SCHEDULE_DATABASE_ID"]
TIMEOUT = 30
TEST_WORDS = ("quiz", "midterm", "exam", "final", "test")


def canvas_get(path: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    url: Optional[str] = f"{CANVAS_BASE_URL}{path}"
    next_params = params
    while url:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {CANVAS_TOKEN}"},
            params=next_params,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError(f"Canvas returned a non-list response for {path}")
        results.extend(data)
        url = response.links.get("next", {}).get("url")
        next_params = None
    return results


def active_courses() -> list[dict[str, Any]]:
    courses = canvas_get(
        "/api/v1/courses",
        {"per_page": 100, "enrollment_state": "active", "state[]": "available"},
    )
    return [course for course in courses if course.get("id") and course.get("name")]


def assignments(course_id: int) -> list[dict[str, Any]]:
    return canvas_get(
        f"/api/v1/courses/{course_id}/assignments",
        {
            "per_page": 100,
            "include[]": "submission",
            "override_assignment_dates": "true",
            "order_by": "due_at",
        },
    )


def quizzes(course_id: int) -> list[dict[str, Any]]:
    return canvas_get(
        f"/api/v1/courses/{course_id}/quizzes",
        {"per_page": 100},
    )


def assignment_type(item: dict[str, Any], quiz_assignment_ids: set[int]) -> str:
    assignment_id = item.get("id")
    if assignment_id in quiz_assignment_ids:
        return "Test"
    lowered = (item.get("name") or "").lower()
    return "Test" if any(word in lowered for word in TEST_WORDS) else "Homework"


def in_window(due_at: Optional[str]) -> bool:
    if not due_at:
        return False
    due = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    return now - timedelta(days=45) <= due <= now + timedelta(days=365)


def existing_assignments(notion: NotionClient, schedule_id: str) -> dict[str, dict[str, Any]]:
    pages = notion.query(
        schedule_id,
        {"property": SCHEDULE["import_source"], "select": {"equals": "Canvas"}},
    )
    result: dict[str, dict[str, Any]] = {}
    for page in pages:
        prop = page.get("properties", {}).get(SCHEDULE["canvas_assignment_id"], {})
        value = "".join(x.get("plain_text", "") for x in prop.get("rich_text", []))
        if value:
            result[value] = page
    return result


def properties(
    item: dict[str, Any],
    course: dict[str, Any],
    existing: Optional[dict[str, Any]],
    kind: str,
    *,
    record_key: Optional[str] = None,
) -> dict[str, Any]:
    commitment = get_select(existing, SCHEDULE["commitment"]) if existing else None
    commitment = commitment or "Maybe"
    submission = item.get("submission") or {}
    done = submission.get("workflow_state") in {"submitted", "graded"}
    name = " ".join((item.get("name") or "Untitled assignment").split())
    return {
        SCHEDULE["name"]: title_value(name, strikethrough=commitment == "No" or done),
        SCHEDULE["date"]: date_value(item["due_at"]),
        SCHEDULE["type"]: select_value(kind),
        SCHEDULE["commitment"]: select_value(commitment),
        SCHEDULE["area"]: {"multi_select": [{"name": "School"}]},
        SCHEDULE["notes"]: text_value(course.get("name") or "Canvas"),
        SCHEDULE["external_id"]: text_value(f"canvas:{item['id']}"),
        SCHEDULE["source_name"]: text_value(name),
        SCHEDULE["source_date"]: date_value(item["due_at"]),
        SCHEDULE["source_url"]: url_value(item.get("html_url")),
        SCHEDULE["source_last_seen"]: date_value(datetime.now(timezone.utc).isoformat()),
        SCHEDULE["source_status"]: select_value("Active"),
        SCHEDULE["source_fingerprint"]: text_value(str(item.get("updated_at") or item["due_at"])),
        SCHEDULE["sync_managed"]: checkbox_value(True),
        SCHEDULE["import_source"]: select_value("Canvas"),
        SCHEDULE["canvas_assignment_id"]: text_value(record_key or str(item["id"])),
        SCHEDULE["canvas_course_id"]: text_value(str(course["id"])),
    }


def main() -> int:
    notion = NotionClient(NOTION_TOKEN)
    schedule_id = notion.data_source_id(SCHEDULE_DATABASE_ID)
    existing = existing_assignments(notion, schedule_id)
    created = updated = skipped = errors = 0
    courses = active_courses()
    for course in courses:
        course_id = int(course["id"])
        try:
            course_quizzes = quizzes(course_id)
        except Exception as exc:
            errors += 1
            course_quizzes = []
            print(f"ERROR {course.get('name')}: quizzes: {exc}", file=sys.stderr)

        quiz_assignment_ids = {
            int(quiz["assignment_id"])
            for quiz in course_quizzes
            if quiz.get("assignment_id") is not None
        }

        for item in assignments(course_id):
            if item.get("published") is False or not in_window(item.get("due_at")):
                skipped += 1
                continue
            key = str(item["id"])
            try:
                kind = assignment_type(item, quiz_assignment_ids)
                props = properties(item, course, existing.get(key), kind)
                if key in existing:
                    notion.update_page(existing[key]["id"], props)
                    updated += 1
                else:
                    notion.create_page(schedule_id, props)
                    created += 1
            except Exception as exc:
                errors += 1
                print(f"ERROR {course.get('name')}: {item.get('name')}: {exc}", file=sys.stderr)

        # Some Canvas quizzes are not backed by an Assignment object. Import
        # those directly so tests/quizzes cannot silently disappear.
        for quiz in course_quizzes:
            if quiz.get("assignment_id") is not None:
                continue
            due_at = quiz.get("due_at")
            if quiz.get("published") is False or not in_window(due_at):
                skipped += 1
                continue
            key = f"quiz:{quiz['id']}"
            quiz_item = {
                "id": quiz["id"],
                "name": quiz.get("title") or "Untitled quiz",
                "due_at": due_at,
                "html_url": quiz.get("html_url"),
                "updated_at": quiz.get("updated_at") or due_at,
                "submission": {},
            }
            try:
                props = properties(
                    quiz_item,
                    course,
                    existing.get(key),
                    "Test",
                    record_key=key,
                )
                props[SCHEDULE["external_id"]] = text_value(f"canvas:{key}")
                if key in existing:
                    notion.update_page(existing[key]["id"], props)
                    updated += 1
                else:
                    notion.create_page(schedule_id, props)
                    created += 1
            except Exception as exc:
                errors += 1
                print(f"ERROR {course.get('name')}: {quiz_item.get('name')}: {exc}", file=sys.stderr)
    print(json.dumps({"courses": len(courses), "created": created, "updated": updated, "skipped": skipped, "errors": errors}, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
