"""Certificate PDF, verification, and authorization checks with temporary storage."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import certificate_service
import data_store
from app import app


class CertificateGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skillmatch-certificates-")
        self.root = Path(self.temp.name)
        self.data_patch = patch.object(data_store, "DATA_DIR", self.root / "data")
        self.data_patch.start()
        self.old_config = {
            key: app.config[key] for key in ("TESTING", "SESSION_COOKIE_SECURE")
        }
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        data_store.save(
            "users.json",
            {
                "organizer": {
                    "name": "Organizer One",
                    "role": "Organizer",
                    "skills": {},
                },
                "other_organizer": {
                    "name": "Organizer Two",
                    "role": "Organizer",
                    "skills": {},
                },
                "volunteer": {
                    "name": "Amelia Johnson",
                    "role": "Student Volunteer",
                    "skills": {},
                },
                "other_volunteer": {
                    "name": "Jordan Lee",
                    "role": "Student Volunteer",
                    "skills": {},
                },
                "other_role": {
                    "name": "Other User",
                    "role": "Other",
                    "skills": {},
                },
            },
        )
        data_store.save(
            "events.json",
            {
                "e1": {
                    "name": "Community Food Drive",
                    "organizer": "organizer",
                    "date": "2025-10-06",
                    "starts_at": "2025-10-06T10:00:00+00:00",
                    "status": "open",
                }
            },
        )
        data_store.save(
            "applications.json",
            {
                "e1": {
                    "volunteer": {"status": "accepted", "role": "Sound Crew"},
                }
            },
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(self.old_config)
        self.data_patch.stop()
        self.temp.cleanup()

    def sign_in(self, username, include_csrf=True):
        with self.client.session_transaction() as session:
            session["username"] = username
            if include_csrf:
                session["csrf_token"] = "test-csrf"

    def issue_directly(self):
        events = data_store.load("events.json")
        events["e1"]["status"] = "completed"
        data_store.save("events.json", events)
        applications = data_store.load("applications.json")
        applications["e1"]["volunteer"].update({"hours": 4, "notes": "Setup and welcome"})
        data_store.save("applications.json", applications)

        success, path = certificate_service.generate_certificate("e1", "volunteer")
        self.assertTrue(success, path)
        certificates = data_store.load("certificates.json")
        record = next(iter(certificates.values()))
        return record, certificate_service.render_certificate_pdf(record)

    def test_organizer_completion_issues_one_page_landscape_pdf_with_dates(self):
        self.sign_in("organizer")
        response = self.client.post(
            "/certificates/events/e1/complete",
            data={
                "csrf_token": "test-csrf",
                "hours_volunteer": "3",
                "notes_volunteer": "Welcomed attendees",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Event completed and certificates issued", response.data)

        event = data_store.load("events.json")["e1"]
        application = data_store.load("applications.json")["e1"]["volunteer"]
        self.assertEqual(event["status"], "completed")
        self.assertEqual(application["hours"], 3)
        self.assertEqual(application["notes"], "Welcomed attendees")

        record = next(iter(data_store.load("certificates.json").values()))
        self.assertEqual(record["event_date"], "2025-10-06")
        pdf = certificate_service.render_certificate_pdf(record)
        issue_date = datetime.fromisoformat(record["generated_at"]).strftime("%B %d, %Y")
        issue_date = issue_date.replace(" 0", " ")

        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
        self.assertRegex(pdf, rb"/MediaBox\s*\[\s*0\s+0\s+792\s+612\s*\]")
        self.assertIn(b"/Count 1", pdf)
        self.assertFalse((self.root / "data" / "certificates").exists())
        for expected in (
            b"CERTIFICATE OF",
            b"PARTICIPATION",
            b"Amelia Johnson",
            b"Community Food Drive",
            b"as Sound Crew",
            b"Organizer One",
            b"Event date: October 6, 2025",
            f"Issue date: {issue_date}".encode(),
            b"3 volunteer hours",
            b"Verification code available in your SkillMatch profile",
            b"Verify at /verify",
            b"Event Organizer Signature",
            b"/Subtype /Image",
            b"/DCTDecode",
            b"/XObject",
            b"/FontFile2",
            b"Fraunces",
            b"DMSans",
            b"W* n",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, pdf)
        self.assertNotIn(record["verification_code"].encode(), pdf)

    def test_certificate_capitalization_brand_fonts_and_optional_role(self):
        users = data_store.load("users.json")
        users["volunteer"]["name"] = "aMELia jOHNSON"
        data_store.save("users.json", users)
        events = data_store.load("events.json")
        events["e1"]["name"] = "community food drive"
        data_store.save("events.json", events)

        record, pdf = self.issue_directly()
        self.assertIn(b"Amelia Johnson", pdf)
        self.assertIn(b"Community Food Drive", pdf)
        self.assertIn(b"as Sound Crew", pdf)
        self.assertIn(b"Organizer One", pdf)
        self.assertIn(b"/FontFile2", pdf)
        self.assertEqual(
            certificate_service._event_display_name("hACKathon Service"),
            "hACKathon Service",
        )

        applications = data_store.load("applications.json")
        applications["e1"]["volunteer"]["role"] = None
        data_store.save("applications.json", applications)
        record["volunteer_role"] = None
        roleless_pdf = certificate_service.render_certificate_pdf(record)
        self.assertNotIn(b"as Sound Crew", roleless_pdf)

    def test_decimal_contribution_hours_are_saved_verified_and_included_in_pdf(self):
        self.sign_in("organizer")
        response = self.client.post(
            "/certificates/events/e1/complete",
            data={
                "csrf_token": "test-csrf",
                "hours_volunteer": "1.5",
                "notes_volunteer": "Supported setup",
            },
        )
        self.assertEqual(response.status_code, 200)
        application = data_store.load("applications.json")["e1"]["volunteer"]
        self.assertEqual(application["hours"], 1.5)
        record = next(iter(data_store.load("certificates.json").values()))
        self.assertEqual(certificate_service.verify_certificate(
            record["verification_code"]
        )["hours"], 1.5)
        self.assertIn(b"1.5 volunteer hours", certificate_service.render_certificate_pdf(record))

    def test_invalid_decimal_contribution_hours_are_rejected(self):
        for invalid in ("NaN", "inf", "-0.5", "not-a-number"):
            with self.subTest(hours=invalid):
                success, message = certificate_service.complete_event(
                    "e1",
                    {"volunteer": {"hours": invalid, "notes": ""}},
                )
                self.assertFalse(success)
                self.assertIn("non-negative finite number", message)
                self.assertEqual(
                    data_store.load("events.json")["e1"]["status"], "open"
                )

    def test_long_names_and_event_titles_fit_without_overflow(self):
        long_name = "Amelia Alexandria Johnson " * 5
        long_event = "Community Food and Supply Drive for Local Families " * 5
        name_lines, name_size = certificate_service._fit_pdf_text(
            long_name, 346, preferred_size=25, minimum_size=17, max_lines=2, bold=True,
        )
        event_lines, event_size = certificate_service._fit_pdf_text(
            long_event, 346, preferred_size=18, minimum_size=13, max_lines=2, bold=True,
        )
        self.assertLessEqual(len(name_lines), 2)
        self.assertLessEqual(len(event_lines), 2)
        self.assertTrue(all(
            certificate_service._estimated_text_width(line, name_size, True) <= 346
            for line in name_lines
        ))
        self.assertTrue(all(
            certificate_service._estimated_text_width(line, event_size, True) <= 346
            for line in event_lines
        ))

        pdf = certificate_service._render_certificate_pdf(
            long_name, long_event, 4, "2025-10-06", "2025-10-07",
        )
        self.assertIn(b"Amelia Alexandria Johnson", pdf)
        self.assertIn(b"Community Food and Supply Drive", pdf)
        self.assertRegex(pdf, rb"/MediaBox\s*\[\s*0\s+0\s+792\s+612\s*\]")
        self.assertIn(b"/Count 1", pdf)

    def test_public_verification_normalizes_code_and_shows_dates(self):
        record, _pdf_bytes = self.issue_directly()
        response = self.client.post(
            "/verify", data={"verification_code": record["verification_code"].lower()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Certificate verified and PDF available", response.data)
        self.assertIn(b"<strong>Volunteer:</strong> Amelia Johnson", response.data)
        self.assertIn(b"Community Food Drive", response.data)
        self.assertIn(b"<strong>Event date:</strong> October 6, 2025", response.data)
        self.assertIn(b"Issue date:", response.data)
        self.assertIn(b"Hours:</strong> 4", response.data)
        self.assertIn(record["verification_code"].encode(), response.data)

    def test_profile_shows_private_certificate_notification_and_prefilled_verification(self):
        record, _pdf = self.issue_directly()
        code = record["verification_code"]

        self.sign_in("volunteer")
        profile = self.client.get("/profile")
        self.assertEqual(profile.status_code, 200)
        self.assertIn(b"Certificate notifications", profile.data)
        self.assertIn(b"has been issued", profile.data)
        self.assertIn(code.encode(), profile.data)
        self.assertIn(f'href="/verify?code={code}"'.encode(), profile.data)

        verify = self.client.get(f"/verify?code={code.lower()}")
        self.assertEqual(verify.status_code, 200)
        self.assertIn(b"Certificate verified and PDF available", verify.data)

        self.sign_in("other_volunteer")
        other_profile = self.client.get("/profile")
        self.assertNotIn(code.encode(), other_profile.data)

    def test_empty_malformed_and_unknown_codes_are_not_found(self):
        for code in ("", "bad-code", "123456789", "UNKNOWN1234", "ZZZZZZZZZZ"):
            with self.subTest(code=code):
                response = self.client.post("/verify", data={"verification_code": code})
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"Certificate not found or invalid.", response.data)

    def test_record_without_stored_pdf_remains_verified_and_downloadable(self):
        code = "MISS1NGPDF"
        data_store.save(
            "certificates.json",
            {
                code: {
                    "verification_code": code,
                    "event_id": "e1",
                    "username": "volunteer",
                    "event_name": "Community Food Drive",
                    "volunteer_name": "Amelia Johnson",
                    "hours": 4,
                    "generated_at": "2025-10-07T12:00:00+00:00",
                }
            },
        )
        response = self.client.post("/verify", data={"verification_code": code})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Certificate verified and PDF available", response.data)
        self.assertNotIn(b"PDF file is unavailable", response.data)
        self.assertNotIn(b"username", response.data)
        self.assertNotIn(str(self.root).encode(), response.data)

        self.sign_in("volunteer")
        download = self.client.get(f"/certificates/{code}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.mimetype, "application/pdf")
        self.assertIn(f"filename=SkillMatch-{code}.pdf", download.headers["Content-Disposition"])
        self.assertTrue(download.data.startswith(b"%PDF-1.4"))
        self.assertFalse((self.root / "data" / "certificates").exists())
        download.close()

    def test_legacy_or_invalid_dates_do_not_break_verification(self):
        code = "LEGACY1234"
        data_store.save(
            "certificates.json",
            {
                code: {
                    "verification_code": code,
                    "event_id": "e1",
                    "username": "volunteer",
                    "event_name": "Community Food Drive",
                    "volunteer_name": "Amelia Johnson",
                    "hours": 4,
                }
            },
        )
        certificate = certificate_service.verify_certificate(code)
        self.assertEqual(certificate["event_date"], "October 6, 2025")
        self.assertIsNone(certificate["issue_date"])
        self.assertTrue(certificate["pdf_available"])

        events = data_store.load("events.json")
        events["e1"]["date"] = "not-a-date"
        events["e1"]["starts_at"] = "not-a-timestamp"
        data_store.save("events.json", events)
        record = data_store.load("certificates.json")[code]
        record["event_date"] = "also-not-a-date"
        record["generated_at"] = "also-not-a-timestamp"
        data_store.save("certificates.json", {code: record})

        certificate = certificate_service.verify_certificate(code)
        self.assertIsNone(certificate["event_date"])
        self.assertIsNone(certificate["issue_date"])
        self.assertTrue(certificate["pdf_available"])

    def test_legacy_certificate_can_be_regenerated_without_inventing_issue_date(self):
        code = "LEGACY1234"
        events = data_store.load("events.json")
        events["e1"]["status"] = "completed"
        data_store.save("events.json", events)
        applications = data_store.load("applications.json")
        applications["e1"]["volunteer"].update({"hours": 4, "notes": ""})
        data_store.save("applications.json", applications)
        data_store.save(
            "certificates.json",
            {
                code: {
                    "verification_code": code,
                    "event_id": "e1",
                    "username": "volunteer",
                    "event_name": "Old event title",
                    "volunteer_name": "Old volunteer name",
                    "hours": 4,
                    "notes": "",
                    "filename": f"{code}.pdf",
                }
            },
        )

        success, path = certificate_service.generate_certificate("e1", "volunteer")
        self.assertTrue(success, path)
        self.assertEqual(path, code)
        updated = data_store.load("certificates.json")[code]
        self.assertEqual(updated["event_date"], "2025-10-06")
        self.assertNotIn("generated_at", updated)
        pdf = certificate_service.render_certificate_pdf(updated)
        self.assertIn(b"Event date: October 6, 2025", pdf)
        self.assertIn(b"Issue date: Not recorded", pdf)

    def test_missing_event_date_is_not_replaced_with_today(self):
        events = data_store.load("events.json")
        events["e1"]["date"] = "invalid"
        events["e1"]["starts_at"] = "invalid"
        data_store.save("events.json", events)
        self.sign_in("organizer")
        response = self.client.post(
            "/certificates/events/e1/complete",
            data={"csrf_token": "test-csrf", "hours_volunteer": "2"},
        )
        self.assertEqual(response.status_code, 200)
        record = next(iter(data_store.load("certificates.json").values()))
        self.assertIsNone(record["event_date"])
        pdf = certificate_service.render_certificate_pdf(record)
        self.assertIn(b"Event date: Not recorded", pdf)

    def test_download_requires_volunteer_role_and_certificate_ownership(self):
        record, pdf = self.issue_directly()
        endpoint = f"/certificates/{record['verification_code']}/download"

        self.sign_in("volunteer")
        owner_response = self.client.get(endpoint)
        self.assertEqual(owner_response.status_code, 200)
        self.assertEqual(owner_response.mimetype, "application/pdf")
        self.assertIn(
            f"filename=SkillMatch-{record['verification_code']}.pdf",
            owner_response.headers["Content-Disposition"],
        )
        self.assertEqual(owner_response.data, pdf)
        self.assertFalse((self.root / "data" / "certificates").exists())
        owner_response.close()

        self.sign_in("other_volunteer")
        self.assertEqual(self.client.get(endpoint).status_code, 404)

        self.sign_in("organizer")
        self.assertEqual(self.client.get(endpoint).status_code, 403)

        anonymous = app.test_client().get(endpoint)
        self.assertEqual(anonymous.status_code, 302)

    def test_completion_route_keeps_role_owner_and_csrf_checks(self):
        endpoint = "/certificates/events/e1/complete"
        data = {"hours_volunteer": "2", "csrf_token": "test-csrf"}

        self.assertEqual(self.client.post(endpoint, data=data).status_code, 302)
        self.sign_in("volunteer")
        self.assertEqual(self.client.post(endpoint, data=data).status_code, 403)

        self.sign_in("organizer", include_csrf=False)
        self.assertEqual(
            self.client.post(endpoint, data={"hours_volunteer": "2"}).status_code, 400
        )

        events = data_store.load("events.json")
        events["e1"]["organizer"] = "other_organizer"
        data_store.save("events.json", events)
        self.sign_in("organizer")
        self.assertEqual(self.client.post(endpoint, data=data).status_code, 404)


if __name__ == "__main__":
    unittest.main()
