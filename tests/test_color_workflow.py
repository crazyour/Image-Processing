from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import app as app_module
from vercel_app.store import save_view_folder


def sample_png(color=(80, 110, 140)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (96, 96), color).save(output, "PNG")
    return output.getvalue()


class ColorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.saved_environment = {name: os.environ.get(name) for name in (
            "LOCAL_DATA_DIR", "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_NAME",
        )}
        self.previous_vercel = os.environ.pop("VERCEL", None)
        self.previous_database = os.environ.pop("DATABASE_URL", None)
        os.environ.update({
            "LOCAL_DATA_DIR": self.tempdir.name,
            "ADMIN_USERNAME": "color-admin",
            "ADMIN_PASSWORD": "color-password",
            "ADMIN_NAME": "配色管理员",
        })
        self.client = TestClient(app_module.app)
        response = self.client.post("/api/auth/login", json={
            "username": "color-admin", "password": "color-password",
        })
        self.assertEqual(response.status_code, 200)
        self.folder = save_view_folder("木马", {
            view: {"url": f"/local-blobs/source/{view}.png", "sha256": view}
            for view in ("front", "left", "right", "back")
        })

    def tearDown(self):
        self.client.close()
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.previous_vercel is not None:
            os.environ["VERCEL"] = self.previous_vercel
        if self.previous_database is not None:
            os.environ["DATABASE_URL"] = self.previous_database
        self.tempdir.cleanup()

    @patch.object(app_module, "_metered_provider_call", side_effect=lambda user, feature, model, action, call: call())
    @patch.object(app_module, "fetch_bytes", return_value=sample_png())
    @patch.object(app_module, "generate_view", return_value=(sample_png((30, 90, 130)), {"calls": 1}))
    @patch.object(app_module, "analyze_coloring")
    def test_front_complete_and_save_as_child_scheme(self, analyze, generate, fetch, metered):
        analyze.return_value = ({
            "object": "木马", "parts": ["身体", "底座"], "materials": ["木材"],
            "style": "童趣", "palette": ["海蓝 #1E5C8A 身体"],
            "coloring_prompt": "身体海蓝，底座原木色", "scheme_name": "海蓝木趣", "warnings": [],
        }, {"calls": 1})

        front = self.client.post(f"/api/view-library/folders/{self.folder['id']}/color-drafts/front")
        self.assertEqual(front.status_code, 200, front.text)
        draft = front.json()
        self.assertEqual(draft["status"], "FRONT_READY")
        self.assertEqual(set(draft["views"]), {"front"})

        rerolled = self.client.post(
            f"/api/view-library/folders/{self.folder['id']}/color-drafts/front",
            json={"previous_name": draft["name"], "previous_palette": draft["analysis"]["palette"]},
        )
        self.assertEqual(rerolled.status_code, 200, rerolled.text)
        draft = rerolled.json()
        self.assertEqual(analyze.call_count, 2)
        self.assertEqual(analyze.call_args.args[2]["name"], "海蓝木趣")

        completed = self.client.post(f"/api/view-library/color-drafts/{draft['id']}/complete")
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(set(completed.json()["views"]), {"front", "left", "right", "back"})

        saved = self.client.post(
            f"/api/view-library/color-drafts/{draft['id']}/save", json={"name": None},
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["name"], "海蓝木趣")

        folders = self.client.get("/api/view-library/folders").json()["folders"]
        parent = next(item for item in folders if item["id"] == self.folder["id"])
        self.assertEqual(parent["color_schemes"][0]["name"], "海蓝木趣")
        self.assertEqual(generate.call_count, 5)
        for call in generate.call_args_list:
            self.assertEqual(call.args[2:4], ("gpt-image-2.5-flare", "medium"))

    def test_frontend_exposes_staged_coloring_actions(self):
        html = (app_module.ROOT / "image3d_frontend" / "index.html").read_text()
        javascript = (app_module.ROOT / "image3d_frontend" / "app.js").read_text()
        for control in ("generate-color-front", "retry-color-front", "complete-color-views", "save-color-scheme"):
            self.assertIn(f'id="{control}"', html)
        self.assertIn("/color-drafts/front", javascript)
        self.assertIn("/complete`", javascript)
        self.assertIn("/save`", javascript)


if __name__ == "__main__":
    unittest.main()
