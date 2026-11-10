"""Additive event category defaults and safe optional image URLs."""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from certificate_themes import CERTIFICATE_THEMES, DEFAULT_CATEGORY

EVENT_CATEGORIES = tuple(CERTIFICATE_THEMES)
DEFAULT_EVENT_IMAGES = {
    "Environment": "environment.jpg",
    "Education": "education.jpg",
    "Health": "health.jpg",
    "Community Service": "community-service.jpg",
    "Tech": "tech.jpg",
    "Arts": "arts.jpg",
    "Sports/Fitness": "sports-fitness.jpg",
    "Animal Welfare": "animal-welfare.jpg",
    "Fundraising": "fundraising.jpg",
}
_CATEGORY_KEYWORDS = (
    ("Animal Welfare", ("animal", "shelter", "pet care", "dog rescue")),
    ("Fundraising", ("fundrais", "charity", "donation")),
    ("Environment", ("environment", "garden", "cleanup", "clean-up", "planting", "tree")),
    ("Education", ("education", "teach", "tutor", "literacy", "school")),
    ("Health", ("health", "medical", "blood", "wellness")),
    ("Tech", ("tech", "coding", "hackathon", "robot", "programming")),
    ("Arts", ("arts", "music", "painting", "dance", "theatre")),
    ("Sports/Fitness", ("sport", "fitness", "running", "marathon", "yoga")),
)


def event_category(event):
    """Respect explicit categories; infer legacy event types without rewriting records."""
    event = event if isinstance(event, Mapping) else {}
    explicit = event.get("category")
    if isinstance(explicit, str) and explicit.strip():
        return next(
            (name for name in EVENT_CATEGORIES if name.casefold() == explicit.strip().casefold()),
            DEFAULT_CATEGORY,
        )
    text = " ".join(
        str(event.get(field) or "") for field in ("type", "event_type", "name", "description")
    ).casefold()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(re.search(r"\b" + re.escape(keyword), text) for keyword in keywords):
            return category
    return DEFAULT_CATEGORY


def validate_image_url(value):
    """Images load in the browser only; never fetch organizer-supplied URLs server-side."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Image URL must be an HTTPS URL of 2048 characters or fewer.")
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https" and parsed.hostname
            and not parsed.username and not parsed.password
            and not any(character.isspace() for character in value)
        )
        parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use a full HTTPS image URL, or leave the image field blank.")
    return value


def event_visual(event):
    event = event if isinstance(event, Mapping) else {}
    category = event_category(event)
    try:
        image_url = validate_image_url(event.get("image_url"))
    except ValueError:
        image_url = ""
    return {
        "category": category,
        "slug": re.sub(r"[^a-z0-9]+", "-", category.casefold()).strip("-"),
        "image_url": image_url,
        "alt": f"{event.get('name') or event.get('event_name') or 'Campus event'} — {category}",
        "default_image_url": (
            f"/static/images/events/{DEFAULT_EVENT_IMAGES[category]}"
            if category in DEFAULT_EVENT_IMAGES else ""
        ),
        "default_alt": f"Stock photo representing {category} volunteering",
    }
