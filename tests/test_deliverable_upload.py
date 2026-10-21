from __future__ import annotations

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

import app as app_module


class DeliverableUploadTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("LOCAL_DATA_DIR")
        os.environ["LOCAL_DATA_DIR"] = self.tempdir.name
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        if self.previous_data_dir is None:
            os.environ.pop("LOCAL_DATA_DIR", None)
        else:
            os.environ["LOCAL_DATA_DIR"] = self.previous_data_dir
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
