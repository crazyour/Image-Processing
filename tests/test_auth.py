from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from vercel_app.auth import database_provider, database_url


DATABASE_ENVIRONMENT = (
    "NEON_DATABASE_URL", "POSTGRES_URL", "DATABASE_URL", "DATABASE_URL_POOLED",
    "NEON_PROJECT_ID",
)


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.saved_environment = {
            name: os.environ.get(name)
            for name in (
                "LOCAL_DATA_DIR", "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_NAME",
                *DATABASE_ENVIRONMENT,
            )
        }
        for name in DATABASE_ENVIRONMENT:
            os.environ.pop(name, None)
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

    def test_neon_connection_variables_win_over_legacy_pooled_database(self):
        with patch.dict(os.environ, {
            "NEON_DATABASE_URL": "postgresql://neon-explicit.example/app",
            "POSTGRES_URL": "postgresql://neon-integration.example/app",
            "DATABASE_URL": "postgresql://neon-generic.example/app",
            "DATABASE_URL_POOLED": "postgresql://other-provider.example/app",
        }, clear=False):
            self.assertEqual(
                database_url(), "postgresql://neon-explicit.example/app",
            )

        with patch.dict(os.environ, {
            "NEON_DATABASE_URL": "",
            "POSTGRES_URL": "postgresql://ep-example.us-east-1.aws.neon.tech/app",
            "DATABASE_URL": "postgresql://generic.example/app",
            "DATABASE_URL_POOLED": "postgresql://other-provider.example/app",
            "NEON_PROJECT_ID": "project-id",
        }, clear=False):
            self.assertEqual(
                database_url(),
                "postgresql://ep-example.us-east-1.aws.neon.tech/app",
            )
            self.assertEqual(database_provider(), "neon-postgresql")

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
            self.assertIn('data-features="creative_studio"', home.text)
            self.assertIn('data-features="photography_center"', home.text)
        finally:
            member.close()

    def test_home_script_locks_features_without_permission(self):
        script = (
            Path(__file__).resolve().parent.parent / "frontend_dist" / "assets" / "home.js"
        ).read_text(encoding="utf-8")
        self.assertIn("is-locked", script)
        self.assertIn("lock-icon", script)
        self.assertIn("/api/auth/me", script)

    def test_admin_permission_dialog_groups_and_bulk_edits_all_features(self):
        root = Path(__file__).resolve().parent.parent / "auth_frontend"
        page = (root / "admin.html").read_text(encoding="utf-8")
        script = (root / "admin.js").read_text(encoding="utf-8")
        styles = (root / "styles.css").read_text(encoding="utf-8")

        self.assertIn('class="account-dialog permission-dialog"', page)
        self.assertIn('class="wide permission-fieldset"', page)
        self.assertIn('title: "核心生产功能"', script)
        self.assertIn('title: "工艺设计与交付"', script)
        self.assertIn('data-permission-action="all"', script)
        self.assertIn('data-permission-action="none"', script)
        self.assertIn(".permission-options", styles)
        self.assertIn("button:focus-visible", styles)

    def test_workbench_requires_an_individually_assigned_feature(self):
        self.create_member([])
        member = self.login_member()
        try:
            self.assertEqual(
                member.get("/tools/creative", follow_redirects=False).status_code, 303,
            )
            self.assertEqual(member.get("/api/jobs").status_code, 403)
            catalog = self.admin.get("/api/admin/users").json()["feature_catalog"]
            self.assertIn("creative_studio", catalog)
        finally:
            member.close()

    def test_cloud_workspace_reports_provider_managed_quota(self):
        self.create_member(["creative_studio"])
        member = self.login_member()
        try:
            workspace = member.get("/api/me")
            self.assertEqual(workspace.status_code, 200)
            self.assertEqual(workspace.json()["budget_scope"], "PROVIDER_ACCOUNT")

            budget = member.get("/api/budget")
            self.assertEqual(budget.status_code, 200)
            self.assertEqual(budget.json(), {
                "quota_source": "PROVIDER_ACCOUNT",
                "limit_micros": None,
                "held_micros": None,
                "spent_micros": None,
                "available_micros": None,
                "pools": [],
            })
        finally:
            member.close()

        resilience = (
            Path(__file__).resolve().parent.parent
            / "frontend_dist" / "assets" / "startup-resilience.js"
        ).read_text(encoding="utf-8")
        self.assertIn('quota_source: "PROVIDER_ACCOUNT"', resilience)
        self.assertIn("available_micros: null", resilience)
        self.assertNotIn("available_micros: 0", resilience)

    def test_dxf_page_marks_and_explains_the_paid_ai_step(self):
        root = Path(__file__).resolve().parent.parent / "frontend_dist"
        script = (root / "assets" / "tool-entry.js").read_text(encoding="utf-8")
        page = (root / "index.html").read_text(encoding="utf-8")

        self.assertIn('"/tools/dxf": { page: "DXF_STUDIO", title: "图片转 DXF", paidNotice:', script)
        self.assertIn('dataset.paidFeatureBanner = "true"', script)
        self.assertIn("使用 AI 清稿会调用图片模型并产生费用", script)
        self.assertIn("上传图片、本地轮廓平滑和 DXF 导出免费", script)
        self.assertIn('id="paid-feature-message"', page)

    def test_every_paid_entry_has_the_fee_banner(self):
        root = Path(__file__).resolve().parent.parent
        script = (root / "frontend_dist" / "assets" / "tool-entry.js").read_text(encoding="utf-8")
        for route in ("creative", "photo-craft", "products", "color-finish", "dxf"):
            line = next(line for line in script.splitlines() if f'"/tools/{route}"' in line)
            self.assertIn("paidNotice:", line)
        for route in ("svg", "files", "delivery"):
            line = next(line for line in script.splitlines() if f'"/tools/{route}"' in line)
            self.assertNotIn("paidNotice:", line)

        text_image = (root / "text2image_frontend" / "index.html").read_text(encoding="utf-8")
        image_3d = (root / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="paid-feature-banner"', text_image)
        self.assertIn("AI 图片生成", text_image)
        self.assertIn("照片转镂空剪影", text_image)
        self.assertIn('class="paid-feature-banner"', image_3d)
        self.assertIn("3D 模型生成会按实际调用产生费用", image_3d)

    def test_visual_studio_uses_the_live_image_workflow_and_its_permission(self):
        user = self.create_member([])
        member = self.login_member()
        try:
            denied = member.get("/tools/visual", follow_redirects=False)
            self.assertEqual(denied.status_code, 303)

            granted = self.admin.patch(
                f"/api/admin/users/{user['id']}",
                json={"features": ["visual_studio"]},
            )
            self.assertEqual(granted.status_code, 200)
            page = member.get("/tools/visual")
            self.assertEqual(page.status_code, 200)
            self.assertIn('id="task-form"', page.text)
            self.assertIn("/text-image-static/app.js", page.text)
            self.assertNotIn("/assets/tool-entry.js", page.text)
        finally:
            member.close()

    def test_http_errors_use_code_and_message_contract(self):
        response = self.admin.get("/api/route-that-does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"code": "NOT_FOUND", "message": "Not Found"})

    def test_login_database_failure_returns_actionable_json(self):
        with patch("vercel_app.auth._connection", side_effect=sqlite3.OperationalError("offline")):
            response = self.admin.post("/api/auth/login", json={
                "username": "owner", "password": "owner-password-123",
            })
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "AUTH_STORAGE_UNAVAILABLE")
        self.assertIn("DATABASE_URL", response.json()["message"])

    def test_legacy_workspace_permission_is_rejected(self):
        user = self.create_member(["text_to_image"])
        response = self.admin.patch(
            f"/api/admin/users/{user['id']}",
            json={"features": ["workspace", "text_to_image"]},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "INVALID_FEATURE")
        member = self.login_member()
        try:
            self.assertEqual(
                member.get("/tools/creative", follow_redirects=False).status_code, 303,
            )
        finally:
            member.close()

    def test_catalog_and_routes_cover_every_home_function(self):
        expected = {
            "text_to_image", "animal_cutout", "image_to_3d", "similarity_search",
            "asset_library", "creative_studio", "photo_craft", "photography_center",
            "product_center", "color_finish", "visual_studio", "svg_studio",
            "dxf_studio", "file_center", "delivery_center",
        }
        catalog = self.admin.get("/api/admin/users").json()["feature_catalog"]
        self.assertEqual(set(catalog), expected)

        user = self.create_member([])
        member = self.login_member()
        route_features = {
            "/tools/creative": "creative_studio",
            "/tools/photo-craft": "photo_craft",
            "/photography-center": "photography_center",
            "/tools/products": "product_center",
            "/tools/color-finish": "color_finish",
            "/tools/visual": "visual_studio",
            "/tools/svg": "svg_studio",
            "/tools/dxf": "dxf_studio",
            "/tools/files": "file_center",
            "/tools/delivery": "delivery_center",
        }
        try:
            routes = list(route_features)
            for index, (route, feature) in enumerate(route_features.items()):
                updated = self.admin.patch(
                    f"/api/admin/users/{user['id']}", json={"features": [feature]},
                )
                self.assertEqual(updated.status_code, 200)
                self.assertEqual(member.get(route).status_code, 200)
                unrelated = routes[(index + 1) % len(routes)]
                self.assertEqual(
                    member.get(unrelated, follow_redirects=False).status_code, 303,
                )
        finally:
            member.close()

    def test_existing_members_keep_previously_open_tools_after_upgrade(self):
        user = self.create_member(["text_to_image"])
        database = Path(self.tempdir.name) / "image3d.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "DELETE FROM auth_migrations WHERE migration_id = ?",
                ("assignable-features-v1",),
            )
            connection.execute(
                "UPDATE auth_users SET features = ? WHERE id = ?",
                ('["text_to_image"]', user["id"]),
            )

        migrated = next(
            item for item in self.admin.get("/api/admin/users").json()["users"]
            if item["id"] == user["id"]
        )
        self.assertIn("text_to_image", migrated["features"])
        self.assertIn("creative_studio", migrated["features"])
        self.assertIn("photography_center", migrated["features"])

        updated = self.admin.patch(
            f"/api/admin/users/{user['id']}", json={"features": ["creative_studio"]},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["user"]["features"], ["creative_studio"])

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
