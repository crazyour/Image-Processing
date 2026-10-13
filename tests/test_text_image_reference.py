from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import app as app_module


def sample_png(color: tuple[int, int, int] = (80, 120, 160)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (128, 128), color).save(output, "PNG")
    return output.getvalue()


class TextImageReferenceTests(unittest.TestCase):
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

    def create_task(self):
        response = self.client.post("/api/text-image/tasks", json={
            "prompts": ["把主体改成柔和的粘土风格"],
            "model": "test-image-model",
            "quality": "medium",
            "size": "1024x1024",
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        return body["task_id"], {"X-Session-Token": body["access_token"]}

    def test_reference_is_saved_and_used_for_image_editing(self):
        task_id, headers = self.create_task()
        reference = sample_png()

        uploaded = self.client.post(
            f"/api/text-image/tasks/{task_id}/reference",
            headers=headers,
            files={"image": ("reference.png", reference, "image/png")},
        )

        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(uploaded.json()["reference_image"]["filename"], "reference.png")
        self.assertTrue(uploaded.json()["reference_image"]["url"].startswith("/local-blobs/"))

        generated = sample_png((200, 180, 120))
        with patch.object(app_module, "generate_view", return_value=(generated, {})) as edit_image, \
             patch.object(app_module, "generate_image") as text_image:
            response = self.client.post(
                f"/api/text-image/tasks/{task_id}/items/0/generate", headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["status"], "SUCCEEDED")
        svg_url = response.json()["items"][0]["svg_url"]
        self.assertTrue(svg_url.endswith("/1.svg"))
        svg_response = self.client.get(svg_url)
        self.assertEqual(svg_response.status_code, 200)
        self.assertEqual(svg_response.headers["content-type"], "image/svg+xml")
        self.assertIn(b'<image width="1024" height="1024"', svg_response.content)
        self.assertIn(b'href="data:image/png;base64,', svg_response.content)
        text_image.assert_not_called()
        self.assertEqual(edit_image.call_count, 1)
        reference_inputs = edit_image.call_args.args[0]
        self.assertEqual(len(reference_inputs), 1)
        with Image.open(io.BytesIO(reference_inputs[0])) as normalized:
            self.assertEqual(normalized.size, (128, 128))
            self.assertEqual(normalized.convert("RGB").getpixel((0, 0)), (80, 120, 160))
        self.assertEqual(edit_image.call_args.args[1], "把主体改成柔和的粘土风格")

    def test_reference_cannot_change_after_generation_starts(self):
        task_id, headers = self.create_task()
        with patch.object(app_module, "generate_image", return_value=(sample_png(), {})):
            generated = self.client.post(
                f"/api/text-image/tasks/{task_id}/items/0/generate", headers=headers,
            )
        self.assertEqual(generated.status_code, 200)

        uploaded = self.client.post(
            f"/api/text-image/tasks/{task_id}/reference",
            headers=headers,
            files={"image": ("late.png", sample_png(), "image/png")},
        )
        self.assertEqual(uploaded.status_code, 409)
        self.assertEqual(uploaded.json()["code"], "REFERENCE_LOCKED")


if __name__ == "__main__":
    unittest.main()
