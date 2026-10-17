from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import app as app_module


def sample_png() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (128, 128), (70, 100, 130)).save(output, "PNG")
    return output.getvalue()


class LocalLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("LOCAL_DATA_DIR")
        self.previous_vercel = os.environ.pop("VERCEL", None)
        self.previous_database = os.environ.pop("DATABASE_URL", None)
        os.environ["LOCAL_DATA_DIR"] = self.tempdir.name
        self.client = TestClient(app_module.app)
        self.library_headers = {"X-Local-Library": "1"}

    def tearDown(self):
        self.client.close()
        if self.previous_data_dir is None:
            os.environ.pop("LOCAL_DATA_DIR", None)
        else:
            os.environ["LOCAL_DATA_DIR"] = self.previous_data_dir
        if self.previous_vercel is not None:
            os.environ["VERCEL"] = self.previous_vercel
        if self.previous_database is not None:
            os.environ["DATABASE_URL"] = self.previous_database
        self.tempdir.cleanup()

    def create_task(self):
        created = self.client.post("/api/text-image/tasks", json={
            "prompts": ["一把放在展台上的木椅"],
            "model": "test-image-model",
            "quality": "medium",
            "size": "1024x1024",
        })
        self.assertEqual(created.status_code, 201)
        return created.json()

    def test_history_requires_local_library_header(self):
        self.assertEqual(self.client.get("/api/local-library/tasks").status_code, 404)
        self.assertEqual(
            self.client.get("/api/local-library/tasks", headers=self.library_headers).status_code,
            200,
        )

    def test_saved_preferences_are_applied_and_visible_in_history(self):
        saved = self.client.put(
            "/api/local-library/preferences",
            headers=self.library_headers,
            json={"enabled": True, "prompt": "背景简洁，使用柔和侧光"},
        )
        self.assertEqual(saved.status_code, 200)
        task = self.create_task()
        self.assertEqual(task["preference_prompt"], "背景简洁，使用柔和侧光")

        with patch.object(app_module, "generate_image", return_value=(sample_png(), {})) as generate:
            generated = self.client.post(
                f"/api/text-image/tasks/{task['task_id']}/items/0/generate",
                headers={"X-Session-Token": task["access_token"]},
            )
        self.assertEqual(generated.status_code, 200)
        self.assertIn("背景简洁，使用柔和侧光", generate.call_args.args[0])

        history = self.client.get("/api/local-library/tasks", headers=self.library_headers).json()
        self.assertEqual(len(history["items"]), 1)
        self.assertTrue(history["items"][0]["preference_applied"])
        self.assertTrue(history["items"][0]["preview_url"].startswith("/local-blobs/"))

    def test_old_record_can_be_renamed_favorited_and_resumed(self):
        task = self.create_task()
        task_id = task["task_id"]
        updated = self.client.patch(
            f"/api/local-library/tasks/{task_id}",
            headers=self.library_headers,
            json={"title": "常用木椅方案", "favorite": True},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["title"], "常用木椅方案")
        self.assertTrue(updated.json()["favorite"])

        resumed = self.client.post(
            f"/api/local-library/tasks/{task_id}/resume",
            headers=self.library_headers,
        )
        self.assertEqual(resumed.status_code, 200)
        fresh_token = resumed.json()["access_token"]
        self.assertNotEqual(fresh_token, task["access_token"])
        self.assertEqual(
            self.client.get(
                f"/api/text-image/tasks/{task_id}",
                headers={"X-Session-Token": task["access_token"]},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                f"/api/text-image/tasks/{task_id}",
                headers={"X-Session-Token": fresh_token},
            ).status_code,
            200,
        )


if __name__ == "__main__":
    unittest.main()
