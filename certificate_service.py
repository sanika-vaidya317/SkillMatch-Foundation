"""Event completion and volunteer certificate operations."""

from collections.abc import Mapping
from datetime import date, datetime, timezone
from io import BytesIO
import math
from pathlib import Path
import re
import secrets
import string
from typing import Any

from data_store import load, save
from formatting import format_decimal
from notification_service import add_notifications
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


EVENTS_FILE = "events.json"
APPLICATIONS_FILE = "applications.json"
USERS_FILE = "users.json"
CERTIFICATES_FILE = "certificates.json"
CERTIFICATE_FONT_FILES = {
    "DM-Sans": Path(__file__).resolve().parent / "static" / "fonts" / "dm-sans" / "DM-Sans-Regular.ttf",
    "DM-Sans-Bold": Path(__file__).resolve().parent / "static" / "fonts" / "dm-sans" / "DM-Sans-Bold.ttf",
    "Fraunces-SemiBold": Path(__file__).resolve().parent / "static" / "fonts" / "fraunces" / "Fraunces-SemiBold.ttf",
    "Fraunces-Bold": Path(__file__).resolve().parent / "static" / "fonts" / "fraunces" / "Fraunces-Bold.ttf",
}
_CERTIFICATE_FONTS_REGISTERED = False


def _load_object(filename: str, description: str) -> dict[str, Any]:
    data = load(filename)
    if not isinstance(data, dict):
        raise ValueError(f"{filename} must contain an object {description}.")
    return data


