"""Authenticated notification-center routes for organizers and volunteers."""

from flask import Blueprint, abort, flash, g, redirect, render_template, url_for

from auth_routes import _login_required, _require_csrf
from data_store import load
from notification_service import (
    get_user_notifications,
    mark_all_notifications_read,
    mark_notification_read,
)


notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


def _supported_account():
    user = getattr(g, "current_user", None)
    if user is None or user.get("role") not in {"Organizer", "Student Volunteer"}:
        abort(403)
    return user


@notifications_bp.get("")
@_login_required
def home():
    """Show the signed-in organizer's or volunteer's event notifications."""
    user = _supported_account()
    notifications = get_user_notifications(user["username"])
    events = load("events.json")
    if not isinstance(events, dict):
        raise ValueError("events.json must contain an object keyed by event ID.")
    notifications = [
        {**notice, "event": events.get(notice.get("event_id"))}
        for notice in notifications
    ]
    return render_template("notifications.html", notifications=notifications)


@notifications_bp.post("/<notification_id>/read")
@_login_required
@_require_csrf
def mark_read(notification_id):
    """Mark a notice read only for the account that owns it."""
    user = _supported_account()
    if not mark_notification_read(user["username"], notification_id):
        abort(404)
    flash("Notification marked as read.", "success")
    return redirect(url_for("notifications.home"), code=303)


@notifications_bp.post("/read-all")
@_login_required
@_require_csrf
def mark_all_read():
    """Mark all notices read for the current account."""
    user = _supported_account()
    mark_all_notifications_read(user["username"])
    flash("All notifications marked as read.", "success")
    return redirect(url_for("notifications.home"), code=303)
