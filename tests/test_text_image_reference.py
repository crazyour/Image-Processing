from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import cv2
from fastapi.testclient import TestClient
import numpy as np
from PIL import Image

import app as app_module


def sample_png(color: tuple[int, int, int] = (80, 120, 160)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (128, 128), color).save(output, "PNG")
    return output.getvalue()


class ImageSvgTracingTests(unittest.TestCase):
    @staticmethod
    def dxf_vertices(raw: bytes) -> np.ndarray:
        dxf = raw.decode("ascii").replace("\r\n", "\n")
        pairs = re.findall(
            r"0\nVERTEX\n.*?10\n(-?\d+\.\d+)\n20\n(-?\d+\.\d+)",
            dxf,
            flags=re.DOTALL,
        )
        return np.asarray([(float(x), float(y)) for x, y in pairs])

    def test_generated_svg_rebuilds_image_from_solid_color_paths(self):
        output = io.BytesIO()
        image = Image.new("RGB", (128, 128), "white")
        for x in range(32, 96):
            for y in range(32, 96):
                image.putpixel((x, y), (210, 43, 43))
        image.save(output, "PNG")

        svg = app_module._image_svg(output.getvalue(), "128x128")

        self.assertIn(b'<path id="region-0" class="vector-region"', svg)
        self.assertIn(b'.vector-region:hover', svg)
        self.assertIn(b'viewBox="0 0 128 128"', svg)
        self.assertIn(b'fill="#ffffff"', svg)
        fills = re.findall(rb'fill="#([0-9a-f]{6})"', svg)
        saturated = [tuple(bytes.fromhex(value.decode("ascii"))) for value in fills]
        self.assertTrue(any(max(color) - min(color) > 180 for color in saturated))
        self.assertEqual(svg.count(b' stroke="#'), svg.count(b'<path '))
        self.assertIn(b'paint-order: stroke fill', svg)
        self.assertIn(
            f'stroke-width: {app_module.COLOR_SVG_SEAM_STROKE_WIDTH:g}'.encode(), svg,
        )
        self.assertNotIn(b'<image', svg)
        self.assertNotIn(b'data:image/', svg)
        self.assertNotIn(b'fill="none"', svg)
        self.assertNotIn(b'fill="transparent"', svg)

    def test_generated_svg_uses_closed_rounded_region_paths(self):
        output = io.BytesIO()
        image = Image.new("RGB", (128, 128), (210, 43, 43))
        for x in range(64, 128):
            for y in range(128):
                image.putpixel((x, y), (43, 80, 210))
        image.save(output, "PNG")

        svg = app_module._image_svg(output.getvalue(), "128x128")

        self.assertGreaterEqual(svg.count(b'<path '), 2)
        region_paths = re.findall(rb'class="vector-region" d="([^"]+)"', svg)
        self.assertTrue(region_paths)
        self.assertTrue(all(path.endswith(b"Z") for path in region_paths))
        self.assertTrue(any(b"Q" in path for path in region_paths))
        self.assertNotIn(b'vector-effect: non-scaling-stroke', svg)
        self.assertIn(b'shape-rendering: geometricPrecision', svg)
        self.assertIn(b'.vector-region:hover { stroke: #00d9ff; stroke-width: 3; }', svg)

    def test_generated_svg_keeps_detached_same_color_blobs_as_separate_vectors(self):
        canvas = np.full((128, 128, 3), 255, dtype=np.uint8)
        cv2.circle(canvas, (36, 64), 18, (210, 43, 43), -1)
        cv2.circle(canvas, (92, 64), 18, (210, 43, 43), -1)
        ok, encoded = cv2.imencode(".png", canvas)
        self.assertTrue(ok)

        svg = app_module._image_svg(encoded.tobytes(), "128x128")
        connected_paths = re.findall(
            rb'<path [^>]*d="([^"]+)"[^>]*data-connected="true"[^>]*/>', svg,
        )

        self.assertEqual(len(connected_paths), 2)
        self.assertTrue(all(path.count(b"M") == 1 for path in connected_paths))
        self.assertTrue(all(path.endswith(b"Z") for path in connected_paths))

    def test_generated_svg_merges_only_small_irregular_color_fragments(self):
        indexes = np.zeros((24, 24), dtype=np.uint8)
        indexes[2:22, 2:22] = 1
        # Tiny, non-rectangular fragment inside the large region.
        indexes[8, 8:11] = 2
        indexes[9, 8:10] = 2
        # A larger irregular feature must remain independent.
        indexes[14:18, 14:18] = 3
        indexes[13, 15:17] = 3
        palette = [0] * (256 * 3)
        palette[3:6] = [40, 100, 180]
        palette[6:9] = [45, 105, 185]
        palette[9:12] = [230, 190, 40]

        merged = app_module._merge_small_color_regions(
            indexes, palette, np.full(indexes.shape, 255, dtype=np.uint8),
            min_region_area=8,
        )

        self.assertNotIn(2, np.unique(merged))
        self.assertEqual(int(np.count_nonzero(merged == 3)), 18)
        self.assertFalse(np.array_equal(merged == 1, np.zeros_like(merged, dtype=bool)))

    def test_generated_dxf_contains_only_the_closed_outer_outline(self):
        output = io.BytesIO()
        image = Image.new("RGB", (128, 128), "white")
        for x in range(32, 96):
            for y in range(32, 96):
                image.putpixel((x, y), (210, 43, 43))
        image.save(output, "PNG")

        dxf = app_module._image_dxf(output.getvalue()).decode("ascii").replace("\r\n", "\n")

        self.assertIn("$ACADVER\n1\nAC1009", dxf)
        self.assertIn("0\nPOLYLINE\n", dxf)
        self.assertIn("0\nVERTEX\n", dxf)
        self.assertIn("0\nSEQEND\n", dxf)
        self.assertEqual(dxf.count("0\nPOLYLINE\n"), 1)
        self.assertIn("2\nOUTLINE\n", dxf)
        self.assertNotIn("COLOR_", dxf)
        self.assertTrue(dxf.endswith("0\nEOF\n"))

    def test_generated_dxf_keeps_meaningful_detached_outer_outlines(self):
        output = io.BytesIO()
        image = Image.new("RGB", (160, 120), "white")
        for x in range(20, 100):
            for y in range(20, 100):
                image.putpixel((x, y), (20, 20, 20))
        for x in range(125, 140):
            for y in range(40, 55):
                image.putpixel((x, y), (20, 20, 20))
        image.save(output, "PNG")

        dxf = app_module._image_dxf(output.getvalue()).decode("ascii").replace("\r\n", "\n")

        self.assertEqual(dxf.count("0\nPOLYLINE\n"), 2)
        x_coordinates = [float(value) for value in re.findall(r"0\nVERTEX\n.*?10\n([0-9.]+)\n", dxf, re.S)]
        self.assertTrue(x_coordinates)
        self.assertGreater(max(x_coordinates), 75.0)

    def test_generated_dxf_ignores_internal_detail_and_tiny_noise(self):
        output = io.BytesIO()
        image = Image.new("RGB", (160, 120), "white")
        for x in range(20, 140):
            for y in range(15, 105):
                image.putpixel((x, y), (210, 43, 43))
        # A contrasting feature inside the subject must not become a DXF path.
        for x in range(65, 95):
            for y in range(45, 75):
                image.putpixel((x, y), (20, 20, 20))
        # A one-pixel background artifact must not become a detached outline.
        image.putpixel((5, 5), (20, 20, 20))
        image.save(output, "PNG")

        dxf = app_module._image_dxf(output.getvalue()).decode("ascii").replace("\r\n", "\n")

        self.assertEqual(dxf.count("0\nPOLYLINE\n"), 1)

    def test_generated_dxf_rejects_an_image_without_a_subject(self):
        with self.assertRaisesRegex(app_module.WorkflowError, "没有可导出的最外层轮廓"):
            app_module._image_dxf(sample_png())

    def test_generated_dxf_smooths_curved_outer_outline(self):
        canvas = np.full((128, 128, 3), 255, dtype=np.uint8)
        cv2.circle(canvas, (64, 64), 38, (20, 20, 20), -1)
        ok, encoded = cv2.imencode(".png", canvas)
        self.assertTrue(ok)

        raw = app_module._image_dxf(encoded.tobytes())
        points = self.dxf_vertices(raw)
        center = points.mean(axis=0)
        radii = np.linalg.norm(points - center, axis=1)

        self.assertGreater(len(points), 32)
        self.assertLess(len(points), 4000)
        self.assertLess(float(radii.std()), 0.24)

    def test_generated_dxf_preserves_sharp_outer_tip(self):
        canvas = np.full((128, 128, 3), 255, dtype=np.uint8)
        polygon = np.asarray([[64, 10], [112, 108], [16, 108]], dtype=np.int32)
        cv2.fillPoly(canvas, [polygon], (20, 20, 20))
        ok, encoded = cv2.imencode(".png", canvas)
        self.assertTrue(ok)

        points = self.dxf_vertices(app_module._image_dxf(encoded.tobytes()))

        self.assertLess(abs(float(points[:, 0].max()) - 87.5), 0.6)
        self.assertGreater(float(points[:, 1].max()), 91.5)


class TextImageReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("LOCAL_DATA_DIR")
        self.previous_admin_username = os.environ.get("ADMIN_USERNAME")
        self.previous_admin_password = os.environ.get("ADMIN_PASSWORD")
        os.environ["LOCAL_DATA_DIR"] = self.tempdir.name
        os.environ["ADMIN_USERNAME"] = "test-admin"
        os.environ["ADMIN_PASSWORD"] = "test-admin-password"
        self.client = TestClient(app_module.app)
        response = self.client.post(
            "/api/auth/login",
            json={"username": "test-admin", "password": "test-admin-password"},
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.client.close()
        if self.previous_data_dir is None:
            os.environ.pop("LOCAL_DATA_DIR", None)
        else:
            os.environ["LOCAL_DATA_DIR"] = self.previous_data_dir
        for name, value in (
            ("ADMIN_USERNAME", self.previous_admin_username),
            ("ADMIN_PASSWORD", self.previous_admin_password),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
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

    def test_visual_studio_task_keeps_its_purpose(self):
        response = self.client.post("/api/text-image/tasks", json={
            "prompts": ["产品放在现代庭院的石材展台上"],
            "purpose": "visual_studio",
        })

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["purpose"], "visual_studio")
        task = self.client.get(
            f"/api/text-image/tasks/{body['task_id']}",
            headers={"X-Session-Token": body["access_token"]},
        )
        self.assertEqual(task.status_code, 200)
        self.assertEqual(task.json()["purpose"], "visual_studio")

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
        self.assertTrue(svg_url.endswith(f"/1-{app_module.COLOR_SVG_TRACE_VERSION}.svg"))
        svg_response = self.client.get(svg_url)
        self.assertEqual(svg_response.status_code, 200)
        self.assertEqual(svg_response.headers["content-type"], "image/svg+xml")
        self.assertIn(b'width="128px" height="128px" viewBox="0 0 128 128"', svg_response.content)
        self.assertIn(b'version="1.1"', svg_response.content)
        self.assertIn(b'<path id="region-0" class="vector-region"', svg_response.content)
        self.assertIn(b'.vector-region:hover', svg_response.content)
        self.assertRegex(svg_response.content, rb'fill="#[0-9a-f]{6}" stroke="#[0-9a-f]{6}"')
        self.assertNotIn(b'<image', svg_response.content)
        self.assertNotIn(b'data:image/', svg_response.content)
        self.assertNotIn(b'fill="none"', svg_response.content)
        self.assertNotIn(b'fill="transparent"', svg_response.content)
        png_download = self.client.get(
            f"/api/text-image/tasks/{task_id}/items/0/download/png", headers=headers,
        )
        self.assertEqual(png_download.status_code, 200)
        self.assertEqual(png_download.content, generated)
        self.assertEqual(png_download.headers["content-type"], "image/png")
        self.assertIn("attachment", png_download.headers["content-disposition"])
        self.assertIn("lingjie-image-1.png", png_download.headers["content-disposition"])
        svg_download = self.client.get(
            f"/api/text-image/tasks/{task_id}/items/0/download/svg", headers=headers,
        )
        self.assertEqual(svg_download.status_code, 200)
        self.assertEqual(svg_download.content, svg_response.content)
        self.assertEqual(svg_download.headers["content-type"], "image/svg+xml")
        self.assertIn("lingjie-image-1.svg", svg_download.headers["content-disposition"])
        native_svg_download = self.client.post(
            f"/api/text-image/tasks/{task_id}/items/0/download/svg",
            data={"session_token": headers["X-Session-Token"]},
        )
        self.assertEqual(native_svg_download.status_code, 200)
        self.assertEqual(native_svg_download.content, svg_response.content)
        self.assertIn("attachment", native_svg_download.headers["content-disposition"])
        dxf_download = self.client.get(
            f"/api/text-image/tasks/{task_id}/items/0/download/dxf", headers=headers,
        )
        self.assertEqual(dxf_download.status_code, 200)
        self.assertEqual(dxf_download.headers["content-type"], "application/dxf")
        self.assertIn(b"POLYLINE", dxf_download.content)
        self.assertIn(b"OUTLINE", dxf_download.content)
        self.assertNotIn(b"COLOR_", dxf_download.content)
        self.assertIn("lingjie-image-1.dxf", dxf_download.headers["content-disposition"])
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

    def test_legacy_color_svg_is_upgraded_on_its_next_download(self):
        task_id, headers = self.create_task()
        generated = sample_png((90, 140, 210))
        with patch.object(app_module, "generate_image", return_value=(generated, {})):
            response = self.client.post(
                f"/api/text-image/tasks/{task_id}/items/0/generate", headers=headers,
            )
        self.assertEqual(response.status_code, 200)

        token = headers["X-Session-Token"]
        state, version = app_module.load_session(task_id, token)
        state["items"][0].pop("svg_trace_version", None)
        app_module.save_session(task_id, state, version)

        with patch.object(
            app_module, "_image_svg", wraps=app_module._image_svg,
        ) as rebuild:
            upgraded = self.client.post(
                f"/api/text-image/tasks/{task_id}/items/0/download/svg",
                data={"session_token": token},
            )

        self.assertEqual(upgraded.status_code, 200)
        self.assertEqual(rebuild.call_count, 1)
        updated, _ = app_module.load_session(task_id, token)
        item = updated["items"][0]
        self.assertEqual(item["svg_trace_version"], app_module.COLOR_SVG_TRACE_VERSION)
        self.assertTrue(
            item["svg_url"].endswith(f"/1-{app_module.COLOR_SVG_TRACE_VERSION}.svg")
        )

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
