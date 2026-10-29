"""Homepage content, step data, and shared-theme regressions."""

import unittest
from pathlib import Path

from app import app, home_steps


class HomePageTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_home_steps_returns_the_four_requested_steps(self):
        self.assertEqual(home_steps(), [
            {"number": "01", "title": "Find an event that fits your skills"},
            {"number": "02", "title": "Apply in one click"},
            {"number": "03", "title": "Get accepted by the organizer"},
            {"number": "04", "title": "Earn a certificate anyone can verify"},
        ])

    def test_home_page_renders_data_driven_steps_and_campus_hero(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        for title in (
            "Find an event that fits your skills",
            "Apply in one click",
            "Get accepted by the organizer",
            "Earn a certificate anyone can verify",
        ):
            self.assertIn(title.encode(), response.data)
        self.assertIn(b'class="home-hero-image"', response.data)
        self.assertIn(b'class="hero-highlight">skill</span>', response.data)
        self.assertNotIn(b"Shared campus volunteering foundation", response.data)
        base_template = Path("templates/base.html").read_text(encoding="utf-8")
        self.assertIn("{% block extra_css %}{% endblock %}", base_template)

    def test_theme_tokens_buttons_badges_and_mobile_steps_use_requested_styles(self):
        with self.client.get("/static/style.css") as response:
            self.assertEqual(response.status_code, 200)
            css = response.data.decode()
        for token in (
            "--navy: #17243b",
            "--accent: #D4B483",
            "--sand: #E2D0B9",
        ):
            self.assertIn(token, css)
        self.assertIn("color: var(--navy);\n  background: var(--accent);", css)
        self.assertIn("background: var(--accent);", css)
        self.assertIn(".home-steps-grid {\n    grid-template-columns: 1fr;", css)

    def test_shared_workspace_and_interior_pages_use_homepage_theme(self):
        css = Path("static/style.css").read_text(encoding="utf-8")
        for rule in (
            ".section-heading h1,\n.page-heading h1",
            ".certificate-verify-form",
            ".profile-panel",
            ".notification-card--unread",
        ):
            self.assertIn(rule, css)

        base_template = Path("templates/base.html").read_text(encoding="utf-8")
        self.assertNotIn('class="sidebar"', base_template)
        self.assertIn('class="top-nav top-nav--desktop"', base_template)
        self.assertIn('url_for(\'dashboard.home\')', base_template)
        self.assertIn('url_for(\'notifications.home\')', base_template)


if __name__ == "__main__":
    unittest.main()
