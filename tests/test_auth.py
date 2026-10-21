from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("LOCAL_DATA_DIR", "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_NAME")
        }
        os.environ.update({
            "LOCAL_DATA_DIR": self.tempdir.name,
            "ADMIN_USERNAME": "owner",
            "ADMIN_PASSWORD": "owner-password-123",
            "ADMIN_NAME": "负责人",
        })
        self.admin = TestClient(app_module.app)
        response = self.admin.post(
            "/api/auth/login",
            json={"username": "owner", "password": "owner-password-123"},
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.admin.close()
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def create_member(self, features: list[str]) -> dict:
        response = self.admin.post("/api/admin/users", json={
            "username": "designer",
            "name": "设计成员",
            "password": "designer-password-123",
            "role": "member",
            "features": features,
        })
        self.assertEqual(response.status_code, 201)
        return response.json()["user"]

    def login_member(self) -> TestClient:
        client = TestClient(app_module.app)
        response = client.post("/api/auth/login", json={
            "username": "designer", "password": "designer-password-123",
        })
        self.assertEqual(response.status_code, 200)
        return client

    def test_login_is_required_and_login_page_has_no_registration(self):
        anonymous = TestClient(app_module.app)
        try:
            response = anonymous.get("/api/auth/me")
            self.assertEqual(response.status_code, 401)
            page = anonymous.get("/", follow_redirects=False)
            self.assertEqual(page.status_code, 303)
            self.assertTrue(page.headers["location"].startswith("/login"))
            login = anonymous.get("/login")
            self.assertEqual(login.status_code, 200)
            self.assertNotIn("注册账号", login.text)
        finally:
            anonymous.close()

    def test_member_can_only_use_granted_features(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            self.assertEqual(member.get("/text-to-image").status_code, 200)
            denied_page = member.get("/image-to-3d", follow_redirects=False)
            self.assertEqual(denied_page.status_code, 303)
            denied_api = member.post("/api/sessions")
            self.assertEqual(denied_api.status_code, 403)
            self.assertEqual(denied_api.json()["code"], "FEATURE_FORBIDDEN")
            self.assertEqual(member.get("/api/admin/users").status_code, 403)
        finally:
            member.close()

    def test_studio_home_is_the_landing_page_with_lockable_entries(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            portal = member.get("/portal", follow_redirects=False)
            self.assertEqual(portal.status_code, 303)
            self.assertEqual(portal.headers["location"], "/")
            home = member.get("/")
            self.assertEqual(home.status_code, 200)
            self.assertIn("/assets/home.js", home.text)
            self.assertIn('data-features="image_to_3d"', home.text)
            self.assertIn('data-features="workspace"', home.text)
        finally:
            member.close()

    def test_home_script_locks_features_without_permission(self):
        script = (
            Path(__file__).resolve().parent.parent / "frontend_dist" / "assets" / "home.js"
        ).read_text(encoding="utf-8")
        self.assertIn("is-locked", script)
        self.assertIn("lock-icon", script)
        self.assertIn("/api/auth/me", script)

    def test_workspace_is_open_to_every_account(self):
        self.create_member([])
        member = self.login_member()
        try:
            self.assertEqual(member.get("/tools/creative").status_code, 200)
            self.assertEqual(member.get("/api/jobs").status_code, 200)
            catalog = self.admin.get("/api/admin/users").json()["feature_catalog"]
            self.assertNotIn("workspace", catalog)
        finally:
            member.close()

    def test_workspace_cannot_be_assigned_or_revoked(self):
        user = self.create_member(["text_to_image"])
        response = self.admin.patch(
            f"/api/admin/users/{user['id']}",
            json={"features": ["workspace", "text_to_image"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["features"], ["text_to_image"])
        member = self.login_member()
        try:
            self.assertEqual(member.get("/tools/creative").status_code, 200)
        finally:
            member.close()

    def test_text_image_page_locks_the_module_without_permission(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            page = member.get("/text-to-image")
            self.assertEqual(page.status_code, 200)
            self.assertIn('data-feature="text_to_image"', page.text)
            self.assertIn('data-feature="animal_cutout"', page.text)
            self.assertIn('id="feature-lock-message"', page.text)
            self.assertEqual(member.get("/api/animal-cutout/prompt-template").status_code, 403)
        finally:
            member.close()
        script = (
            Path(__file__).resolve().parent.parent / "text2image_frontend" / "app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("is-locked", script)
        self.assertIn("/api/auth/me", script)

    def test_admin_can_delete_account_and_sessions_end(self):
        user = self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            self.assertEqual(member.get("/api/auth/me").status_code, 200)
            deleted = self.admin.delete(f"/api/admin/users/{user['id']}")
            self.assertEqual(deleted.status_code, 200)
            self.assertEqual(member.get("/api/auth/me").status_code, 401)
            usernames = [item["username"] for item in self.admin.get("/api/admin/users").json()["users"]]
            self.assertNotIn("designer", usernames)
        finally:
            member.close()

    def test_admin_cannot_delete_the_signed_in_account(self):
        current = self.admin.get("/api/auth/me").json()["user"]
        response = self.admin.delete(f"/api/admin/users/{current['id']}")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "SELF_DELETE")

    def test_member_can_change_own_password(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            changed = member.post("/api/auth/password", json={
                "current_password": "designer-password-123", "new_password": "new-designer-password-456",
            })
            self.assertEqual(changed.status_code, 200)
            self.assertEqual(member.get("/api/auth/me").status_code, 200)
        finally:
            member.close()
        again = TestClient(app_module.app)
        try:
            stale = again.post("/api/auth/login", json={
                "username": "designer", "password": "designer-password-123",
            })
            self.assertEqual(stale.status_code, 401)
            fresh = again.post("/api/auth/login", json={
                "username": "designer", "password": "new-designer-password-456",
            })
            self.assertEqual(fresh.status_code, 200)
        finally:
            again.close()

    def test_changing_password_requires_the_current_one(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            wrong = member.post("/api/auth/password", json={
                "current_password": "not-the-password", "new_password": "another-password-789",
            })
            self.assertEqual(wrong.status_code, 401)
            self.assertEqual(wrong.json()["code"], "PASSWORD_MISMATCH")
        finally:
            member.close()

    def test_admin_can_edit_features_and_disable_account(self):
        user = self.create_member([])
        member = self.login_member()
        response = self.admin.patch(
            f"/api/admin/users/{user['id']}",
            json={"features": ["image_to_3d"], "name": "三维设计成员"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(member.get("/image-to-3d").status_code, 200)

        response = self.admin.patch(
            f"/api/admin/users/{user['id']}", json={"active": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(member.get("/api/auth/me").status_code, 401)
        member.close()

    def test_short_non_empty_password_can_be_used(self):
        created = self.admin.post("/api/admin/users", json={
            "username": "short-password-user",
            "name": "短密码成员",
            "password": "1",
            "role": "member",
            "features": [],
        })
        self.assertEqual(created.status_code, 201)

        member = TestClient(app_module.app)
        try:
            logged_in = member.post("/api/auth/login", json={
                "username": "short-password-user", "password": "1",
            })
            self.assertEqual(logged_in.status_code, 200)
        finally:
            member.close()

    def test_last_active_admin_cannot_be_disabled_or_demoted(self):
        current = self.admin.get("/api/auth/me").json()["user"]
        disabled = self.admin.patch(
            f"/api/admin/users/{current['id']}", json={"active": False},
        )
        self.assertEqual(disabled.status_code, 409)
        self.assertEqual(disabled.json()["code"], "LAST_ADMIN")
        demoted = self.admin.patch(
            f"/api/admin/users/{current['id']}", json={"role": "member"},
        )
        self.assertEqual(demoted.status_code, 409)
        self.assertEqual(demoted.json()["code"], "LAST_ADMIN")


if __name__ == "__main__":
    unittest.main()
