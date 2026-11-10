"""Organizer dashboard and volunteer-review operations backed by JSON storage."""

from collections.abc import Mapping
import math
from datetime import date, datetime, time as time_value, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from data_store import load, save
from event_service import EVENTS_FILE as EVENT_RECORDS_FILE, create_event, validate_event_data
from formatting import format_decimal


EVENTS_FILE = "events.json"
APPLICATIONS_FILE = "applications.json"
USERS_FILE = "users.json"
APPLICATION_STATUSES = {"pending", "accepted", "rejected"}


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"'{field}' must be a non-empty string.")
    return value.strip()


def _load_owned_event(organizer_id: str, event_id: str) -> tuple[dict, dict, dict]:
    organizer_id = _required_text(organizer_id, "organizer_id")
    event_id = _required_text(event_id, "event_id")

    events = load(EVENTS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")
    event = events.get(event_id)
    if not isinstance(event, dict) or event.get("organizer") != organizer_id:
        raise KeyError(event_id)

    applications = load(APPLICATIONS_FILE)
    if not isinstance(applications, dict):
        raise ValueError("applications.json must contain an object keyed by event ID.")
    event_applications = applications.get(event_id, {})
    if not isinstance(event_applications, dict):
        raise ValueError(f"Applications for event '{event_id}' must be an object.")
    return event, applications, event_applications


def _validated_status(application: Mapping[str, Any], volunteer_id: str) -> str:
    status = application.get("status")
    if not isinstance(status, str) or status.casefold() not in APPLICATION_STATUSES:
        raise ValueError(f"Application for '{volunteer_id}' has an invalid status.")
    return status.casefold()


def _application_counts(event_applications: Mapping[str, Any]) -> dict[str, int]:
    counts = {status: 0 for status in APPLICATION_STATUSES}
    for volunteer_id, application in event_applications.items():
        if not isinstance(application, Mapping):
            raise ValueError(f"Application for '{volunteer_id}' must be an object.")
        counts[_validated_status(application, volunteer_id)] += 1
    return counts


def event_calendar_date(event: Mapping[str, Any]) -> date | None:
    """Return the event's calendar date, or None when no date was recorded."""
    for field in ("date", "event_date", "starts_at", "start_at", "start_date"):
        value = event.get(field)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise ValueError(f"Event '{field}' must be an ISO date or timestamp.")
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
        except ValueError as error:
            raise ValueError(f"Event '{field}' must be a valid ISO date or timestamp.") from error
    return None


def is_event_day(event: Mapping[str, Any], today: date | None = None) -> bool:
    """Whether the event's recorded calendar date is today."""
    event_date = event_calendar_date(event)
    return event_date is not None and event_date == (today or date.today())


def get_organizer_events(organizer_id: str) -> list[dict[str, Any]]:
    """List an organizer's posted events with application counts."""
    organizer_id = _required_text(organizer_id, "organizer_id")
    events = load(EVENTS_FILE)
    applications = load(APPLICATIONS_FILE)
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")
    if not isinstance(applications, dict):
        raise ValueError("applications.json must contain an object keyed by event ID.")

    results = []
    for event_id, event in events.items():
        if not isinstance(event_id, str) or not isinstance(event, dict):
            raise ValueError("Each event must be an object keyed by a string ID.")
        if event.get("organizer") != organizer_id:
            continue

        event_applications = applications.get(event_id, {})
        if not isinstance(event_applications, dict):
            raise ValueError(f"Applications for event '{event_id}' must be an object.")
        counts = _application_counts(event_applications)
        slots_needed = event.get("slots_needed")
        if (
            not isinstance(slots_needed, int)
            or isinstance(slots_needed, bool)
            or slots_needed <= 0
        ):
            raise ValueError(f"Event '{event_id}' must have a positive slots_needed value.")

        event_date = event_calendar_date(event)
        results.append({
            "event_id": event_id,
            "event": event,
            "event_date": event_date.isoformat() if event_date else None,
            "pending_count": counts["pending"],
            "accepted_count": counts["accepted"],
            "rejected_count": counts["rejected"],
            "slots_remaining": max(0, slots_needed - counts["accepted"]),
        })

    return sorted(
        results,
        key=lambda item: (
            item["event_date"] or "9999-12-31",
            item["event"].get("name", "").casefold()
            if isinstance(item["event"].get("name"), str)
            else "",
        ),
    )


def _event_form_payload(organizer_id: str, form: Mapping[str, Any]) -> dict[str, Any]:
    """Parse and validate the organizer form into the shared event data shape."""
    organizer_id = _required_text(organizer_id, "organizer_id")

    def text_field(name: str, label: str, limit: int) -> str:
        value = form.get(name)
        value = _required_text(value, label)
        if len(value) > limit:
            raise ValueError(f"'{label}' must be {limit} characters or fewer.")
        return value

    skills_text = form.get("required_skills")
    if not isinstance(skills_text, str) or not skills_text.strip():
        raise ValueError("Add at least one required skill and rating.")
    required_skills: dict[str, int] = {}
    for line_number, line in enumerate(skills_text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        skill, separator, rating_text = line.rpartition(":")
        skill = skill.strip()
        rating_text = rating_text.strip()
        if not separator or not skill or not rating_text.isdigit():
            raise ValueError(
                f"Required skill line {line_number} must use the format 'Skill: rating'."
            )
        required_skills[skill] = int(rating_text)
    if not required_skills:
        raise ValueError("Add at least one required skill and rating.")

    date_text = form.get("date")
    if not isinstance(date_text, str):
        raise ValueError("'date' must be a valid date.")
    try:
        event_date = date.fromisoformat(date_text.strip())
    except ValueError as error:
        raise ValueError("'date' must be a valid date.") from error

    start_time_text = form.get("start_time", "09:00")
    if not isinstance(start_time_text, str):
        raise ValueError("'start_time' must be a valid time.")
    try:
        start_time = time_value.fromisoformat(start_time_text.strip())
    except ValueError as error:
        raise ValueError("'start_time' must be a valid time.") from error
    if start_time.tzinfo is not None:
        raise ValueError("'start_time' must not include a timezone.")

    duration_text = form.get("duration")
    try:
        duration_hours = float(duration_text)
    except (TypeError, ValueError) as error:
        raise ValueError("'duration' must be a number of hours.") from error
    if not math.isfinite(duration_hours) or not 0 < duration_hours <= 72:
        raise ValueError("'duration' must be more than 0 and no more than 72 hours.")

    seats_text = form.get("seats")
    if not isinstance(seats_text, str) or not seats_text.strip().isdigit():
        raise ValueError("'seats' must be a positive whole number.")
    seats = int(seats_text.strip())
    if not 0 < seats <= 10000:
        raise ValueError("'seats' must be between 1 and 10000.")

    starts_at = datetime.combine(
        event_date, start_time, tzinfo=ZoneInfo("Asia/Kolkata")
    )
    ends_at = starts_at + timedelta(hours=duration_hours)
    return {
        "name": text_field("title", "title", 120),
        "description": text_field("description", "description", 3000),
        "organizer": organizer_id,
        "required_skills": required_skills,
        "slots_needed": seats,
        "department": text_field("department", "department", 100),
        "type": text_field("event_type", "event type", 100),
        "location": text_field("venue", "venue", 160),
        "date": event_date.isoformat(),
        "starts_at": starts_at.isoformat(),
        "ends_at": ends_at.isoformat(),
        "duration_hours": duration_hours,
    }


def organizer_event_form_values(event: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Build form values from an existing event, or return create-form defaults."""
    if not isinstance(event, Mapping):
        return {"start_time": "09:00", "duration": "1"}
    start_value = event.get("starts_at")
    starts_at = None
    if isinstance(start_value, str):
        try:
            starts_at = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
            if starts_at.tzinfo is None:
                starts_at = starts_at.replace(tzinfo=timezone.utc)
            starts_at = starts_at.astimezone(ZoneInfo("Asia/Kolkata"))
        except ValueError:
            starts_at = None
    event_date = event.get("date")
    if not isinstance(event_date, str) and starts_at is not None:
        event_date = starts_at.date().isoformat()
    duration = event.get("duration_hours")
    if not isinstance(duration, (int, float)) and starts_at is not None:
        ends_value = event.get("ends_at")
        if isinstance(ends_value, str):
            try:
                ends_at = datetime.fromisoformat(ends_value.replace("Z", "+00:00"))
                duration = (ends_at - starts_at).total_seconds() / 3600
            except ValueError:
                duration = 1
    if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
        duration = 1
    skills = event.get("required_skills", {})
    skill_lines = (
        "\n".join(f"{skill}: {rating}" for skill, rating in skills.items())
        if isinstance(skills, Mapping)
        else ""
    )
    return {
        "title": str(event.get("name", "")),
        "description": str(event.get("description", "")),
        "required_skills": skill_lines,
        "department": str(event.get("department", event.get("dept", ""))),
        "event_type": str(event.get("type", event.get("event_type", ""))),
        "date": event_date if isinstance(event_date, str) else "",
        "start_time": starts_at.strftime("%H:%M") if starts_at else "09:00",
        "venue": str(event.get("location", event.get("venue", ""))),
        "duration": format_decimal(duration),
        "seats": str(event.get("slots_needed", "")),
    }


def create_organizer_event(organizer_id: str, form: Mapping[str, Any]) -> str:
    """Validate and create an event owned by the signed-in organizer."""
    return create_event(_event_form_payload(organizer_id, form))


def get_organizer_event(organizer_id: str, event_id: str) -> dict[str, Any]:
    """Return an event only when it belongs to the requested organizer."""
    event, _events, _applications = _load_owned_event(organizer_id, event_id)
    return event


def update_organizer_event(
    organizer_id: str, event_id: str, form: Mapping[str, Any]
) -> None:
    """Update an owned event while preserving its ID, status, and creation time."""
    event, events, _event_applications = _load_owned_event(organizer_id, event_id)
    payload = validate_event_data(_event_form_payload(organizer_id, form))
    payload["status"] = event.get("status", "open")
    if payload["status"] not in ("open", "completed"):
        raise ValueError(f"Event '{event_id}' has an invalid status.")
    payload["created_at"] = event.get("created_at")
    updated_event = dict(event)
    updated_event.update(payload)
    events[event_id] = updated_event
    save(EVENT_RECORDS_FILE, events)


def get_event_applicants(organizer_id: str, event_id: str) -> dict[str, Any]:
    """Return an owned event and its applicants' safe profile details."""
    event, _applications, event_applications = _load_owned_event(organizer_id, event_id)
    users = load(USERS_FILE)
    if not isinstance(users, dict):
        raise ValueError("users.json must contain an object keyed by username.")

    applicants = []
    for volunteer_id, application in event_applications.items():
        if not isinstance(volunteer_id, str) or not isinstance(application, Mapping):
            raise ValueError("Each application must be an object keyed by username.")
        status = _validated_status(application, volunteer_id)
        user = users.get(volunteer_id, {})
        if not isinstance(user, Mapping):
            raise ValueError(f"User '{volunteer_id}' must be an object.")
        skills = user.get("skills", {})
        if not isinstance(skills, Mapping):
            raise ValueError(f"User '{volunteer_id}' must have a skills object.")
        skill_items = []
        for skill, rating in skills.items():
            if (
                not isinstance(skill, str)
                or not skill.strip()
                or not isinstance(rating, int)
                or isinstance(rating, bool)
                or rating < 0
            ):
                raise ValueError(f"User '{volunteer_id}' has invalid skill data.")
            skill_items.append((skill, rating))

        attended = application.get("attended", False)
        if not isinstance(attended, bool):
            raise ValueError(f"Application for '{volunteer_id}' has invalid attendance data.")
        role = application.get("role")
        if role is not None and not isinstance(role, str):
            raise ValueError(f"Application for '{volunteer_id}' has an invalid assigned role.")
        applicants.append({
            "username": volunteer_id,
            "name": user.get("name") if isinstance(user.get("name"), str) else volunteer_id,
            "department": user.get("dept") if isinstance(user.get("dept"), str) else None,
            "skill_items": sorted(skill_items, key=lambda item: item[0].casefold()),
            "status": status,
            "role": role,
            "attended": attended,
            "attended_on": application.get("attended_on"),
        })

    status_order = {"pending": 0, "accepted": 1, "rejected": 2}
    applicants.sort(key=lambda applicant: (
        status_order[applicant["status"]],
        applicant["name"].casefold(),
    ))
    event_date = event_calendar_date(event)
    counts = _application_counts(event_applications)
    slots_needed = event.get("slots_needed")
    if (
        not isinstance(slots_needed, int)
        or isinstance(slots_needed, bool)
        or slots_needed <= 0
    ):
        raise ValueError(f"Event '{event_id}' must have a positive slots_needed value.")

    return {
        "event_id": event_id,
        "event": event,
        "event_date": event_date.isoformat() if event_date else None,
        "is_event_day": is_event_day(event),
        "slots_remaining": max(0, slots_needed - counts["accepted"]),
        "applicants": applicants,
    }


def update_application_status(
    organizer_id: str,
    event_id: str,
    volunteer_id: str,
    status: str,
    role: str | None = None,
) -> None:
    """Accept or reject an applicant, assigning a role and enforcing capacity."""
    volunteer_id = _required_text(volunteer_id, "volunteer_id")
    if not isinstance(status, str) or status.strip().casefold() not in {"accepted", "rejected"}:
        raise ValueError("Choose whether to accept or reject the application.")
    new_status = status.strip().casefold()
    if new_status == "accepted":
        role = _required_text(role, "role")
        if len(role) > 80:
            raise ValueError("The assigned role must be 80 characters or fewer.")
    else:
        role = None

    event, applications, event_applications = _load_owned_event(organizer_id, event_id)
    application = event_applications.get(volunteer_id)
    if not isinstance(application, dict):
        raise ValueError(f"Application for '{volunteer_id}' was not found.")
    current_status = _validated_status(application, volunteer_id)
    if application.get("attended") is True and new_status != "accepted":
        raise ValueError("Attendance has been recorded; this application cannot be rejected.")

    slots_needed = event.get("slots_needed")
    if (
        not isinstance(slots_needed, int)
        or isinstance(slots_needed, bool)
        or slots_needed <= 0
    ):
        raise ValueError(f"Event '{event_id}' must have a positive slots_needed value.")
    counts = _application_counts(event_applications)
    if new_status == "accepted" and current_status != "accepted":
        if counts["accepted"] >= slots_needed:
            raise ValueError(f"Event '{event_id}' has no volunteer slots remaining.")

    application["status"] = new_status
    application["role"] = role
    event_applications[volunteer_id] = application
    applications[event_id] = event_applications
    save(APPLICATIONS_FILE, applications)


def mark_attendance(
    organizer_id: str,
    event_id: str,
    volunteer_id: str,
    today: date | None = None,
) -> bool:
    """Mark an accepted volunteer present on the event's calendar day."""
    volunteer_id = _required_text(volunteer_id, "volunteer_id")
    event, applications, event_applications = _load_owned_event(organizer_id, event_id)
    application = event_applications.get(volunteer_id)
    if not isinstance(application, dict):
        raise ValueError(f"Application for '{volunteer_id}' was not found.")
    if _validated_status(application, volunteer_id) != "accepted":
        raise ValueError("Only accepted volunteers can have attendance recorded.")

    event_date = event_calendar_date(event)
    if event_date is None:
        raise ValueError("Add an event date before recording attendance.")
    current_date = today or date.today()
    if not isinstance(current_date, date) or isinstance(current_date, datetime):
        raise ValueError("'today' must be a calendar date.")
    if event_date != current_date:
        raise ValueError(f"Attendance can only be recorded on {event_date.isoformat()}.")

    if application.get("attended") is True:
        return False
    application["attended"] = True
    application["attended_on"] = current_date.isoformat()
    event_applications[volunteer_id] = application
    applications[event_id] = event_applications
    save(APPLICATIONS_FILE, applications)
    return True
