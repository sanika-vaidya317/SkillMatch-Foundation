"""Event completion and volunteer certificate operations."""

from collections.abc import Mapping
from datetime import date, datetime, timezone
import math
import re
import secrets
import string
from typing import Any

from data_store import load, save
from formatting import format_decimal


EVENTS_FILE = "events.json"
APPLICATIONS_FILE = "applications.json"
USERS_FILE = "users.json"
CERTIFICATES_FILE = "certificates.json"


def _load_object(filename: str, description: str) -> dict[str, Any]:
    data = load(filename)
    if not isinstance(data, dict):
        raise ValueError(f"{filename} must contain an object {description}.")
    return data


def _safe_pdf_text(value: Any) -> str:
    """Make user-provided text safe for a basic Helvetica PDF content stream."""
    text = " ".join(str(value).split())
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _parse_certificate_date(value: Any) -> date | None:
    """Parse supported ISO date/timestamp values without inventing a fallback date."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        return value
    elif isinstance(value, str) and value.strip():
        raw_value = value.strip()
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
            except ValueError:
                return None
    else:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.date()


def _format_certificate_date(value: Any) -> str | None:
    parsed = _parse_certificate_date(value)
    if parsed is None:
        return None
    return parsed.strftime("%B %d, %Y").replace(" 0", " ")


def _event_date_value(event: Mapping[str, Any]) -> str | None:
    """Use the canonical event date first, then supported legacy scheduling fields."""
    for field in ("date", "event_date", "starts_at", "start_at", "start_date"):
        parsed = _parse_certificate_date(event.get(field))
        if parsed is not None:
            return parsed.isoformat()
    return None


def _normalize_verification_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if re.fullmatch(r"[A-Z0-9]{10}", normalized) else None


def _estimated_text_width(text: str, font_size: int, bold: bool = False) -> float:
    """Conservatively estimate Helvetica text width for line wrapping and centering."""
    width = 0.0
    for character in text:
        if character in " ilI.,:;!|'`":
            factor = 0.29
        elif character in "MW@%&":
            factor = 0.86
        elif character in "frt()[]":
            factor = 0.36
        elif character.isupper():
            factor = 0.66
        else:
            factor = 0.54
        width += factor * font_size * (1.05 if bold else 1)
    return width


def _wrap_pdf_text(text: Any, max_width: float, font_size: int, bold: bool) -> list[str]:
    normalized = " ".join(str(text).split())
    if not normalized:
        return [""]

    tokens: list[str] = []
    for word in normalized.split(" "):
        if _estimated_text_width(word, font_size, bold) <= max_width:
            tokens.append(word)
            continue
        chunk = ""
        for character in word:
            if chunk and _estimated_text_width(chunk + character, font_size, bold) > max_width:
                tokens.append(chunk)
                chunk = character
            else:
                chunk += character
        if chunk:
            tokens.append(chunk)

    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current} {token}".strip()
        if current and _estimated_text_width(candidate, font_size, bold) > max_width:
            lines.append(current)
            current = token
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fit_pdf_text(
    text: Any,
    max_width: float,
    preferred_size: int,
    minimum_size: int,
    max_lines: int,
    bold: bool = False,
) -> tuple[list[str], int]:
    for font_size in range(preferred_size, minimum_size - 1, -1):
        lines = _wrap_pdf_text(text, max_width, font_size, bold)
        if len(lines) <= max_lines:
            return lines, font_size

    font_size = minimum_size
    lines = _wrap_pdf_text(text, max_width, font_size, bold)[:max_lines]
    if lines:
        suffix = "..."
        last_line = lines[-1]
        while last_line and _estimated_text_width(last_line + suffix, font_size, bold) > max_width:
            last_line = last_line[:-1].rstrip()
        lines[-1] = last_line + suffix
    return lines, font_size


def _pdf_text_command(
    text: Any,
    x: float,
    y: float,
    font_size: int,
    font: str,
    color: tuple[float, float, float],
    centered: bool = False,
) -> str:
    normalized = " ".join(str(text).split())
    if centered:
        x -= _estimated_text_width(normalized, font_size, font == "F2") / 2
    red, green, blue = color
    return (
        f"{red:.3f} {green:.3f} {blue:.3f} rg "
        f"BT /{font} {font_size} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm "
        f"({_safe_pdf_text(normalized)}) Tj ET"
    )


def _render_certificate_pdf(
    volunteer_name: str,
    event_name: str,
    hours: int | float,
    verification_code: str,
    event_date: str | None = None,
    issue_date: str | None = None,
) -> bytes:
    """Render a designed, single-page landscape certificate without local file storage."""
    dark_green = (0.08, 0.31, 0.25)
    muted_green = (0.20, 0.43, 0.36)
    gold = (0.78, 0.58, 0.23)
    ink = (0.12, 0.16, 0.20)
    muted_ink = (0.34, 0.38, 0.40)
    commands = [
        "q",
        f"{dark_green[0]:.3f} {dark_green[1]:.3f} {dark_green[2]:.3f} RG "
        "2.2 w 22 22 748 568 re S",
        f"{gold[0]:.3f} {gold[1]:.3f} {gold[2]:.3f} RG "
        "0.8 w 32 32 728 548 re S",
        f"{gold[0]:.3f} {gold[1]:.3f} {gold[2]:.3f} RG "
        "1.2 w 124 516 m 350 516 l S 442 516 m 668 516 l S",
        f"{gold[0]:.3f} {gold[1]:.3f} {gold[2]:.3f} rg "
        "389 512 m 396 519 l 403 512 l 396 505 l h f",
        "Q",
    ]
    commands.append(_pdf_text_command(
        "CERTIFICATE OF PARTICIPATION", 396, 542, 22, "F2", dark_green, centered=True,
    ))
    commands.append(_pdf_text_command(
        "PRESENTED TO", 396, 490, 10, "F2", muted_green, centered=True,
    ))

    name_lines, name_size = _fit_pdf_text(
        volunteer_name, 670, preferred_size=34, minimum_size=22, max_lines=3, bold=True,
    )
    name_y = 462
    for line in name_lines:
        commands.append(_pdf_text_command(line, 396, name_y, name_size, "F2", ink, centered=True))
        name_y -= name_size + 3

    event_label_y = name_y - 10
    commands.append(_pdf_text_command(
        "FOR PARTICIPATION IN", 396, event_label_y, 10, "F2", muted_green, centered=True,
    ))
    event_lines, event_size = _fit_pdf_text(
        event_name, 670, preferred_size=20, minimum_size=13, max_lines=3, bold=True,
    )
    event_y = event_label_y - 28
    for line in event_lines:
        commands.append(_pdf_text_command(line, 396, event_y, event_size, "F2", ink, centered=True))
        event_y -= event_size + 2

    details_y = event_y - 7
    event_date_text = _format_certificate_date(event_date) or "Not recorded"
    issue_date_text = _format_certificate_date(issue_date) or "Not recorded"
    commands.append(_pdf_text_command(
        f"Event date: {event_date_text}", 396, details_y, 11, "F1", muted_ink, centered=True,
    ))
    commands.append(_pdf_text_command(
        f"Issue date: {issue_date_text}", 396, details_y - 19, 11, "F1", muted_ink, centered=True,
    ))
    commands.append(_pdf_text_command(
        f"{format_decimal(hours)} volunteer hours",
        396, details_y - 49, 16, "F2", dark_green, centered=True,
    ))
    commands.append(_pdf_text_command(
        f"Verification code: {verification_code}",
        396, details_y - 88, 11, "F2", ink, centered=True,
    ))
    commands.append(_pdf_text_command(
        "Verify this certificate at /verify",
        396, details_y - 108, 10, "F1", muted_ink, centered=True,
    ))
    commands.extend([
        f"{gold[0]:.3f} {gold[1]:.3f} {gold[2]:.3f} RG "
        "1 w 301 87 m 491 87 l S",
        _pdf_text_command(
            "Event Organizer Signature", 396, 69, 9, "F1", muted_ink, centered=True,
        ),
    ])
    stream = "\n".join(commands).encode("cp1252", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 792 612] /Resources "
        b"<< /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{index} 0 obj\n".encode("ascii"))
        pdf.extend(body)
        pdf.extend(b"\nendobj\n")
    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


def render_certificate_pdf(record: Mapping[str, Any]) -> bytes | None:
    """Render a persisted certificate record as PDF bytes for an on-demand download."""
    if not isinstance(record, Mapping):
        return None
    code = _normalize_verification_code(record.get("verification_code"))
    if code is None or record.get("verification_code") != code:
        return None

    hours = record.get("hours", 0)
    if (
        not isinstance(hours, (int, float))
        or isinstance(hours, bool)
        or not math.isfinite(hours)
        or hours < 0
    ):
        hours = 0
    volunteer_name = record.get("volunteer_name")
    if not isinstance(volunteer_name, str) or not volunteer_name.strip():
        volunteer_name = record.get("username", "Student Volunteer")
    event_name = record.get("event_name")
    if not isinstance(event_name, str) or not event_name.strip():
        event_name = record.get("event_id", "Campus event")

    return _render_certificate_pdf(
        str(volunteer_name),
        str(event_name),
        hours,
        code,
        event_date=record.get("event_date"),
        issue_date=record.get("generated_at"),
    )


def _certificate_for(event_id: str, username: str) -> dict[str, Any] | None:
    certificates = _load_object(CERTIFICATES_FILE, "keyed by verification code")
    return next(
        (
            record
            for record in certificates.values()
            if isinstance(record, dict)
            and record.get("event_id") == event_id
            and record.get("username") == username
        ),
        None,
    )


def generate_certificate(
    event_id: str, username: str
) -> tuple[bool, str]:
    """Generate or refresh a PDF only for an accepted applicant of a completed event."""
    try:
        if not isinstance(event_id, str) or not event_id.strip():
            return False, "Enter a valid event ID."
        if not isinstance(username, str) or not username.strip():
            return False, "Enter a valid volunteer username."
        event_id, username = event_id.strip(), username.strip()

        events = _load_object(EVENTS_FILE, "keyed by event ID")
        event = events.get(event_id)
        if not isinstance(event, dict):
            return False, "The event was not found."
        applications = _load_object(APPLICATIONS_FILE, "keyed by event ID")
        event_applications = applications.get(event_id, {})
        if not isinstance(event_applications, Mapping):
            return False, "The event's application records are invalid."
        application = event_applications.get(username)
        if not isinstance(application, Mapping):
            return False, "This volunteer did not apply to the event."
        if not isinstance(application.get("status"), str) or (
            application["status"].casefold() != "accepted"
        ):
            return False, "Only accepted applicants can receive a certificate."
        if event.get("status") != "completed":
            return False, "The event must be marked complete before issuing certificates."

        users = _load_object(USERS_FILE, "keyed by username")
        user = users.get(username)
        if not isinstance(user, Mapping):
            return False, "The volunteer's account could not be found."
        if user.get("role") != "Student Volunteer":
            return False, "Certificates are issued only to student volunteers."

        event_date = _event_date_value(event)
        certificates = _load_object(CERTIFICATES_FILE, "keyed by verification code")
        record = _certificate_for(event_id, username)
        if record is None:
            alphabet = string.ascii_uppercase + string.digits
            code = ""
            while not code or code in certificates:
                code = "".join(secrets.choice(alphabet) for _ in range(10))
            record = {
                "verification_code": code,
                "event_id": event_id,
                "username": username,
                "event_name": event.get("name", event_id),
                "event_date": event_date,
                "volunteer_name": user.get("name", username),
                "hours": application.get("hours", 0),
                "notes": application.get("notes", ""),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }

        code = record.get("verification_code")
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9]{10}", code):
            return False, "The stored certificate has an invalid verification code."
        record.update({
            "event_name": event.get("name", event_id),
            "volunteer_name": user.get("name", username),
            "hours": application.get("hours", 0),
            "notes": application.get("notes", ""),
        })
        if event_date is not None:
            record["event_date"] = event_date
        certificates[code] = record
        save(CERTIFICATES_FILE, certificates)
        return True, code
    except Exception as error:
        return False, f"Could not generate the certificate: {error}"


def complete_event(
    event_id: str, contribution_updates: Mapping[str, Any]
) -> tuple[bool, str]:
    """Save final accepted-volunteer contributions, complete the event, and issue certificates."""
    try:
        if not isinstance(event_id, str) or not event_id.strip():
            return False, "Enter a valid event ID."
        event_id = event_id.strip()
        if not isinstance(contribution_updates, Mapping):
            return False, "Contribution updates must be provided for each accepted volunteer."

        events = _load_object(EVENTS_FILE, "keyed by event ID")
        event = events.get(event_id)
        if not isinstance(event, dict):
            return False, "The event was not found."
        applications = _load_object(APPLICATIONS_FILE, "keyed by event ID")
        event_applications = applications.get(event_id, {})
        if not isinstance(event_applications, dict):
            return False, "The event's application records are invalid."
        accepted = {
            username: application
            for username, application in event_applications.items()
            if isinstance(application, Mapping)
            and isinstance(application.get("status"), str)
            and application["status"].casefold() == "accepted"
        }
        if event.get("status") == "completed":
            for username in accepted:
                success, result = generate_certificate(event_id, username)
                if not success:
                    return False, f"Event already complete, but certificate issuance failed: {result}"
            return True, "Event was already complete; certificates are available."
        if not accepted:
            if contribution_updates:
                return False, "There are no accepted volunteers with contribution details to save."
            event["status"] = "completed"
            event["completed_at"] = datetime.now(timezone.utc).isoformat()
            events[event_id] = event
            save(EVENTS_FILE, events)
            return True, "Event completed. There were no accepted volunteers to certify."
        if set(contribution_updates) != set(accepted):
            return False, "Enter final hours and notes for every accepted volunteer."

        cleaned_updates: dict[str, tuple[int | float, str]] = {}
        for username in accepted:
            update = contribution_updates.get(username)
            if not isinstance(update, Mapping):
                return False, f"Contribution details for '{username}' are invalid."
            hours = update.get("hours")
            if isinstance(hours, str):
                try:
                    hours = float(hours.strip())
                except ValueError:
                    hours = None
            if (
                not isinstance(hours, (int, float))
                or isinstance(hours, bool)
                or not math.isfinite(hours)
                or hours < 0
            ):
                return False, f"Hours for '{username}' must be a non-negative finite number."
            if float(hours).is_integer():
                hours = int(hours)
            notes = update.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 1000:
                return False, f"Notes for '{username}' must be 1,000 characters or fewer."
            cleaned_updates[username] = (hours, notes.strip())

        for username, (hours, notes) in cleaned_updates.items():
            accepted[username]["hours"] = hours
            accepted[username]["notes"] = notes
        event["status"] = "completed"
        event["completed_at"] = datetime.now(timezone.utc).isoformat()
        applications[event_id] = event_applications
        events[event_id] = event
        save(APPLICATIONS_FILE, applications)
        save(EVENTS_FILE, events)

        for username in accepted:
            success, result = generate_certificate(event_id, username)
            if not success:
                return False, f"Event completed, but a certificate could not be issued: {result}"
        return True, "Event completed and certificates issued."
    except Exception as error:
        return False, f"Could not complete the event: {error}"


def get_organizer_events(organizer_id: str) -> list[dict[str, Any]]:
    """Return this organizer's events with accepted volunteers for completion forms."""
    events = _load_object(EVENTS_FILE, "keyed by event ID")
    applications = _load_object(APPLICATIONS_FILE, "keyed by event ID")
    users = _load_object(USERS_FILE, "keyed by username")
    result = []
    for event_id, event in events.items():
        if not isinstance(event, dict) or event.get("organizer") != organizer_id:
            continue
        event_applications = applications.get(event_id, {})
        if not isinstance(event_applications, Mapping):
            raise ValueError(f"Applications for event '{event_id}' must be an object.")
        accepted = []
        for username, application in event_applications.items():
            if not isinstance(application, Mapping) or not isinstance(
                application.get("status"), str
            ):
                continue
            if application["status"].casefold() != "accepted":
                continue
            user = users.get(username, {})
            accepted.append({
                "username": username,
                "name": user.get("name", username) if isinstance(user, Mapping) else username,
                "hours": application.get("hours", 0),
                "notes": application.get("notes", ""),
            })
        result.append({
            "event_id": event_id,
            "event": event,
            "volunteers": sorted(accepted, key=lambda item: str(item["name"]).casefold()),
        })
    return sorted(result, key=lambda item: str(item["event"].get("name", "")).casefold())


