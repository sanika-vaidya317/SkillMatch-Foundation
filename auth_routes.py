"""Flask routes for SkillMatch account registration and profile management."""

import hmac
import os
import secrets
from datetime import timedelta
from functools import wraps
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    flash,
    g,
    get_template_attribute,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth_service import login_user, register_user
from data_store import load, save


USERS_FILE = "users.json"
auth_bp = Blueprint("auth", __name__)


@auth_bp.record_once
def _configure_session(state):
    """Configure signed, browser-protected sessions."""
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        # Local-development fallback only; set SESSION_SECRET in Replit Secrets before public deployment.
        secret = secrets.token_urlsafe(32)

    state.app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_NAME="skillmatch_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    )


@auth_bp.before_app_request
def _load_current_user():
    """Load a safe public account view for role-aware templates."""
    if request.endpoint == "static":
        g.current_user = None
        return

    username = session.get("username")
    if not isinstance(username, str):
        g.current_user = None
        return

    users = load(USERS_FILE)
    user = users.get(username) if isinstance(users, dict) else None
    if not isinstance(user, dict):
        session.pop("username", None)
        g.current_user = None
        return

    g.current_user = {
        "username": username,
        "name": user.get("name", ""),
        "role": user.get("role", ""),
        "dept": user.get("dept"),
        "skills": user.get("skills", {}),
    }


@auth_bp.app_context_processor
def _inject_auth_context():
    """Expose the current account and a per-session form token to templates."""
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return {
        "current_user": getattr(g, "current_user", None),
        "csrf_token": token,
    }


def _is_htmx_request() -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _toast_response(message: str, kind: str = "error", status: int = 200):
    """Render a notification through the shared Jinja toast macro."""
    toast = get_template_attribute("macros.html", "toast")
    return make_response(toast(message, kind), status)


def _valid_csrf_token() -> bool:
    expected = session.get("csrf_token")
    submitted = request.form.get("csrf_token", "")
    return (
        isinstance(expected, str)
        and bool(expected)
        and isinstance(submitted, str)
        and hmac.compare_digest(expected, submitted)
    )


def _csrf_failure():
    if _is_htmx_request():
        return _toast_response("Your form expired. Refresh the page and try again.")
    abort(400, description="The form token is missing or invalid.")


def _require_csrf(view):
    @wraps(view)
    def checked_view(*args, **kwargs):
        if not _valid_csrf_token():
            return _csrf_failure()
        return view(*args, **kwargs)

    return checked_view


def _login_required(view):
    @wraps(view)
    def authenticated_view(*args, **kwargs):
        if getattr(g, "current_user", None) is None:
            flash("Please sign in to view your profile.", "error")
            login_url = url_for("auth.login", next=request.full_path.rstrip("?"))
            if _is_htmx_request():
                response = make_response("", 204)
                response.headers["HX-Redirect"] = login_url
                return response
            return redirect(login_url)
        return view(*args, **kwargs)

    return authenticated_view


def _redirect_after_post(location: str):
    if _is_htmx_request():
        response = make_response("", 204)
        response.headers["HX-Redirect"] = location
        return response
    return redirect(location)


def _safe_next_url(candidate: str | None) -> str | None:
    if (
        not candidate
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
    ):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    return candidate


def _parse_skills(raw_skills: str) -> tuple[dict[str, int] | None, str | None]:
    if not raw_skills.strip():
        return {}, None

    skills: dict[str, int] = {}
    seen: set[str] = set()
    for entry in raw_skills.split(","):
        skill, separator, raw_rating = entry.partition(":")
        skill = skill.strip()
        raw_rating = raw_rating.strip()
        if not separator or not skill:
            return None, "Use the format Skill:rating, for example Photography:4."
        try:
            rating = int(raw_rating)
        except ValueError:
            return None, "Skill ratings must be whole numbers of zero or higher."
        if rating < 0:
            return None, "Skill ratings must be whole numbers of zero or higher."
        normalized_skill = skill.casefold()
        if normalized_skill in seen:
            return None, "Each skill can only be listed once."
        seen.add(normalized_skill)
        skills[skill] = rating
    return skills, None


def _profile_completion(user: dict) -> int:
    name_complete = bool(str(user.get("name") or "").strip())
    if user.get("role") == "Organizer":
        return 100 if name_complete else 0
    dept_complete = bool(str(user.get("dept") or "").strip())
    skills = user.get("skills")
    skills_complete = isinstance(skills, dict) and bool(skills)
    completed = sum((name_complete, dept_complete, skills_complete))
    return round(completed / 3 * 100)


def _form_error(message: str, template_name: str, context: dict):
    if _is_htmx_request():
        return _toast_response(message)
    flash(message, "error")
    return render_template(template_name, **context)


def _registration_context(form_data: dict | None = None) -> dict:
    return {"form_data": form_data or {}}


