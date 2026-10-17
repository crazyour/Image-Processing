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

    def test_one_prompt_can_create_multiple_candidate_items(self):
        prompt = "同一只狗的黑白镂空剪影"
        response = self.client.post("/api/text-image/tasks", json={
            "prompts": [prompt, prompt, prompt],
            "model": "test-image-model",
            "quality": "medium",
            "size": "1024x1024",
        })

        self.assertEqual(response.status_code, 201)
        items = response.json()["items"]
        self.assertEqual(len(items), 3)
        self.assertEqual([item["prompt"] for item in items], [prompt, prompt, prompt])

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
        self.assertEqual(len(uploaded.json()["reference_images"]), 1)
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
        edit_prompt = edit_image.call_args.args[1]
        self.assertIn("前 1 张是产品图片", edit_prompt)
        self.assertIn("把主体改成柔和的粘土风格", edit_prompt)

    def test_multiple_references_are_saved_and_used_together(self):
        task_id, headers = self.create_task()
        uploaded = self.client.post(
            f"/api/text-image/tasks/{task_id}/references",
            headers=headers,
            files=[
                ("images", ("primary.png", sample_png((20, 40, 60)), "image/png")),
                ("images", ("detail.png", sample_png((180, 160, 140)), "image/png")),
            ],
        )

        self.assertEqual(uploaded.status_code, 200)
        self.assertEqual(
            [item["filename"] for item in uploaded.json()["reference_images"]],
            ["primary.png", "detail.png"],
        )

        with patch.object(app_module, "generate_view", return_value=(sample_png(), {})) as edit_image:
            response = self.client.post(
                f"/api/text-image/tasks/{task_id}/items/0/generate", headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        reference_inputs = edit_image.call_args.args[0]
        self.assertEqual(len(reference_inputs), 2)
        with Image.open(io.BytesIO(reference_inputs[0])) as primary:
            self.assertEqual(primary.convert("RGB").getpixel((0, 0)), (20, 40, 60))
        with Image.open(io.BytesIO(reference_inputs[1])) as detail:
            self.assertEqual(detail.convert("RGB").getpixel((0, 0)), (180, 160, 140))

    def test_product_and_example_references_keep_distinct_roles(self):
        task_id, headers = self.create_task()
        uploaded = self.client.post(
            f"/api/text-image/tasks/{task_id}/references",
            headers=headers,
            files=[
                ("product_images", ("chair.png", sample_png((30, 60, 90)), "image/png")),
                ("product_images", ("chair-side.png", sample_png((40, 70, 100)), "image/png")),
                ("example_images", ("room-layout.png", sample_png((210, 190, 170)), "image/png")),
            ],
        )

        self.assertEqual(uploaded.status_code, 200)
        body = uploaded.json()
        self.assertEqual(len(body["product_reference_images"]), 2)
        self.assertEqual(len(body["example_reference_images"]), 1)
        self.assertEqual(
            [item["filename"] for item in body["reference_images"]],
            ["chair.png", "chair-side.png", "room-layout.png"],
        )

        with patch.object(app_module, "generate_view", return_value=(sample_png(), {})) as edit_image:
            response = self.client.post(
                f"/api/text-image/tasks/{task_id}/items/0/generate", headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(edit_image.call_args.args[0]), 3)
        edit_prompt = edit_image.call_args.args[1]
        self.assertIn("前 2 张是产品图片", edit_prompt)
        self.assertIn("第 3 至 3 张是生成样例图片", edit_prompt)
        self.assertIn("不要把样例中的其他产品当成目标产品", edit_prompt)

    def test_reference_count_is_limited(self):
        task_id, headers = self.create_task()
        files = [
            ("images", (f"reference-{index}.png", sample_png(), "image/png"))
            for index in range(7)
        ]
        uploaded = self.client.post(
            f"/api/text-image/tasks/{task_id}/references", headers=headers, files=files,
        )

        self.assertEqual(uploaded.status_code, 422)
        self.assertEqual(uploaded.json()["code"], "TOO_MANY_PRODUCT_REFERENCES")

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