def _register_certificate_fonts() -> None:
    """Register the local static instances of the site's DM Sans and Fraunces fonts."""
    global _CERTIFICATE_FONTS_REGISTERED
    if _CERTIFICATE_FONTS_REGISTERED:
        return
    registered_fonts = set(pdfmetrics.getRegisteredFontNames())
    for font_name, font_path in CERTIFICATE_FONT_FILES.items():
        if not font_path.is_file():
            raise FileNotFoundError(f"Required certificate font is missing: {font_path}")
        if font_name not in registered_fonts:
            pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    _CERTIFICATE_FONTS_REGISTERED = True


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


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Read width and height from a JPEG frame header."""
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("The campus dome asset is not a JPEG image.")
    frame_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0xD9, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            break
        if marker in frame_markers:
            if segment_length < 7:
                break
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            if width > 0 and height > 0:
                return width, height
            break
        offset += segment_length
    raise ValueError("Could not read the campus dome image dimensions.")


def _read_dome_image() -> tuple[bytes, int, int]:
    image_path = Path(__file__).resolve().parent / "static" / "images" / "mitwpu-dome.jpeg"
    image_data = image_path.read_bytes()
    width, height = _jpeg_dimensions(image_data)
    return image_data, width, height


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


def _estimated_text_width(
    text: str,
    font_size: int,
    bold: bool = False,
    font_name: str | None = None,
) -> float:
    """Measure text using the registered font that will be embedded in the PDF."""
    _register_certificate_fonts()
    selected_font = font_name or (
        "Fraunces-Bold" if bold else "Fraunces-SemiBold"
    )
    return pdfmetrics.stringWidth(" ".join(str(text).split()), selected_font, font_size)


def _wrap_pdf_text(
    text: Any,
    max_width: float,
    font_size: int,
    bold: bool,
    font_name: str | None = None,
) -> list[str]:
    normalized = " ".join(str(text).split())
    if not normalized:
        return [""]

    tokens: list[str] = []
    for word in normalized.split(" "):
        if _estimated_text_width(word, font_size, bold, font_name) <= max_width:
            tokens.append(word)
            continue
        chunk = ""
        for character in word:
            if chunk and _estimated_text_width(
                chunk + character, font_size, bold, font_name
            ) > max_width:
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
        if current and _estimated_text_width(
            candidate, font_size, bold, font_name
        ) > max_width:
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
    font_name: str | None = None,
) -> tuple[list[str], int]:
    for font_size in range(preferred_size, minimum_size - 1, -1):
        lines = _wrap_pdf_text(text, max_width, font_size, bold, font_name)
        if len(lines) <= max_lines:
            return lines, font_size

    font_size = minimum_size
    lines = _wrap_pdf_text(text, max_width, font_size, bold, font_name)[:max_lines]
    if lines:
        suffix = "..."
        last_line = lines[-1]
        while last_line and _estimated_text_width(
            last_line + suffix, font_size, bold, font_name
        ) > max_width:
            last_line = last_line[:-1].rstrip()
        lines[-1] = last_line + suffix
    return lines, font_size


def _draw_certificate_text(
    canvas: Canvas,
    text: Any,
    x: float,
    y: float,
    font_size: int,
    font_name: str,
    color: Color,
) -> None:
    normalized = " ".join(str(text).split())
    canvas.setFillColor(color)
    canvas.setFont(font_name, font_size)
    canvas.drawCentredString(x, y, normalized)


def _volunteer_display_name(value: Any) -> str:
    """Use the requested Title Case presentation without changing stored profile data."""
    return " ".join(str(value).split()).title()


def _event_display_name(value: Any) -> str:
    """Title-case event names only when every cased character was entered lowercase."""
    normalized = " ".join(str(value).split())
    return normalized.title() if normalized.islower() else normalized


def _render_certificate_pdf(
    volunteer_name: str,
    event_name: str,
    hours: int | float,
    event_date: str | None = None,
    issue_date: str | None = None,
    volunteer_role: str | None = None,
    organizer_name: str | None = None,
) -> bytes:
    """Render a single-page landscape certificate with a split photo-and-type layout."""
    _register_certificate_fonts()
    navy = HexColor("#17243b")
    accent = HexColor("#D4B483")
    paper = Color(0.976, 0.965, 0.929)
    muted_ink = HexColor("#48566b")
    dome_jpeg, dome_width, dome_height = _read_dome_image()
    image_scale = 558 / dome_height
    image_width = dome_width * image_scale
    image_height = dome_height * image_scale
    image_x = 27 - 315 * image_scale
    image_y = 27
    buffer = BytesIO()
    pdf = Canvas(
        buffer,
        pagesize=(792, 612),
        pageCompression=0,
        pdfVersion=(1, 4),
        invariant=1,
    )
    pdf.setTitle("SkillMatch Certificate of Participation")
    pdf.setFillColor(paper)
    pdf.rect(0, 0, 792, 612, stroke=0, fill=1)

    # Keep the existing double border, campus-photo crop, and split-page layout.
    pdf.setStrokeColor(accent)
    pdf.setLineWidth(0.9)
    pdf.rect(18, 18, 756, 576, stroke=1, fill=0)
    pdf.rect(25, 25, 742, 562, stroke=1, fill=0)
    photo_clip = pdf.beginPath()
    photo_clip.moveTo(27, 27)
    photo_clip.lineTo(27, 585)
    photo_clip.lineTo(350, 585)
    photo_clip.curveTo(369, 548, 380, 495, 381, 430)
    photo_clip.curveTo(383, 352, 380, 280, 371, 210)
    photo_clip.curveTo(361, 140, 347, 75, 337, 27)
    photo_clip.lineTo(27, 27)
    photo_clip.close()
    pdf.saveState()
    pdf.clipPath(photo_clip, stroke=0, fill=0)
    pdf.drawImage(
        ImageReader(BytesIO(dome_jpeg)),
        image_x,
        image_y,
        width=image_width,
        height=image_height,
        preserveAspectRatio=False,
    )
    pdf.restoreState()

    photo_edge = pdf.beginPath()
    photo_edge.moveTo(27, 585)
    photo_edge.lineTo(350, 585)
    photo_edge.curveTo(369, 548, 380, 495, 381, 430)
    photo_edge.curveTo(383, 352, 380, 280, 371, 210)
    photo_edge.curveTo(361, 140, 347, 75, 337, 27)
    photo_edge.lineTo(27, 27)
    pdf.setStrokeColor(accent)
    pdf.setLineWidth(1.5)
    pdf.drawPath(photo_edge, stroke=1, fill=0)

    photo_detail = pdf.beginPath()
    photo_detail.moveTo(346, 582)
    photo_detail.curveTo(365, 545, 375, 490, 377, 427)
    photo_detail.curveTo(379, 352, 376, 280, 367, 210)
    photo_detail.curveTo(358, 140, 344, 75, 333, 30)
    pdf.setLineWidth(0.55)
    pdf.drawPath(photo_detail, stroke=1, fill=0)

    diamond = pdf.beginPath()
    diamond.moveTo(576, 532)
    diamond.lineTo(581, 537)
    diamond.lineTo(586, 532)
    diamond.lineTo(581, 527)
    diamond.close()
    pdf.setFillColor(accent)
    pdf.drawPath(diamond, stroke=0, fill=1)
    pdf.setLineWidth(0.9)
    pdf.line(448, 534, 510, 534)
    pdf.line(652, 534, 714, 534)

    # Preserve the certificate's existing gold botanical ornament.
    branch = pdf.beginPath()
    branch.moveTo(366, 29)
    branch.curveTo(380, 55, 390, 100, 414, 151)
    pdf.setLineWidth(0.75)
    pdf.drawPath(branch, stroke=1, fill=0)
    leaf_curves = (
        ((371, 47), (362, 45), (357, 49), (354, 55), (362, 59), (369, 56), (371, 47)),
        ((378, 64), (388, 57), (396, 58), (400, 63), (394, 70), (386, 70), (378, 64)),
        ((382, 81), (372, 77), (365, 81), (363, 87), (371, 92), (378, 89), (382, 81)),
        ((388, 98), (398, 90), (407, 91), (411, 96), (404, 103), (396, 104), (388, 98)),
        ((394, 116), (384, 112), (377, 116), (375, 122), (383, 127), (390, 124), (394, 116)),
        ((401, 134), (411, 126), (419, 128), (422, 133), (415, 140), (407, 140), (401, 134)),
    )
    for start, c1, c2, end, c3, c4, finish in leaf_curves:
        leaf = pdf.beginPath()
        leaf.moveTo(*start)
        leaf.curveTo(*c1, *c2, *end)
        leaf.curveTo(*c3, *c4, *finish)
        pdf.drawPath(leaf, stroke=1, fill=0)

    _draw_certificate_text(
        pdf, "MIT WORLD PEACE UNIVERSITY", 581, 566, 10, "DM-Sans-Bold", navy,
    )
    _draw_certificate_text(pdf, "PUNE", 581, 551, 7, "DM-Sans-Bold", navy)
    _draw_certificate_text(
        pdf, "CERTIFICATE OF", 581, 501, 20, "Fraunces-SemiBold", navy,
    )
    _draw_certificate_text(
        pdf, "PARTICIPATION", 581, 472, 29, "Fraunces-Bold", navy,
    )
    _draw_certificate_text(
        pdf, "PRESENTED TO", 581, 438, 8, "DM-Sans-Bold", navy,
    )

    volunteer_name = _volunteer_display_name(volunteer_name)
    event_name = _event_display_name(event_name)
    text_center = 581
    text_width = 346
    name_lines, name_size = _fit_pdf_text(
        volunteer_name,
        text_width,
        preferred_size=25,
        minimum_size=17,
        max_lines=2,
        bold=True,
        font_name="Fraunces-Bold",
    )
    name_y = 411
    for line in name_lines:
        _draw_certificate_text(
            pdf, line, text_center, name_y, name_size, "Fraunces-Bold", navy,
        )
        name_y -= name_size + 3

    event_label_y = 411 - len(name_lines) * (name_size + 3) - 6
    _draw_certificate_text(
        pdf, "FOR PARTICIPATION IN", text_center, event_label_y, 8, "DM-Sans-Bold", navy,
    )
    event_lines, event_size = _fit_pdf_text(
        event_name,
        text_width,
        preferred_size=18,
        minimum_size=13,
        max_lines=2,
        bold=True,
        font_name="Fraunces-Bold",
    )
    event_y = event_label_y - 24
    for line in event_lines:
        _draw_certificate_text(
            pdf, line, text_center, event_y, event_size, "Fraunces-Bold", navy,
        )
        event_y -= event_size + 3

    details_y = event_y - 10
    normalized_role = (
        " ".join(volunteer_role.split())
        if isinstance(volunteer_role, str) and volunteer_role.strip()
        else None
    )
    if normalized_role:
        role_lines, role_size = _fit_pdf_text(
            f"as {normalized_role}",
            text_width,
            preferred_size=10,
            minimum_size=8,
            max_lines=1,
            bold=True,
            font_name="DM-Sans-Bold",
        )
        role_y = event_y - 3
        for line in role_lines:
            _draw_certificate_text(
                pdf, line, text_center, role_y, role_size, "DM-Sans-Bold", navy,
            )
            role_y -= role_size + 2
        details_y = role_y - 10

    event_date_text = _format_certificate_date(event_date) or "Not recorded"
    issue_date_text = _format_certificate_date(issue_date) or "Not recorded"
    _draw_certificate_text(
        pdf,
        f"Event date: {event_date_text}",
        text_center,
        details_y,
        9,
        "DM-Sans",
        muted_ink,
    )
    _draw_certificate_text(
        pdf,
        f"Issue date: {issue_date_text}",
        text_center,
        details_y - 16,
        9,
        "DM-Sans",
        muted_ink,
    )
    _draw_certificate_text(
        pdf,
        f"{format_decimal(hours)} volunteer hours",
        text_center,
        details_y - 45,
        14,
        "DM-Sans-Bold",
        navy,
    )
    _draw_certificate_text(
        pdf,
        "Verification code available in your SkillMatch profile",
        text_center,
        details_y - 76,
        8,
        "DM-Sans-Bold",
        navy,
    )
    _draw_certificate_text(
        pdf, "Verify at /verify", text_center, details_y - 94, 8, "DM-Sans", muted_ink,
    )

    pdf.setStrokeColor(accent)
    pdf.setLineWidth(0.9)
    pdf.line(507, 82, 655, 82)
    signature_name = (
        " ".join(organizer_name.split())
        if isinstance(organizer_name, str) and organizer_name.strip()
        else "Event Organizer"
    )
    organizer_lines, organizer_size = _fit_pdf_text(
        signature_name,
        140,
        preferred_size=9,
        minimum_size=6,
        max_lines=1,
        bold=True,
        font_name="DM-Sans-Bold",
    )
    _draw_certificate_text(
        pdf,
        organizer_lines[0],
        text_center,
        65,
        organizer_size,
        "DM-Sans-Bold",
        navy,
    )
    _draw_certificate_text(
        pdf,
        "Event Organizer Signature",
        text_center,
        51,
        7,
        "DM-Sans",
        muted_ink,
    )
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def _certificate_display_details(
    record: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Resolve role and organizer name for both new and legacy certificate records."""
    role = record.get("volunteer_role")
    if not isinstance(role, str) or not role.strip():
        role = None
    organizer_name = record.get("organizer_name")
    if not isinstance(organizer_name, str) or not organizer_name.strip():
        organizer_name = None

    event_id = record.get("event_id")
    username = record.get("username")
    if (
        "volunteer_role" not in record
        and isinstance(event_id, str)
        and isinstance(username, str)
    ):
        applications = load(APPLICATIONS_FILE)
        event_applications = (
            applications.get(event_id, {})
            if isinstance(applications, Mapping)
            else {}
        )
        application = (
            event_applications.get(username, {})
            if isinstance(event_applications, Mapping)
            else {}
        )
        candidate = application.get("role") if isinstance(application, Mapping) else None
        role = candidate.strip() if isinstance(candidate, str) and candidate.strip() else None

    if (
        organizer_name is None
        and isinstance(event_id, str)
    ):
        events = load(EVENTS_FILE)
        event = events.get(event_id, {}) if isinstance(events, Mapping) else {}
        organizer_id = event.get("organizer") if isinstance(event, Mapping) else None
        if isinstance(organizer_id, str) and organizer_id.strip():
            users = load(USERS_FILE)
            organizer = users.get(organizer_id) if isinstance(users, Mapping) else None
            candidate = (
                organizer.get("name")
                if isinstance(organizer, Mapping)
                else None
            )
            organizer_name = (
                candidate.strip()
                if isinstance(candidate, str) and candidate.strip()
                else organizer_id
            )
    return role, organizer_name


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
    volunteer_role, organizer_name = _certificate_display_details(record)

    return _render_certificate_pdf(
        str(volunteer_name),
        str(event_name),
        hours,
        event_date=record.get("event_date"),
        issue_date=record.get("generated_at"),
        volunteer_role=volunteer_role,
        organizer_name=organizer_name,
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

        organizer_id = event.get("organizer")
        organizer = users.get(organizer_id) if isinstance(organizer_id, str) else None
        organizer_name = (
            organizer.get("name")
            if isinstance(organizer, Mapping)
            and isinstance(organizer.get("name"), str)
            and organizer.get("name").strip()
            else organizer_id
            if isinstance(organizer_id, str) and organizer_id.strip()
            else "Event Organizer"
        )
        assigned_role = application.get("role")
        volunteer_role = (
            assigned_role.strip()
            if isinstance(assigned_role, str) and assigned_role.strip()
            else None
        )
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
                "organizer_name": organizer_name,
                "volunteer_role": volunteer_role,
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
            "organizer_name": organizer_name,
            "volunteer_role": volunteer_role,
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
            organizer_id = event.get("organizer")
            if isinstance(organizer_id, str) and organizer_id.strip():
                add_notifications(
                    [organizer_id],
                    kind="event_ended",
                    title="Event ended",
                    message=f"{event.get('name', 'Your event')} has been marked complete.",
                    event_id=event_id,
                )
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
        organizer_id = event.get("organizer")
        if isinstance(organizer_id, str) and organizer_id.strip():
            add_notifications(
                [organizer_id],
                kind="event_ended",
                title="Event ended",
                message=f"{event.get('name', 'Your event')} has been marked complete.",
                event_id=event_id,
            )
        add_notifications(
            list(accepted),
            kind="event_ended",
            title="Event ended",
            message=f"{event.get('name', 'The event')} has ended. Thank you for volunteering.",
            event_id=event_id,
        )

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
