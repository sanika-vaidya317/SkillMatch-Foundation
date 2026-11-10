"""Certificate pages, organizer event completion, and public verification."""

from functools import wraps
from io import BytesIO

from flask import Blueprint, abort, g, render_template, request, send_file, url_for

from auth_routes import _login_required, _require_csrf
from certificate_service import (
    complete_event as complete_event_service,
    get_certificate_for_user,
    get_organizer_events,
    get_user_certificates,
    render_certificate_pdf,
    render_verified_certificate_pdf,
    verify_certificate,
)
from data_store import load


certificates_bp = Blueprint("certificates", __name__)


def _role_required(role: str):
    def decorate(view):
        @wraps(view)
        def checked_view(*args, **kwargs):
            user = getattr(g, "current_user", None)
            if user is None or user.get("role") != role:
                abort(403)
            return view(*args, **kwargs)

        return checked_view

    return decorate


@certificates_bp.get("/certificates")
@_login_required
def home():
    """Show event completion controls to organizers and personal certificates to volunteers."""
    current_user = g.current_user
    if current_user.get("role") == "Organizer":
        try:
            events = get_organizer_events(current_user["username"])
        except ValueError as error:
            return render_template(
                "certificates.html", role="Organizer", events=[], certificates=[],
                error=str(error), notice=None,
            )
        return render_template(
            "certificates.html", role="Organizer", events=events, certificates=[],
            error=None, notice=None,
        )
    if current_user.get("role") == "Student Volunteer":
        try:
            certificates = get_user_certificates(current_user["username"])
            event_records = load("events.json")
            if not isinstance(event_records, dict):
                raise ValueError("events.json must contain an object keyed by event ID.")
            certificates = [
                {
                    **record,
                    "event": event_records.get(record.get("event_id"), {
                        "name": record.get("event_name"),
                        "category": record.get("category", "Community Service"),
                    }),
                }
                for record in certificates
            ]
        except ValueError as error:
            return render_template(
                "certificates.html", role="Student Volunteer", events=[], certificates=[],
                error=str(error), notice=None,
            )
        return render_template(
            "certificates.html", role="Student Volunteer", events=[],
            certificates=certificates, error=None, notice=None,
        )
    abort(403)


@certificates_bp.post("/certificates/events/<event_id>/complete")
@_login_required
@_role_required("Organizer")
@_require_csrf
def complete_event(event_id):
    """Accept final contribution details only for an event owned by this organizer."""
    try:
        events = get_organizer_events(g.current_user["username"])
    except ValueError as error:
        abort(400, description=str(error))
    if not any(item["event_id"] == event_id for item in events):
        abort(404)

    contribution_updates = {}
    for key, hours_value in request.form.items():
        if key.startswith("hours_"):
            username = key.removeprefix("hours_")
            contribution_updates[username] = {
                "hours": hours_value,
                "notes": request.form.get(f"notes_{username}", ""),
            }
    success, message = complete_event_service(event_id, contribution_updates)
    try:
        events = get_organizer_events(g.current_user["username"])
    except ValueError:
        events = []
    return render_template(
        "certificates.html",
        role="Organizer",
        events=events,
        certificates=[],
        error=None if success else message,
        notice=message if success else None,
    )


@certificates_bp.get("/certificates/<verification_code>/download")
@_login_required
@_role_required("Student Volunteer")
def download(verification_code):
    """Download only a certificate owned by the signed-in volunteer."""
    record = get_certificate_for_user(verification_code, g.current_user["username"])
    if record is None:
        abort(404)
    pdf_bytes = render_certificate_pdf(record)
    if pdf_bytes is None:
        abort(404)
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"SkillMatch-{record['verification_code']}.pdf",
    )


@certificates_bp.route("/verify", methods=["GET", "POST"])
def verify():
    """Public certificate verification lookup; reveals details only for a valid code."""
    code = (
        request.form.get("verification_code", "").strip().upper()
        if request.method == "POST"
        else request.args.get("code", "").strip().upper()
    )
    result = verify_certificate(code) if code else None
    searched = request.method == "POST" or bool(code)
    return render_template(
        "verify.html",
        verification_code=code,
        certificate=result,
        searched=searched,
    )


@certificates_bp.get("/verify/<verification_code>/certificate.pdf")
def verified_certificate_preview(verification_code):
    """Public inline copy matches the owner's PDF; verification codes stay off the PDF."""
    pdf = render_verified_certificate_pdf(verification_code)
    if pdf is None:
        abort(404)
    response = send_file(
        BytesIO(pdf), mimetype="application/pdf", as_attachment=False,
        download_name="SkillMatch-certificate.pdf",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response
