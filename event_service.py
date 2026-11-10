"""Event creation backed by the shared JSON data store."""

import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from auth_service import check_eligibility
from data_store import load, save


EVENTS_FILE = "events.json"
APPLICATIONS_FILE = "applications.json"
USERS_FILE = "users.json"
REQUIRED_FIELDS = (
    "name",
    "description",
    "organizer",
    "required_skills",
    "slots_needed",
)


def create_event(data: Mapping[str, Any]) -> str:
    """Validate and save an event, returning its generated event ID."""
    if not isinstance(data, Mapping):
        raise ValueError("Event data must be a mapping of field names to values.")

    missing_fields = [field for field in REQUIRED_FIELDS if field not in data]
    if missing_fields:
        raise ValueError(
            "Missing required event field(s): " + ", ".join(missing_fields) + "."
        )

    cleaned_text: dict[str, str] = {}
    for field in ("name", "description", "organizer"):
        value = data[field]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"'{field}' must be a non-empty string.")
        cleaned_text[field] = value.strip()

    raw_skills = data["required_skills"]
    if not isinstance(raw_skills, Mapping):
        raise ValueError(
            "'required_skills' must map skill names to non-negative whole-number ratings."
        )

    required_skills: dict[str, int] = {}
    seen_skills: set[str] = set()
    for skill, rating in raw_skills.items():
        if not isinstance(skill, str) or not skill.strip():
            raise ValueError(
                "'required_skills' must map non-empty skill names to "
                "non-negative whole-number ratings."
            )
        normalized_skill = skill.strip()
        if normalized_skill.casefold() in seen_skills:
            raise ValueError("Required skill names must be unique.")
        if not isinstance(rating, int) or isinstance(rating, bool) or rating < 0:
            raise ValueError(
                "'required_skills' ratings must be non-negative whole numbers."
            )
        seen_skills.add(normalized_skill.casefold())
        required_skills[normalized_skill] = rating

    slots_needed = data["slots_needed"]
    if (
        not isinstance(slots_needed, int)
        or isinstance(slots_needed, bool)
        or slots_needed <= 0
    ):
        raise ValueError("'slots_needed' must be a positive whole number.")

    status = data.get("status", "open")
    if status not in ("open", "completed"):
        raise ValueError("'status' must be 'open' or 'completed'.")

    events = load(EVENTS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")

    existing_ids = (
        int(match.group(1))
        for event_id in events
        if isinstance(event_id, str)
        if (match := re.fullmatch(r"e(\d+)", event_id))
    )
    event_id = f"e{max(existing_ids, default=0) + 1}"

    events[event_id] = {
        "name": cleaned_text["name"],
        "description": cleaned_text["description"],
        "organizer": cleaned_text["organizer"],
        "required_skills": required_skills,
        "slots_needed": slots_needed,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save(EVENTS_FILE, events)
    return event_id


def _normalize_filter(value: str | None, field: str) -> str | None:
    """Normalize an optional text filter, rejecting unexpected input types."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"'{field}' filter must be a string.")
    normalized = value.strip().casefold()
    return normalized or None


def find_eligible_events(
    user: Mapping[str, Any],
    search: str | None = None,
    skill: str | None = None,
    department: str | None = None,
    type: str | None = None,
    date: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Return open events the user qualifies for as (event_id, event) pairs."""
    if not isinstance(user, Mapping):
        raise ValueError("User must be a mapping containing a skills mapping.")

    search_filter = _normalize_filter(search, "search")
    skill_filter = _normalize_filter(skill, "skill")
    department_filter = _normalize_filter(department, "department")
    type_filter = _normalize_filter(type, "type")
    date_filter = _normalize_filter(date, "date")

    events = load(EVENTS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")

    user_skills = user.get("skills", {})
    results: list[tuple[str, dict[str, Any]]] = []

    for event_id, event in events.items():
        if not isinstance(event_id, str) or not isinstance(event, dict):
            raise ValueError("Each event must be an object keyed by a string ID.")
        if event.get("status") != "open":
            continue

        required_skills = event.get("required_skills")
        if not isinstance(required_skills, Mapping):
            raise ValueError(
                f"Event '{event_id}' must contain required_skills as an object."
            )
        if not check_eligibility(user_skills, required_skills):
            continue

        text = " ".join(
            value
            for value in (
                event_id,
                event.get("name", ""),
                event.get("description", ""),
                event.get("organizer", ""),
            )
            if isinstance(value, str)
        ).casefold()
        if search_filter and search_filter not in text:
            continue

        if skill_filter and not any(
            skill_filter in required_skill.casefold()
            for required_skill in required_skills
            if isinstance(required_skill, str)
        ):
            continue

        event_department = event.get("department", event.get("dept"))
        if department_filter and (
            not isinstance(event_department, str)
            or event_department.strip().casefold() != department_filter
        ):
            continue

        event_type = event.get("type", event.get("event_type"))
        if type_filter and (
            not isinstance(event_type, str)
            or event_type.strip().casefold() != type_filter
        ):
            continue

        event_date = event.get("date", event.get("event_date"))
        if date_filter and (
            not isinstance(event_date, str)
            or not event_date.strip().casefold().startswith(date_filter)
        ):
            continue

        results.append((event_id, event))

    return results


def _as_utc_datetime(value: Any) -> datetime | None:
    """Parse supported event timestamps and treat naive values as UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _first_event_value(event: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-null value from supported event field aliases."""
    return next((event[key] for key in keys if event.get(key) is not None), None)


def _remaining_seats(event: Mapping[str, Any]) -> int | None:
    """Derive remaining seats from explicit counts or accepted applications."""
    explicit_remaining = _first_event_value(
        event, ("seats_left", "remaining_seats", "seats_remaining")
    )
    if isinstance(explicit_remaining, int) and not isinstance(explicit_remaining, bool):
        return explicit_remaining

    slots_needed = event.get("slots_needed")
    if (
        not isinstance(slots_needed, int)
        or isinstance(slots_needed, bool)
        or slots_needed < 0
    ):
        return None

    accepted_count = _first_event_value(
        event, ("accepted_count", "accepted_applications", "seats_filled", "filled_slots")
    )
    if isinstance(accepted_count, int) and not isinstance(accepted_count, bool):
        return slots_needed - accepted_count if accepted_count >= 0 else None

    applications = _first_event_value(event, ("applications", "applicants"))
    if isinstance(applications, Mapping):
        application_records = applications.values()
    elif isinstance(applications, (list, tuple)):
        application_records = applications
    else:
        return None

    accepted_count = sum(
        1
        for application in application_records
        if isinstance(application, Mapping)
        and isinstance(application.get("status"), str)
        and application["status"].casefold() == "accepted"
    )
    return slots_needed - accepted_count


def get_badges(event: Mapping[str, Any], user: Mapping[str, Any]) -> list[str]:
    """Return badges derived from the supplied event and user without modifying either."""
    if not isinstance(event, Mapping) or not isinstance(user, Mapping):
        raise ValueError("Event and user must be mappings.")

    badges: list[str] = []
    required_skills = event.get("required_skills", {})
    user_skills = user.get("skills", {})
    if not isinstance(required_skills, Mapping):
        required_skills = {}
    if not isinstance(user_skills, Mapping):
        user_skills = {}

    # Great match: any skill name shared by the user and event earns the badge.
    user_skill_names = {
        skill.strip().casefold()
        for skill in user_skills
        if isinstance(skill, str) and skill.strip()
    }
    if any(
        isinstance(skill, str)
        and skill.strip()
        and skill.strip().casefold() in user_skill_names
        for skill in required_skills
    ):
        badges.append("Great match")

    now = datetime.now(timezone.utc)

    # New: the event's creation timestamp is no more than three days old.
    created_at = _as_utc_datetime(event.get("created_at"))
    if created_at is not None and timedelta(0) <= now - created_at <= timedelta(days=3):
        badges.append("New")

    start_value = _first_event_value(
        event, ("starts_at", "start_at", "start_date", "event_date", "date")
    )
    starts_at = _as_utc_datetime(start_value)
    seats_left = _remaining_seats(event)

    # Closing Soon: the start is within the next two days, or only one or two seats remain.
    starts_within_two_days = (
        starts_at is not None
        and timedelta(0) <= starts_at - now <= timedelta(days=2)
    )
    if starts_within_two_days or seats_left in (1, 2):
        badges.append("Closing Soon")

    return badges


def apply_to_event(user_id: str, event_id: str) -> str:
    """Create a pending application, returning its display status."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("'user_id' must be a non-empty string.")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("'event_id' must be a non-empty string.")
    user_id = user_id.strip()
    event_id = event_id.strip()

    events = load(EVENTS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")
    event = events.get(event_id)
    if not isinstance(event, Mapping):
        raise ValueError(f"Event '{event_id}' was not found.")
    if event.get("status") != "open":
        raise ValueError(f"Event '{event_id}' is not open for applications.")

    applications = load(APPLICATIONS_FILE)
    if not isinstance(applications, dict):
        raise ValueError("applications.json must contain an object keyed by event ID.")
    event_applications = applications.get(event_id, {})
    if not isinstance(event_applications, dict):
        raise ValueError(f"Applications for event '{event_id}' must be an object.")
    if user_id in event_applications:
        raise ValueError(f"User '{user_id}' has already applied to event '{event_id}'.")

    slots_needed = event.get("slots_needed")
    if (
        not isinstance(slots_needed, int)
        or isinstance(slots_needed, bool)
        or slots_needed <= 0
    ):
        raise ValueError(f"Event '{event_id}' must have a positive slots_needed value.")

    accepted_count = 0
    for applicant_id, application in event_applications.items():
        if not isinstance(application, Mapping):
            raise ValueError(
                f"Application for user '{applicant_id}' must be an object."
            )
        status = application.get("status")
        if isinstance(status, str) and status.casefold() == "accepted":
            accepted_count += 1
    if accepted_count >= slots_needed:
        raise ValueError(f"Event '{event_id}' is full.")

    event_applications[user_id] = {
        "status": "pending",
        "role": None,
        "hours": 0,
        "notes": "",
    }
    applications[event_id] = event_applications
    save(APPLICATIONS_FILE, applications)
    return "Applied"


def get_apply_status(user_id: str, event_id: str) -> str:
    """Return the user's application button status for an event."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("'user_id' must be a non-empty string.")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("'event_id' must be a non-empty string.")
    user_id = user_id.strip()
    event_id = event_id.strip()

    events = load(EVENTS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")
    event = events.get(event_id)
    if not isinstance(event, Mapping):
        raise ValueError(f"Event '{event_id}' was not found.")

    applications = load(APPLICATIONS_FILE)
    if not isinstance(applications, dict):
        raise ValueError("applications.json must contain an object keyed by event ID.")
    event_applications = applications.get(event_id, {})
    if not isinstance(event_applications, Mapping):
        raise ValueError(f"Applications for event '{event_id}' must be an object.")

    if user_id not in event_applications:
        return "Apply"
    application = event_applications[user_id]
    if not isinstance(application, Mapping):
        raise ValueError(f"Application for user '{user_id}' must be an object.")

    ends_at = _as_utc_datetime(event.get("ends_at"))
    if (
        isinstance(application.get("status"), str)
        and application["status"].casefold() == "accepted"
        and ends_at is not None
        and ends_at <= datetime.now(timezone.utc)
    ):
        return "Completed"
    return "Applied"


def get_saved_event_ids(user_id: str) -> list[str]:
    """Return a copy of a user's saved event IDs."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("'user_id' must be a non-empty string.")
    user_id = user_id.strip()

    users = load(USERS_FILE)
    if not isinstance(users, dict):
        raise ValueError("users.json must contain an object keyed by username.")
    user = users.get(user_id)
    if not isinstance(user, dict):
        raise ValueError(f"User '{user_id}' was not found.")

    saved_event_ids = user.get("saved_events", [])
    if not isinstance(saved_event_ids, list) or any(
        not isinstance(saved_id, str) or not saved_id.strip()
        for saved_id in saved_event_ids
    ):
        raise ValueError(f"User '{user_id}' has an invalid saved_events list.")
    return list(saved_event_ids)


def toggle_saved_event(user_id: str, event_id: str) -> bool:
    """Toggle an event in only this user's saved_events list; return its new state."""
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("'user_id' must be a non-empty string.")
    if not isinstance(event_id, str) or not event_id.strip():
        raise ValueError("'event_id' must be a non-empty string.")
    user_id = user_id.strip()
    event_id = event_id.strip()

    events = load(EVENTS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")
    if not isinstance(events.get(event_id), Mapping):
        raise ValueError(f"Event '{event_id}' was not found.")

    users = load(USERS_FILE)
    if not isinstance(users, dict):
        raise ValueError("users.json must contain an object keyed by username.")
    user = users.get(user_id)
    if not isinstance(user, dict):
        raise ValueError(f"User '{user_id}' was not found.")

    saved_event_ids = user.get("saved_events", [])
    if not isinstance(saved_event_ids, list) or any(
        not isinstance(saved_id, str) or not saved_id.strip()
        for saved_id in saved_event_ids
    ):
        raise ValueError(f"User '{user_id}' has an invalid saved_events list.")

    if event_id in saved_event_ids:
        user["saved_events"] = [
            saved_id for saved_id in saved_event_ids if saved_id != event_id
        ]
        is_saved = False
    else:
        user["saved_events"] = [*saved_event_ids, event_id]
        is_saved = True

    users[user_id] = user
    save(USERS_FILE, users)
    return is_saved