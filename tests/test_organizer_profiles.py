"""Role-aware registration and profile checks using disposable storage."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auth_service
import certificate_service
import data_store
from app import app


class OrganizerProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skillmatch-profile-tests-")
        self.storage = patch.object(data_store, "DATA_DIR", Path(self.temp.name))
        self.storage.start()
        self.old_config = {
            key: app.config[key] for key in ("TESTING", "SESSION_COOKIE_SECURE")
        }
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = app.test_client()
        self.client.get("/register")
        with self.client.session_transaction() as session:
            self.csrf = session["csrf_token"]

    def tearDown(self):
        app.config.update(self.old_config)
        self.storage.stop()
        self.temp.cleanup()

    def test_organizer_signup_ignores_department_and_skills_on_server(self):
        response = self.client.post("/register", data={
            "username": "organizer",
            "name": "Organizer One",
            "password": "safe-pass1",
            "role": "Organizer",
            "dept": "Ignore this",
            "skills": "not a valid skill list",
            "csrf_token": self.csrf,
        })
        self.assertEqual(response.status_code, 302)
        account = data_store.load("users.json")["organizer"]
        self.assertIsNone(account["dept"])
        self.assertEqual(account["skills"], {})
        self.assertIn("password_hash", account)

        profile = self.client.get("/profile")
        self.assertIn(b"Profile 100% complete", profile.data)
        editing = self.client.get("/profile/edit")
        self.assertNotIn(b'name="dept"', editing.data)
        self.assertNotIn(b'name="skills"', editing.data)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.assertEqual(self.client.get("/events/discover").status_code, 200)

    def test_role_change_returns_only_applicable_fields_without_javascript(self):
        organizer = self.client.get("/register/profile-fields?role=Organizer")
        self.assertEqual(organizer.status_code, 200)
        self.assertNotIn(b'name="dept"', organizer.data)
        self.assertNotIn(b'name="skills"', organizer.data)
        student = self.client.get("/register/profile-fields?role=Student+Volunteer")
        self.assertIn(b'name="dept"', student.data)
        self.assertIn(b'name="skills"', student.data)
        self.assertIn(b"required", student.data)
        self.assertIn(
            b'hx-get="/register/profile-fields"', self.client.get("/register").data
        )
        organizer_page = self.client.get("/register?role=Organizer")
        self.assertNotIn(b'name="dept"', organizer_page.data)
        self.assertNotIn(b'name="skills"', organizer_page.data)

    def test_registration_password_requires_digit_and_special_character(self):
        response = self.client.get("/register")
        self.assertIn(b'pattern="(?=.*[0-9])(?=.*[^A-Za-z0-9\\s]).{8,1024}"', response.data)
        rejected = self.client.post("/register", data={
            "username": "bad-password",
            "name": "Student One",
            "password": "password1",
            "role": "Organizer",
            "csrf_token": self.csrf,
        })
        self.assertEqual(rejected.status_code, 200)
        self.assertIn(b"one digit and one special character", rejected.data)
        self.assertNotIn("bad-password", data_store.load("users.json"))

        for password in ("password1", "safe-password", "short1!"):
            success, message = auth_service.register_user(
                "student",
                password,
                "Student Volunteer",
                "Student One",
                dept="CSE",
                skills={"Python": 2},
            )
            self.assertFalse(success)
            self.assertIn("digit", message)
            self.assertIn("special character", message)
        success, message = auth_service.register_user(
            "student",
            "safe-pass1",
            "Student Volunteer",
            "Student One",
            dept="CSE",
            skills={"Python": 2},
        )
        self.assertTrue(success, message)

    def test_organizer_service_normalizes_unused_profile_fields(self):
        success, message = auth_service.register_user(
            "organizer", "safe-pass1", "Organizer", "Organizer One",
            dept="Anything", skills={"Invalid": True},
        )
        self.assertTrue(success, message)
        account = data_store.load("users.json")["organizer"]
        self.assertIsNone(account["dept"])
        self.assertEqual(account["skills"], {})

    def test_student_signup_requires_department_and_skills(self):
        for dept, skills, expected in (
            (None, {"Python": 2}, "department"),
            ("CSE", {}, "skill"),
            ("CSE", None, "skill"),
        ):
            success, message = auth_service.register_user(
                "student", "safe-pass1", "Student Volunteer", "Student One",
                dept=dept, skills=skills,
            )
            self.assertFalse(success)
            self.assertIn(expected, message)
        self.assertEqual(data_store.load("users.json"), {})
        success, message = auth_service.register_user(
            "student", "safe-pass1", "Student Volunteer", "Student One",
            dept="CSE", skills={"Python": 2},
        )
        self.assertTrue(success, message)

    def test_volunteer_badges_use_completed_hours_and_appear_on_profile(self):
        data_store.save("users.json", {
            "student": {
                "name": "Student One",
                "role": "Student Volunteer",
                "dept": "CSE",
                "skills": {"Python": 2},
            },
        })
        for hours, expected_badge in (
            (0, None),
            (0.5, "Copper"),
            (9.99, "Copper"),
            (10, "Silver"),
            (24.5, "Silver"),
            (25, "Gold"),
            (99.5, "Gold"),
            (100, "Diamond"),
        ):
            with self.subTest(hours=hours):
                data_store.save("events.json", {
                    "completed": {"status": "completed"},
                    "in_progress": {"status": "open"},
                })
                data_store.save("applications.json", {
                    "completed": {
                        "student": {"status": "accepted", "hours": hours},
                    },
                    "in_progress": {
                        "student": {"status": "accepted", "hours": 200},
                    },
                })
                badge = certificate_service.get_volunteer_badge("student")
                self.assertEqual(
                    badge["name"] if badge else None,
                    expected_badge,
                )

        data_store.save("events.json", {"completed": {"status": "completed"}})
        data_store.save("applications.json", {
            "completed": {"student": {"status": "accepted", "hours": 10}},
        })
        with self.client.session_transaction() as session:
            session["username"] = "student"
        profile = self.client.get("/profile")
        self.assertEqual(profile.status_code, 200)
        self.assertIn(b"Silver badge", profile.data)
        self.assertIn(b"10 completed volunteer hours", profile.data)

    def test_organizer_profile_save_keeps_other_record_fields(self):
        data_store.save("users.json", {"organizer": {
            "name": "Organizer", "role": "Organizer", "dept": "Old",
            "skills": {"Old": 2}, "saved_events": ["e1"], "custom": "keep",
        }})
        with self.client.session_transaction() as session:
            session["username"] = "organizer"
        response = self.client.post("/profile/edit", data={
            "name": "Renamed Organizer", "dept": "Ignore",
            "skills": "not a skill", "csrf_token": self.csrf,
        })
        self.assertEqual(response.status_code, 302)
        account = data_store.load("users.json")["organizer"]
        self.assertIsNone(account["dept"])
        self.assertEqual(account["skills"], {})
        self.assertEqual(account["saved_events"], ["e1"])
        self.assertEqual(account["custom"], "keep")

    def test_student_profile_cannot_remove_required_fields(self):
        account = {
            "name": "Student", "role": "Student Volunteer",
            "dept": "CSE", "skills": {"Python": 2},
        }
        data_store.save("users.json", {"student": account})
        with self.client.session_transaction() as session:
            session["username"] = "student"
        for dept, skills, message in (
            ("", "Python:2", b"Enter your department"),
            ("CSE", "", b"Add at least one skill"),
        ):
            response = self.client.post("/profile/edit", data={
                "name": "Student", "dept": dept, "skills": skills,
                "csrf_token": self.csrf,
            })
            self.assertIn(message, response.data)
            self.assertEqual(data_store.load("users.json")["student"], account)


if __name__ == "__main__":
    unittest.main()
