"""Category-driven certificate themes shared by PDFs and public verification."""

DEFAULT_CATEGORY = "Community Service"


def _theme(primary, accent, heading, citation, path, border_pattern="double"):
    return {
        "primary": primary,
        "accent": accent,
        "heading": heading,
        "citation": citation,
        "border_pattern": border_pattern,
        "background_graphic": None,
        "emblem_svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
            'fill="none" stroke="currentColor" stroke-width="1.4" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<path d="{path}"/></svg>'
        ),
    }


# Adding an entry also makes its category available in the organizer form.
CERTIFICATE_THEMES = {
    "Environment": _theme(
        "#244C3B", "#A6B77D", "ENVIRONMENTAL STEWARDSHIP",
        "for helping keep our campus clean and green",
        "M4 20 L12 12 M4 20 C2 10 7 3 20 4 C21 17 14 22 4 20 Z "
        "M12 12 L12 6 M12 12 L18 12",
    ),
    "Education": _theme(
        "#253C67", "#D4B483", "TEACHING EXCELLENCE",
        "for sharing knowledge and helping others learn",
        "M12 6 C8 3 4 4 2 5 L2 19 C6 17 9 18 12 20 "
        "C15 18 18 17 22 19 L22 5 C18 3 15 4 12 6 Z M12 6 L12 20",
        "dashed",
    ),
    "Health": _theme(
        "#28575C", "#CB9E93", "COMMUNITY WELLNESS",
        "for supporting a healthier, more caring community",
        "M9 3 L15 3 L15 9 L21 9 L21 15 L15 15 L15 21 "
        "L9 21 L9 15 L3 15 L3 9 L9 9 Z",
    ),
    DEFAULT_CATEGORY: _theme(
        "#17243b", "#D4B483", "PARTICIPATION",
        "for strengthening our campus and community",
        "M8 8 C3 8 3 2 8 2 C13 2 13 8 8 8 Z "
        "M2 21 L2 17 C2 11 14 11 14 17 L14 21 "
        "M17 9 C13 9 13 4 17 4 C21 4 21 9 17 9 Z "
        "M17 13 C21 13 23 15 23 18 L23 21",
    ),
    "Tech": _theme(
        "#30345D", "#9AAED4", "TECHNOLOGY & INNOVATION",
        "for using technology to create a positive impact",
        "M6 6 L18 6 L18 18 L6 18 Z M9 9 L15 9 L15 15 L9 15 Z "
        "M9 2 L9 6 M15 2 L15 6 M9 18 L9 22 M15 18 L15 22 "
        "M2 9 L6 9 M2 15 L6 15 M18 9 L22 9 M18 15 L22 15",
        "dashed",
    ),
    "Arts": _theme(
        "#633E5B", "#D8AD91", "CREATIVE CONTRIBUTION",
        "for bringing creativity and expression to our campus",
        "M12 2 C1 2 0 17 8 21 C12 23 11 17 15 17 "
        "C23 19 25 2 12 2 Z M7 7 L8 7 M13 5 L14 5 "
        "M18 8 L19 8 M5 12 L6 12",
        "dotted",
    ),
    "Sports/Fitness": _theme(
        "#2C4D61", "#B3BD8D", "SPORTS & TEAMWORK",
        "for inspiring teamwork, movement and active wellbeing",
        "M14 5 C11 5 11 1 14 1 C17 1 17 5 14 5 Z "
        "M5 10 L10 7 L15 9 L20 7 M10 7 L9 14 L15 17 L17 23 "
        "M9 14 L5 19 L1 19",
    ),
    "Animal Welfare": _theme(
        "#66513F", "#C6B59A", "COMPASSION IN ACTION",
        "for showing care and compassion for animals",
        "M7 9 C3 9 3 3 6 3 C9 3 10 9 7 9 Z "
        "M17 9 C14 9 15 3 18 3 C21 3 21 9 17 9 Z "
        "M3 15 C0 15 0 10 3 10 C6 10 6 15 3 15 Z "
        "M21 15 C18 15 18 10 21 10 C24 10 24 15 21 15 Z "
        "M5 20 C5 14 9 11 12 11 C15 11 19 14 19 20 "
        "C19 24 15 21 12 21 C9 21 5 24 5 20 Z",
        "dotted",
    ),
    "Fundraising": _theme(
        "#594B2D", "#D1B36F", "GENEROUS SERVICE",
        "for helping turn community generosity into meaningful support",
        "M12 2 C-1 2 -1 22 12 22 C25 22 25 2 12 2 Z "
        "M16 7 C8 3 5 12 12 12 C19 12 16 21 8 17 M12 4 L12 20",
    ),
}


def get_certificate_theme(category=None):
    """Unknown and pre-theme certificates always use the original default palette."""
    key = next(
        (
            name for name in CERTIFICATE_THEMES
            if isinstance(category, str) and name.casefold() == category.strip().casefold()
        ),
        DEFAULT_CATEGORY,
    )
    return {"category": key, **CERTIFICATE_THEMES[key]}