def get_user_certificates(username: str) -> list[dict[str, Any]]:
    """Return only the certificate records belonging to the specified user."""
    certificates = _load_object(CERTIFICATES_FILE, "keyed by verification code")
    return sorted(
        (
            dict(record)
            for record in certificates.values()
            if isinstance(record, Mapping) and record.get("username") == username
        ),
        key=lambda record: str(record.get("generated_at", "")),
        reverse=True,
    )


def get_certificate_for_user(
    verification_code: str, username: str
) -> dict[str, Any] | None:
    """Find a certificate only when its verification code belongs to the user."""
    certificates = _load_object(CERTIFICATES_FILE, "keyed by verification code")
    record = certificates.get(verification_code)
    if not isinstance(record, Mapping) or record.get("username") != username:
        return None
    if (
        not isinstance(record.get("verification_code"), str)
        or not re.fullmatch(r"[A-Z0-9]{10}", record["verification_code"])
        or record["verification_code"] != verification_code
    ):
        return None
    return dict(record)


def verify_certificate(verification_code: str) -> dict[str, Any] | None:
    """Return safe public certificate details, or None for an invalid or unknown code."""
    code = _normalize_verification_code(verification_code)
    if code is None:
        return None
    certificates = _load_object(CERTIFICATES_FILE, "keyed by verification code")
    record = certificates.get(code)
    if (
        not isinstance(record, Mapping)
        or record.get("verification_code") != code
    ):
        return None

    events = _load_object(EVENTS_FILE, "keyed by event ID")
    event_id = record.get("event_id")
    event = events.get(event_id) if isinstance(event_id, str) else None
    event_date = _format_certificate_date(record.get("event_date"))
    if event_date is None and isinstance(event, Mapping):
        event_date = _format_certificate_date(_event_date_value(event))

    hours = record.get("hours")
    if (
        not isinstance(hours, (int, float))
        or isinstance(hours, bool)
        or not math.isfinite(hours)
        or hours < 0
    ):
        hours = None
    event_name = record.get("event_name")
    if not isinstance(event_name, str) and isinstance(event, Mapping):
        event_name = event.get("name")

    return {
        "verification_code": code,
        "volunteer_name": (
            record.get("volunteer_name")
            if isinstance(record.get("volunteer_name"), str)
            else ""
        ),
        "event_name": event_name if isinstance(event_name, str) else "",
        "event_date": event_date,
        "issue_date": _format_certificate_date(record.get("generated_at")),
        "hours": hours,
        "pdf_available": True,
    }