def _profile_context(editing: bool, form_data: dict | None = None) -> dict:
    user = g.current_user
    skills = user.get("skills") if isinstance(user.get("skills"), dict) else {}
    default_form_data = {
        "name": user.get("name", ""),
        "dept": user.get("dept") or "",
        "skills": ", ".join(
            f"{skill}:{rating}"
            for skill, rating in sorted(skills.items(), key=lambda item: item[0].casefold())
        ),
    }
    if form_data:
        default_form_data.update(form_data)
    return {
        "user": user,
        "is_organizer": user.get("role") == "Organizer",
        "completion_percent": _profile_completion(user),
        "editing": editing,
        "form_data": default_form_data,
        "skill_items": sorted(skills.items(), key=lambda item: item[0].casefold()),
    }


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        if getattr(g, "current_user", None) is not None:
            return _redirect_after_post(url_for("auth.profile"))
        role = request.args.get("role", "Student Volunteer")
        if role not in ("Organizer", "Student Volunteer"):
            role = "Student Volunteer"
        return render_template(
            "register.html", **_registration_context({"role": role})
        )
    if not _valid_csrf_token():
        return _csrf_failure()

    form_data = {
        "username": request.form.get("username", "").strip(),
        "name": request.form.get("name", "").strip(),
        "role": request.form.get("role", "Student Volunteer").strip(),
        "dept": request.form.get("dept", "").strip(),
        "skills": request.form.get("skills", "").strip(),
    }
    if form_data["role"] == "Organizer":
        form_data["dept"] = ""
        form_data["skills"] = ""
        skills = {}
    else:
        if not form_data["dept"]:
            return _form_error(
                "Enter your department.",
                "register.html",
                _registration_context(form_data),
            )
        skills, skills_error = _parse_skills(form_data["skills"])
        if skills_error:
            return _form_error(
                skills_error,
                "register.html",
                _registration_context(form_data),
            )
        if not skills:
            return _form_error(
                "Add at least one skill to your volunteer profile.",
                "register.html",
                _registration_context(form_data),
            )

    success, message = register_user(
        username=form_data["username"],
        password=request.form.get("password", ""),
        role=form_data["role"],
        name=form_data["name"],
        dept=form_data["dept"] or None,
        skills=skills,
    )
    if not success:
        return _form_error(
            message,
            "register.html",
            _registration_context(form_data),
        )

    session.clear()
    session["username"] = form_data["username"]
    session.permanent = True
    flash(message, "success")
    return _redirect_after_post(url_for("auth.profile"))


@auth_bp.get("/register/profile-fields")
def registration_profile_fields():
    """Return role-specific registration fields after an HTMX role change."""
    role = request.args.get("role", "Student Volunteer")
    if role not in ("Organizer", "Student Volunteer"):
        role = "Student Volunteer"
    return render_template(
        "registration_fields.html",
        selected_role=role,
        form_data={
            "dept": request.args.get("dept", ""),
            "skills": request.args.get("skills", ""),
        },
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if getattr(g, "current_user", None) is not None:
            return _redirect_after_post(url_for("auth.profile"))
        return render_template(
            "login.html",
            username="",
            next_url=_safe_next_url(request.args.get("next")),
        )
    if not _valid_csrf_token():
        return _csrf_failure()

    username = request.form.get("username", "").strip()
    next_url = _safe_next_url(request.form.get("next"))
    user = login_user(username, request.form.get("password", ""))
    if user is None:
        if _is_htmx_request():
            return _toast_response("Username or password is incorrect.")
        flash("Username or password is incorrect.", "error")
        return render_template(
            "login.html",
            username=username,
            next_url=next_url,
        )

    session.clear()
    session["username"] = username
    session.permanent = True
    flash("You are signed in.", "success")
    return _redirect_after_post(next_url or url_for("auth.profile"))


@auth_bp.post("/logout")
@_require_csrf
def logout():
    session.clear()
    flash("You have signed out.", "success")
    return _redirect_after_post(url_for("auth.login"))


@auth_bp.get("/profile")
@_login_required
def profile():
    return render_template("profile.html", **_profile_context(editing=False))


@auth_bp.route("/profile/edit", methods=["GET", "POST"])
@_login_required
def edit_profile():
    if request.method == "GET":
        return render_template("profile.html", **_profile_context(editing=True))

    form_data = {
        "name": request.form.get("name", "").strip(),
        "dept": request.form.get("dept", "").strip(),
        "skills": request.form.get("skills", "").strip(),
    }
    if not form_data["name"] or len(form_data["name"]) > 100:
        return _form_error(
            "Enter a name of 1 to 100 characters.",
            "profile.html",
            _profile_context(editing=True, form_data=form_data),
        )
    username = session.get("username")
    users = load(USERS_FILE)
    account = users.get(username) if isinstance(users, dict) else None
    if not isinstance(account, dict):
        session.clear()
        return _redirect_after_post(url_for("auth.login"))

    if account.get("role") == "Organizer":
        form_data["dept"] = ""
        form_data["skills"] = ""
        skills = {}
    else:
        if not form_data["dept"]:
            return _form_error(
                "Enter your department.",
                "profile.html",
                _profile_context(editing=True, form_data=form_data),
            )
        if len(form_data["dept"]) > 100:
            return _form_error(
                "Department names can be up to 100 characters.",
                "profile.html",
                _profile_context(editing=True, form_data=form_data),
            )
        skills, skills_error = _parse_skills(form_data["skills"])
        if skills_error:
            return _form_error(
                skills_error,
                "profile.html",
                _profile_context(editing=True, form_data=form_data),
            )
        if not skills:
            return _form_error(
                "Add at least one skill to your volunteer profile.",
                "profile.html",
                _profile_context(editing=True, form_data=form_data),
            )

    account.update(
        name=form_data["name"],
        dept=form_data["dept"] or None,
        skills=skills,
    )
    save(USERS_FILE, users)
    flash("Your profile has been updated.", "success")
    return _redirect_after_post(url_for("auth.profile"))
