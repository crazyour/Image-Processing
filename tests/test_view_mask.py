from __future__ import annotations

import base64
import io
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import app as app_module
import test_multiview_quality as multiview_tests
from vercel_app import workflow
from vercel_app.store import load_session, save_session
from vercel_app.view_mask import validate_view_mask, prepare_masked_view, composite_view_edit
from vercel_app.workflow import WorkflowError


def encode(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()


def mask_url(size=(128, 128), mode="RGBA", empty=False):
    image = Image.new(mode, size, "black")
    if mode == "RGBA" and not empty:
        ImageDraw.Draw(image).rectangle((20, 30, 60, 80), fill=(255, 0, 0, 0))
    return "data:image/png;base64," + base64.b64encode(encode(image)).decode()


class ViewMaskTests(unittest.TestCase):
    def test_portrait_and_landscape_masks_keep_exact_coordinates_after_square_padding(self):
        for size in [(173, 320), (320, 173)]:
            with self.subTest(size=size):
                source = encode(Image.new("RGBA", size, (10, 20, 30, 180)))
                mask = validate_view_mask(mask_url(size), source)
                padded_source, padded_mask = prepare_masked_view(source, mask)
                frame = Image.open(io.BytesIO(padded_source))
                selection = Image.open(io.BytesIO(padded_mask))
                edge = max(size)
                left, top = (edge - size[0]) // 2, (edge - size[1]) // 2
                self.assertEqual(frame.size, (edge, edge))
                self.assertEqual(selection.size, frame.size)
                self.assertEqual(selection.getpixel((0, 0))[3], 255)
                self.assertEqual(selection.getpixel((left + 20, top + 30))[3], 0)
                self.assertEqual(frame.getpixel((left + 20, top + 30)), (10, 20, 30, 180))
                result = Image.open(io.BytesIO(composite_view_edit(source, padded_source, mask, padded=True)))
                np.testing.assert_array_equal(np.array(result), np.array(Image.open(io.BytesIO(source))))
                red = encode(Image.new("RGBA", (1024, 1024), (255, 0, 0, 255)))
                result = Image.open(io.BytesIO(composite_view_edit(source, red, mask, padded=True)))
                self.assertEqual(result.size, size)
                self.assertEqual(result.getpixel((20, 30)), (255, 0, 0, 255))
                self.assertEqual(result.getpixel((19, 30)), (10, 20, 30, 180))
                self.assertEqual(result.getpixel((60, 80)), (255, 0, 0, 255))
                self.assertEqual(result.getpixel((61, 80)), (10, 20, 30, 180))

    def test_composite_preserves_every_unselected_pixel_including_alpha(self):
        rng = np.random.default_rng(42)
        pixels = rng.integers(0, 256, (128, 128, 4), dtype=np.uint8)
        source = encode(Image.fromarray(pixels))
        mask = validate_view_mask(mask_url(), source)
        generated = encode(Image.new("RGBA", (256, 256), (80, 40, 20, 255)))
        result = np.array(Image.open(io.BytesIO(composite_view_edit(source, generated, mask))))
        selected = np.array(Image.open(io.BytesIO(mask)))[:, :, 3] == 0
        np.testing.assert_array_equal(result[~selected], pixels[~selected])
        self.assertTrue(np.all(result[selected] == [80, 40, 20, 255]))

    def test_empty_malformed_wrong_dimensions_and_no_alpha_rejected(self):
        source = multiview_tests.png()
        for invalid in [mask_url(empty=True), mask_url((64, 128)), mask_url(mode="RGB"),
                        "data:image/jpeg;base64,AA==", "data:image/png;base64,broken!",
                        "data:image/png;base64," + "A" * 4_000_000]:
            with self.subTest(prefix=invalid[:30]), self.assertRaises(WorkflowError):
                validate_view_mask(invalid, source)
        with self.assertRaises(WorkflowError) as error:
            composite_view_edit(source, encode(Image.new("RGB", (128, 64))),
                                validate_view_mask(mask_url(), source))
        self.assertEqual(error.exception.code, "EDIT_SIZE_MISMATCH")

    def test_provider_payload_transmits_mask_as_separate_png_without_reordering(self):
        source = multiview_tests.png()
        mask = validate_view_mask(mask_url(), source)
        with patch.object(workflow, "_openai_config") as config, patch.object(workflow.httpx, "Client") as client:
            config.return_value.base_url = "https://example.test/v1"
            config.return_value.api_key = "test-key"
            response = client.return_value.__enter__.return_value.post.return_value
            response.status_code = 200
            response.json.return_value = {"data": [{"b64_json": base64.b64encode(source).decode()}]}
            workflow.generate_view([source, multiview_tests.png(1)], "局部修复", "gpt-image-2.5-flare", "medium", mask=mask)
            payload = client.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(base64.b64decode(payload["images"][0]["image_url"].split(",")[1]), source)
        self.assertEqual(base64.b64decode(payload["mask"]["image_url"].split(",")[1]), mask)
        self.assertEqual(payload["model"], "gpt-image-2.5-flare")
        self.assertEqual(payload["quality"], "medium")
        self.assertEqual(payload["size"], "1024x1024")


class ViewMaskWorkflowTests(unittest.TestCase):
    setUp = multiview_tests.MultiviewWorkflowTests.setUp
    post = multiview_tests.MultiviewWorkflowTests.post
    generate = multiview_tests.MultiviewWorkflowTests.generate

    def upload_single_view(self, size=(256, 128)):
        created = self.client.post("/api/sessions", files={"image": (
            "left.png", encode(Image.new("RGB", size, "blue")), "image/png")},
            data={"view_source": "uploaded", "initial_view": "left"}).json()
        self.session_id, self.token = created["session_id"], created["access_token"]
        self.path = f"/api/sessions/{self.session_id}"
        self.headers = {"X-Session-Token": self.token}
        self.client.patch(self.path + "/model-views", headers=self.headers,
                          json={"excluded_views": ["front", "right", "back"]})
        return self.client.get(self.path, headers=self.headers).json()

    def test_single_uploaded_view_can_be_edited_from_all_editable_states_and_reconfirmed(self):
        for status in ["VIEWS_REVIEW", "VIEWS_CONFIRMED", "CONFIRMED", "FAILED"]:
            with self.subTest(status=status):
                before = self.upload_single_view()
                self.assertEqual(self.post("/confirm").status_code, 200)
                state, version = load_session(self.session_id, self.token)
                state["status"] = status
                save_session(self.session_id, state, version)
                generated = encode(Image.new("RGB", (1024, 1024), "red"))
                with patch.object(app_module, "generate_view", return_value=(generated, {})) as provider:
                    response = self.post("/views/left/refine", json=self.request_data(before, mask_data_url=mask_url((256, 128))))
                self.assertEqual(response.status_code, 200, response.text)
                args = provider.call_args.args
                self.assertEqual(args[2:4], ("gpt-image-2.5-flare", "medium"))
                self.assertEqual(len(args[0]), 1)  # No AI analysis or unrelated original/other-angle references.
                self.assertEqual(Image.open(io.BytesIO(args[0][0])).size, (256, 256))
                after = response.json()
                self.assertEqual(after["view_source"], "uploaded")
                self.assertEqual(set(after["views"]), {"left"})
                self.assertEqual(after["excluded_3d_views"], before["excluded_3d_views"])
                self.assertIsNone(after["confirmed_at"])
                self.assertIsNone(after["description_confirmed_at"])
                self.assertTrue(after["views"]["left"]["uploaded"])
                result = Image.open(io.BytesIO(app_module.fetch_bytes(after["views"]["left"]["url"])))
                self.assertEqual(result.size, (256, 128))
                self.assertEqual(result.getpixel((30, 40)), (255, 0, 0, 255))
                self.assertEqual(result.getpixel((100, 100)), (0, 0, 255, 255))
                self.assertEqual(self.post("/confirm").status_code, 200)

    def test_replaced_upload_is_the_only_edit_source_and_old_masks_are_rejected(self):
        before = self.upload_single_view((128, 256))
        replacement = encode(Image.new("RGB", (128, 256), "green"))
        response = self.post("/views/left/upload", files={"image": ("new.png", replacement, "image/png")})
        current = response.json()
        with patch.object(app_module, "_metered_provider_call") as meter:
            self.assertEqual(self.post("/views/left/refine", json=self.request_data(before, mask_data_url=mask_url((128, 256)))).status_code, 409)
            meter.assert_not_called()
        with patch.object(app_module, "generate_view", side_effect=lambda refs, prompt, model, quality, **kw: (refs[0], {})) as provider:
            response = self.post("/views/left/refine", json=self.request_data(current, mask_data_url=mask_url((128, 256))))
        self.assertEqual(response.status_code, 200, response.text)
        refs = provider.call_args.args[0]
        self.assertEqual(len(refs), 1)
        self.assertEqual(Image.open(io.BytesIO(refs[0])).getpixel((64, 40)), (0, 128, 0, 255))
        result = app_module.fetch_bytes(response.json()["views"]["left"]["url"])
        np.testing.assert_array_equal(np.array(Image.open(io.BytesIO(result))),
                                      np.array(Image.open(io.BytesIO(replacement)).convert("RGBA")))

    def test_uploaded_failures_preserve_original_and_locked_tasks_never_bill(self):
        before = self.upload_single_view()
        payload = self.request_data(before, mask_data_url=mask_url((256, 128)))
        with patch.object(app_module, "generate_view", side_effect=WorkflowError("OPENAI_ERROR", "failed", 502)):
            self.assertEqual(self.post("/views/left/refine", json=payload).status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(state["views"], before["views"])
        with patch.object(app_module, "_metered_provider_call") as meter:
            self.assertEqual(self.post("/views/left/refine", json={"prompt": "修改"}).status_code, 422)
            for status in ["GENERATING_VIEWS", "REVIEWING_VIEWS", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"]:
                state, version = load_session(self.session_id, self.token)
                state["status"] = status
                save_session(self.session_id, state, version)
                self.assertEqual(self.post("/views/left/refine", json=payload).status_code, 409)
            meter.assert_not_called()

    def test_existing_session_keeps_its_saved_model_for_mask_edits(self):
        before = self.upload_single_view((128, 128))
        state, version = load_session(self.session_id, self.token)
        state["model_settings"]["image_model"] = "gpt-image-2.5-flare"
        state["model_settings"]["image_quality"] = "medium"
        state["model_settings"].pop("texture_model", None)
        state["model_settings"].pop("texture_quality", None)
        save_session(self.session_id, state, version)
        with patch.object(app_module, "generate_view", return_value=(multiview_tests.png(), {})) as provider:
            response = self.post("/views/left/refine", json=self.request_data(before))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(provider.call_args.args[2:4], ("gpt-image-2.5-flare", "medium"))

    def request_data(self, state, **changes):
        item = state["views"]["left"]
        return {"prompt": "删除多余装饰并补全表面", "mask_data_url": mask_url(),
                "source_revision": item["revision"], "source_sha256": item["sha256"], **changes}

    def test_only_target_changes_and_exclusion_is_preserved(self):
        before = self.generate()
        state, version = load_session(self.session_id, self.token)
        state["model_settings"].update(image_model="gpt-image-2.6-sunburst", image_quality="high")
        state["excluded_3d_views"] = ["left"]
        state["confirmed_snapshot"] = {"old": True}
        save_session(self.session_id, state, version)
        generated = encode(Image.new("RGB", (128, 128), "red"))
        with patch.object(app_module, "generate_view", return_value=(generated, {})) as provider:
            response = self.post("/views/left/refine", json=self.request_data(before))
        self.assertEqual(response.status_code, 200, response.text)
        args, kwargs = provider.call_args
        self.assertEqual(args[2:4], ("gpt-image-2.5-flare", "medium"))
        self.assertEqual(args[0][0], multiview_tests.png(1))
        self.assertEqual(args[0][-1], multiview_tests.png(0))
        self.assertIn("第一张图片（当前左视图）", args[1])
        self.assertIn("第 3 张是已生成的正视图", args[1])
        self.assertIn("删除多余装饰", args[1])
        self.assertIsNotNone(kwargs["mask"])
        after = response.json()
        self.assertEqual(after["excluded_3d_views"], ["left"])
        stored, _ = load_session(self.session_id, self.token)
        self.assertEqual(stored["model_settings"]["image_model"], "gpt-image-2.5-flare")
        self.assertEqual(stored["model_settings"]["image_quality"], "medium")
        for view in ["front", "right", "back"]:
            self.assertEqual(after["views"][view], before["views"][view])
        self.assertEqual(after["views"]["left"]["revision"], before["views"]["left"]["revision"] + 1)
        self.assertEqual(after["views"]["left"]["refinement_kind"], "masked")
        self.assertIsNone(after["confirmed_at"])
        result = np.array(Image.open(io.BytesIO(app_module.fetch_bytes(after["views"]["left"]["url"]))))
        original = np.array(Image.open(io.BytesIO(multiview_tests.png(1))).convert("RGBA"))
        selected = np.array(Image.open(io.BytesIO(kwargs["mask"])))[:, :, 3] == 0
        np.testing.assert_array_equal(result[~selected], original[~selected])
        self.assertTrue(np.all(result[selected] == [255, 0, 0, 255]))
        self.assertEqual(self.post("/confirm").status_code, 200)

    def test_preflight_rejects_invalid_masks_and_old_images_before_billing_or_mutation(self):
        before = self.generate()
        for changes, expected in [({"mask_data_url": mask_url(empty=True)}, 422),
                                  ({"mask_data_url": mask_url((64, 128))}, 422),
                                  ({"source_revision": 99}, 409), ({"source_sha256": "0" * 64}, 409),
                                  ({"source_revision": None}, 409), ({"prompt": "  "}, 422)]:
            with self.subTest(changes=changes), patch.object(app_module, "_metered_provider_call") as meter:
                response = self.post("/views/left/refine", json=self.request_data(before, **changes))
                self.assertEqual(response.status_code, expected, response.text)
                meter.assert_not_called()
                after, _ = load_session(self.session_id, self.token)
                self.assertEqual(after["status"], "VIEWS_REVIEW")
                self.assertEqual(after["views"], before["views"])
        state, version = load_session(self.session_id, self.token)
        state["prompt_revision"] += 1
        save_session(self.session_id, state, version)
        with patch.object(app_module, "_metered_provider_call") as meter:
            self.assertEqual(self.post("/views/left/refine", json=self.request_data(before)).status_code, 409)
            meter.assert_not_called()

    def test_provider_or_storage_failure_restores_original_and_allows_mask_retry(self):
        before = self.generate()
        with patch.object(app_module, "generate_view", side_effect=WorkflowError("OPENAI_ERROR", "failed", 502)):
            response = self.post("/views/left/refine", json=self.request_data(before))
        self.assertEqual(response.status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(state["status"], "VIEWS_REVIEW")
        self.assertEqual(state["views"], before["views"])
        with patch.object(app_module, "generate_view", return_value=(multiview_tests.png(3), {})), \
                patch.object(app_module.PublicBlobStore, "put", side_effect=RuntimeError("storage failure")):
            self.assertEqual(self.post("/views/left/refine", json=self.request_data(before)).status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(state["views"], before["views"])
        with patch.object(app_module, "generate_view", return_value=(multiview_tests.png(3), {})):
            self.assertEqual(self.post("/views/left/refine", json=self.request_data(before)).status_code, 200)

    def test_edit_image_is_authenticated_and_rejects_old_revisions(self):
        before = self.generate()
        path = self.path + "/views/left/edit-image"
        response = self.client.get(path + "?revision=1", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, multiview_tests.png(1))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get(path + "?revision=99", headers=self.headers).status_code, 409)
        self.assertIn(self.client.get(path + "?revision=1", headers={"X-Session-Token": "wrong"}).status_code, (401, 403, 404))
        with patch.object(app_module, "_metered_provider_call") as meter:
            self.assertIn(self.client.post(self.path + "/views/left/refine", json=self.request_data(before),
                                          headers={"X-Session-Token": "wrong"}).status_code, (401, 403, 404))
            meter.assert_not_called()
        state, version = load_session(self.session_id, self.token)
        state["status"] = "GENERATING_3D"
        save_session(self.session_id, state, version)
        with patch.object(app_module, "_metered_provider_call") as meter:
            self.assertEqual(self.post("/views/left/refine", json=self.request_data(before)).status_code, 409)
            meter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
