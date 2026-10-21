from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import app as app_module


class DeliverableUploadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("LOCAL_DATA_DIR", "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_NAME")
        }
        os.environ["LOCAL_DATA_DIR"] = self.tempdir.name
        os.environ.update({
            "ADMIN_USERNAME": "asset-admin",
            "ADMIN_PASSWORD": "asset-password",
            "ADMIN_NAME": "素材库管理员",
        })
        self.client = TestClient(app_module.app)
        logged_in = self.client.post("/api/auth/login", json={
            "username": "asset-admin", "password": "asset-password",
        })
        self.assertEqual(logged_in.status_code, 200)

    def tearDown(self):
        self.client.close()
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def test_page_is_linked_from_feature_list(self):
        home = self.client.get("/")
        self.assertIn('href="/file-upload"', home.text)
        self.assertIn("成果素材库", home.text)
        self.assertNotIn("成果文件上传", home.text)
        self.assertEqual(self.client.get("/file-upload").status_code, 200)

    def test_uploads_non_image_deliverable_and_returns_download(self):
        response = self.client.post(
            "/api/deliverables/upload",
            files={"file": ("model.stl", b"solid model\nendsolid model", "model/stl")},
            data={"task": "image-3d"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["name"], "model.stl")
        self.assertEqual(payload["task"], "image-3d")
        self.assertEqual(payload["task_label"], "图生 3D")
        self.assertTrue(payload["url"].startswith("/local-blobs/deliverables/"))
        self.assertEqual(self.client.get(payload["url"]).content, b"solid model\nendsolid model")
        listed = self.client.get("/api/deliverables")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["name"], "model.stl")
        self.assertEqual(listed.json()["items"][0]["type"], "file")
        self.assertEqual(listed.json()["items"][0]["task"], "image-3d")

    def test_upload_is_attributed_to_the_signed_in_account(self):
        response = self.client.post(
            "/api/deliverables/upload",
            files={"file": ("design.dxf", b"dxf", "application/dxf")},
            data={"task": "design-engineering"},
        )
        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload["uploader_username"], "asset-admin")
        self.assertEqual(payload["uploader_name"], "素材库管理员")
        listed = self.client.get("/api/deliverables").json()
        self.assertEqual(listed["items"][0]["uploader_username"], "asset-admin")
        self.assertEqual(listed["accounts"][0]["username"], "asset-admin")
        self.assertIn(
            {"key": "design-engineering", "label": "设计与工程文件"},
            listed["tasks"],
        )

    def test_assets_from_different_accounts_and_types_are_grouped(self):
        self.client.post(
            "/api/admin/users", json={
                "username": "member", "name": "成员甲", "password": "member-password",
                "role": "member", "features": ["asset_library"],
            },
        )
        member = TestClient(app_module.app)
        member.post("/api/auth/login", json={
            "username": "member", "password": "member-password",
        })
        try:
            member.post(
                "/api/deliverables/upload",
                files={"file": ("photo.png", b"png", "image/png")},
                data={"task": "text-image"},
            )
        finally:
            member.close()
        self.client.post(
            "/api/deliverables/upload",
            files={"file": ("plan.dxf", b"dxf", "application/dxf")},
            data={"task": "design-engineering"},
        )
        listed = self.client.get("/api/deliverables").json()
        self.assertEqual(len(listed["items"]), 2)
        self.assertEqual(len(listed["accounts"]), 2)
        by_account = {item["uploader_username"]: item for item in listed["items"]}
        self.assertEqual(by_account["member"]["task"], "text-image")
        self.assertEqual(by_account["asset-admin"]["task"], "design-engineering")

    def test_member_without_library_permission_cannot_upload(self):
        self.client.post("/api/admin/users", json={
            "username": "outsider", "name": "外部成员", "password": "outsider-password",
            "role": "member", "features": ["text_to_image"],
        })
        outsider = TestClient(app_module.app)
        outsider.post("/api/auth/login", json={
            "username": "outsider", "password": "outsider-password",
        })
        try:
            response = outsider.post(
                "/api/deliverables/upload",
                files={"file": ("secret.png", b"png", "image/png")},
                data={"task": "text-image"},
            )
            self.assertEqual(response.status_code, 403)
        finally:
            outsider.close()

    def test_rejects_unknown_task(self):
        response = self.client.post(
            "/api/deliverables/upload",
            files={"file": ("asset.png", b"png", "image/png")},
            data={"task": "unknown-task"},
        )
        self.assertEqual(response.status_code, 422)

    def test_rejects_unsupported_and_empty_files(self):
        unsupported = self.client.post(
            "/api/deliverables/upload",
            files={"file": ("page.html", b"<script></script>", "text/html")},
        )
        self.assertEqual(unsupported.status_code, 422)
        empty = self.client.post(
            "/api/deliverables/upload",
            files={"file": ("empty.png", b"", "image/png")},
        )
        self.assertEqual(empty.status_code, 422)

    def test_rejects_file_over_deployment_limit(self):
        response = self.client.post(
            "/api/deliverables/upload",
            files={"file": ("large.zip", b"x" * 4_000_001, "application/zip")},
        )
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main()
