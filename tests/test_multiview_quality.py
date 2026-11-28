from __future__ import annotations

import base64
import copy
import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import app as app_module
from vercel_app.multiview import (
    current_review, local_view_checks, normalize_review, review_signature, review_views,
)
from vercel_app.store import load_session, save_session
from vercel_app.workflow import (VIEW_TYPES, VIEW_NAMES, WorkflowError, view_prompt,
                                 CONSISTENCY_CONSTRAINT, PRINT_SCULPTURE_CONSTRAINT, effective_prompt)


def png(index=0, *, large=False, compress_level=6):
    image = Image.new("RGB", (1024, 768) if large else (128, 128), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10 + index * 9, 20, 45 + index * 12, 100), fill=(20, 50 + index * 20, 80))
    draw.ellipse((55, 10 + index * 8, 90, 48 + index * 8), fill=(180, 45, 60))
    output = io.BytesIO()
    image.save(output, "PNG", compress_level=compress_level)
    return output.getvalue()


def accepted_result():
    return {view: {
        "actual_view": view, "confidence": 0.95, "same_subject": True,
        "orthographic": True, "symmetric": False, "duplicate_of": "", "issue": "",
    } for view in VIEW_TYPES}


class MultiviewChecksTests(unittest.TestCase):
    def test_faithful_mode_preserves_existing_prompt(self):
        expected = f"主体要求：\n彩色角色\n\n跨视角固定约束：\n{CONSISTENCY_CONSTRAINT}"
        self.assertEqual(effective_prompt("彩色角色"), expected)
        self.assertEqual(effective_prompt("彩色角色", "faithful"), expected)

    def test_sculpture_mode_preserves_color_and_limits_geometry_changes(self):
        for view in VIEW_TYPES:
            prompt = view_prompt("彩色角色", view, view_mode="print_sculpture")
            self.assertIn(PRINT_SCULPTURE_CONSTRAINT, prompt)
            self.assertIn("保留原图配色、材质表现", prompt)
            self.assertIn("不额外改成黑白", prompt)
            self.assertIn("不能粘合手指", prompt)
            self.assertNotIn("不改变主体几何结构", prompt)
            # Keep the added instructions short; no extra reference images.
            self.assertLess(len(prompt) - len(view_prompt("彩色角色", view)), 160)

    def test_mode_is_part_of_review_cache_identity(self):
        state = {"prompt_revision": 1, "views": {}}
        previous = review_signature(state)
        state["view_mode"] = "faithful"
        self.assertEqual(review_signature(state), previous)
        state["view_mode"] = "print_sculpture"
        self.assertNotEqual(review_signature(state), previous)

    @patch("vercel_app.multiview._openai_config", return_value=Mock(base_url="https://example.invalid", api_key="test"))
    @patch("vercel_app.multiview.httpx.Client")
    def test_sculpture_review_allows_only_intended_adjustments_in_same_call(self, client, config):
        post = client.return_value.__enter__.return_value.post
        post.return_value = httpx.Response(200, json={"output": [{"content": [{"type": "output_text", "text": json.dumps({
            "views": [{"view": view, **item} for view, item in accepted_result().items()],
        })}]}]})
        review_views({view: png(i) for i, view in enumerate(VIEW_TYPES)}, png(), "test-model",
                     {"similar_pairs": []}, view_mode="print_sculpture")
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_output_tokens"], 900)
        content = payload["input"][0]["content"]
        self.assertEqual(len(content), 6)
        self.assertIn("不因此判为主体漂移", content[0]["text"])
        self.assertIn("仍须保留原配色", content[0]["text"])
        self.assertIn("不判断真实壁厚", content[0]["text"])

    def test_detects_same_pixels_despite_different_png_encoding(self):
        a, b = png(compress_level=0), png(compress_level=9)
        self.assertNotEqual(a, b)
        checks = local_view_checks({"left": a, "right": b, "front": png(2)})
        self.assertEqual(checks["exact_duplicates"], [["left", "right"]])

    def test_near_duplicates_are_candidates_not_automatic_failures(self):
        original = png()
        with Image.open(io.BytesIO(original)) as image:
            image.putpixel((22, 22), (21, 51, 81))
            output = io.BytesIO()
            image.save(output, "PNG")
        checks = local_view_checks({"left": original, "right": output.getvalue()})
        self.assertEqual(checks["exact_duplicates"], [])
        self.assertEqual(checks["similar_pairs"], [["left", "right"]])

    def test_wrong_angle_and_semantic_duplicate_are_rejected(self):
        result = accepted_result()
        result["right"].update(actual_view="left", duplicate_of="left", issue="将相机移到另一侧")
        result["back"].update(actual_view="oblique", orthographic=False)
        normalized = normalize_review(result)
        self.assertEqual(normalized["failed_views"], ["right", "back"])
        self.assertEqual(normalized["views"]["front"]["status"], "passed")

    def test_symmetry_does_not_force_unnecessary_regeneration(self):
        result = accepted_result()
        result["left"].update(actual_view="unknown", confidence=0.4, symmetric=True, duplicate_of="right")
        self.assertEqual(normalize_review(result)["status"], "passed")
        result["left"]["orthographic"] = False
        self.assertEqual(normalize_review(result)["failed_views"], ["left"])

    def test_uncertainty_and_malformed_reviews_cannot_pass(self):
        result = accepted_result()
        result["left"].update(actual_view="unknown", confidence=0.4)
        self.assertEqual(normalize_review(result)["failed_views"], ["left"])
        for mutation in (lambda r: r.pop("back"),
                         lambda r: r["left"].update(confidence=float("nan")),
                         lambda r: r["left"].update(same_subject="true")):
            broken = accepted_result()
            mutation(broken)
            with self.assertRaises(ValueError):
                normalize_review(broken)

    def test_review_signature_invalidates_on_prompt_or_image_revision(self):
        state = {"prompt_revision": 1, "views": {
            view: {"sha256": view, "revision": 1, "prompt_revision": 1} for view in VIEW_TYPES
        }}
        state["view_review"] = {"status": "passed", "signature": review_signature(state)}
        self.assertIsNotNone(current_review(state))
        for modify in (lambda s: s.update(prompt_revision=2),
                       lambda s: s["views"]["front"].update(revision=2),
                       lambda s: s["views"]["right"].update(sha256="changed")):
            changed = copy.deepcopy(state)
            modify(changed)
            self.assertIsNone(current_review(changed))

    def test_prompt_uses_source_angle_and_opposing_side_directions(self):
        left = view_prompt("雕塑", "left", source_view="FRONT_THREE_QUARTER")
        right = view_prompt("雕塑", "right", source_view="FRONT_THREE_QUARTER")
        self.assertIn("主图视角为斜前方", left)
        self.assertIn("前方指向画面右侧", left)
        self.assertIn("前方指向画面左侧", right)
        self.assertIn("禁止3/4斜视", left)
        self.assertIn("最后一张是待修正", view_prompt("雕塑", "left", editing_current=True))

    def test_prompt_labels_originals_previous_angles_and_refinement_target(self):
        prompt = view_prompt("雕塑", "right", reference_count=2,
                             prior_views=("front", "left"), editing_current=True)
        self.assertIn("前2张是原始主体证据", prompt)
        self.assertIn("第3张是已生成的正视图", prompt)
        self.assertIn("第4张是已生成的左视图", prompt)
        self.assertIn("原始证据、主体要求和造型模式优先", prompt)
        self.assertIn("最后一张是待修正的右视图", prompt)

    @patch("vercel_app.multiview._openai_config", return_value=Mock(base_url="https://example.invalid", api_key="test"))
    @patch("vercel_app.multiview.httpx.Client")
    def test_review_uses_one_low_detail_thumbnail_batch_and_bounded_output(self, client, config):
        response = httpx.Response(200, json={
            "output": [{"content": [{"type": "output_text", "text": json.dumps({
                "views": [{"view": view, **item} for view, item in accepted_result().items()],
            })}]}],
            "usage": {"input_tokens": 700, "output_tokens": 350},
        })
        post = client.return_value.__enter__.return_value.post
        post.return_value = response
        result, usage = review_views({view: png(i, large=True) for i, view in enumerate(VIEW_TYPES)},
                                     png(large=True), "gpt-6-luna", {"similar_pairs": []})
        self.assertEqual(result["status"], "passed")
        self.assertEqual(usage["output_tokens"], 350)
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_output_tokens"], 900)
        images = payload["input"][0]["content"][1:]
        self.assertEqual(len(images), 5)
        for item in images:
            self.assertEqual(item["detail"], "low")
            with Image.open(io.BytesIO(base64.b64decode(item["image_url"].split(",")[1]))) as image:
                self.assertLessEqual(max(image.size), 512)


class MultiviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        environment = {
            "LOCAL_DATA_DIR": self.tempdir.name, "ADMIN_USERNAME": "multiview-admin",
            "ADMIN_PASSWORD": "multiview-password", "ADMIN_NAME": "测试管理员",
            **{name: "" for name in ("DATABASE_URL", "NEON_DATABASE_URL", "POSTGRES_URL",
                                     "DATABASE_URL_POOLED", "BLOB_READ_WRITE_TOKEN", "VERCEL")},
        }
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)
        login = self.client.post("/api/auth/login", json={"username": "multiview-admin", "password": "multiview-password"})
        self.assertEqual(login.status_code, 200, login.text)
        # No provider traffic or billing during these state-transition tests.
        self.meter = patch.object(app_module, "_metered_provider_call", side_effect=lambda u, f, m, a, call: call())
        self.meter.start()
        self.addCleanup(self.meter.stop)
        created = self.client.post("/api/sessions", files={"image": ("source.png", png(4), "image/png")},
                                   data={"view_source": "generated"})
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.session_id, self.token = body["session_id"], body["access_token"]
        self.path = f"/api/sessions/{self.session_id}"
        self.headers = {"X-Session-Token": self.token}
        state, version = load_session(self.session_id, self.token)
        state.update(status="DESCRIPTION_CONFIRMED", user_prompt="白色雕塑", description_confirmed_at=1,
                     analysis={"source_view": "FRONT_THREE_QUARTER"})
        save_session(self.session_id, state, version)

    def post(self, suffix, **kwargs):
        return self.client.post(self.path + suffix, headers=self.headers, **kwargs)

    def change_mode(self, mode):
        state, _ = load_session(self.session_id, self.token)
        return self.client.patch(self.path + "/prompt", headers=self.headers,
                                 json={"prompt": state["user_prompt"], "view_mode": mode})

    def test_mode_change_invalidates_old_views_without_paid_calls(self):
        before = self.generate()
        self.accepted_review()
        self.post("/confirm")
        with patch.object(app_module, "generate_view") as generate, patch.object(app_module, "review_views") as review:
            response = self.change_mode("print_sculpture")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["view_mode"], "print_sculpture")
        self.assertEqual(body["status"], "PROMPT_REVIEW")
        self.assertEqual(body["prompt_revision"], before["prompt_revision"] + 1)
        self.assertEqual(body["views"], before["views"])
        self.assertIsNone(body["view_review"])
        self.assertIsNone(body["confirmed_at"])
        self.assertFalse(body["views_approved"])
        generate.assert_not_called()
        review.assert_not_called()
        self.assertEqual(self.post("/confirm").status_code, 409)
        self.assertEqual(self.post("/generate", json={"model_provider": "meshy"}).status_code, 409)
        self.assertEqual(self.post("/views", json={"views": list(VIEW_TYPES)}).status_code, 409)

    def test_sculpture_generation_refinement_and_confirmation_keep_mode(self):
        self.change_mode("print_sculpture")
        self.post("/description/confirm")
        with patch.object(app_module, "generate_view", return_value=(png(4), {})) as generate:
            response = self.post("/views", json={"views": list(VIEW_TYPES)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_count, 4)
        for count, call in enumerate(generate.call_args_list, start=1):
            self.assertEqual(len(call.args[0]), count)
            self.assertIn(PRINT_SCULPTURE_CONSTRAINT, call.args[1])
            self.assertEqual(call.args[3], "medium")
        self.assertTrue(all(v["view_mode"] == "print_sculpture" for v in response.json()["views"].values()))
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as refine:
            response = self.post("/views/left/refine", json={"prompt": "减少发束交叉"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(refine.call_args.args[0]), 3)
        self.assertIn(PRINT_SCULPTURE_CONSTRAINT, refine.call_args.args[1])
        # Unique fixtures let the existing single batch reviewer run.
        self.generate()
        with patch.object(app_module, "review_views", return_value=(normalize_review(accepted_result()), {})) as review:
            self.assertEqual(self.post("/views/review").status_code, 200)
            self.assertEqual(review.call_args.kwargs["view_mode"], "print_sculpture")
        self.assertEqual(self.post("/confirm").status_code, 200)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(state["confirmed_snapshot"]["view_mode"], "print_sculpture")

    def test_mode_noop_and_omitted_field_preserve_selection(self):
        initial, _ = load_session(self.session_id, self.token)
        self.assertEqual(self.change_mode("faithful").json()["prompt_revision"], initial["prompt_revision"])
        changed = self.change_mode("print_sculpture").json()
        self.assertEqual(self.change_mode("print_sculpture").json()["prompt_revision"], changed["prompt_revision"])
        response = self.client.patch(self.path + "/prompt", headers=self.headers, json={"prompt": "保留红色衣服"})
        self.assertEqual(response.json()["view_mode"], "print_sculpture")
        self.assertIn(PRINT_SCULPTURE_CONSTRAINT, response.json()["effective_prompt"])

    def test_mode_cannot_change_uploaded_or_locked_sessions(self):
        self.assertEqual(self.change_mode("unknown-mode").status_code, 422)
        for status in ["GENERATING_VIEWS", "REVIEWING_VIEWS", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"]:
            state, version = load_session(self.session_id, self.token)
            state["status"] = status
            save_session(self.session_id, state, version)
            self.assertEqual(self.change_mode("print_sculpture").status_code, 409, status)
        state, version = load_session(self.session_id, self.token)
        state.update(status="VIEWS_REVIEW", view_source="uploaded")
        save_session(self.session_id, state, version)
        self.assertEqual(self.change_mode("print_sculpture").status_code, 409)

    def test_print_mode_cannot_accept_faithful_images_with_matching_revision(self):
        self.generate()
        state, version = load_session(self.session_id, self.token)
        state["view_mode"] = "print_sculpture"
        save_session(self.session_id, state, version)
        self.assertFalse(app_module._views_current(state))
        self.assertEqual(self.post("/confirm").status_code, 409)

    def generate(self, *, duplicate=False):
        def make_image(references, prompt, model, quality):
            target = next(view for view in VIEW_TYPES if f"一张{VIEW_NAMES[view]}" in prompt)
            self.assertEqual(len(references), 1 + VIEW_TYPES.index(target))
            self.assertEqual(references[1:], [png(0 if duplicate else i) for i in range(VIEW_TYPES.index(target))])
            self.assertEqual(model, "gpt-image-2.5-flare")
            self.assertEqual(quality, "medium")
            return png(0 if duplicate else VIEW_TYPES.index(target)), {"calls": 1}
        with patch.object(app_module, "generate_view", side_effect=make_image) as generate:
            response = self.post("/views", json={"views": list(VIEW_TYPES)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_count, 4)
        return response.json()

    def accepted_review(self):
        with patch.object(app_module, "review_views", return_value=(normalize_review(accepted_result()), {})):
            response = self.post("/views/review")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_batch_is_ordered_and_each_result_is_saved_before_next_call(self):
        original, _ = load_session(self.session_id, self.token)
        source = app_module.fetch_bytes(original["reference_images"][0]["url"])
        seen = []

        def produce(references, prompt, model, quality):
            target = next(view for view in VIEW_TYPES if f"一张{VIEW_NAMES[view]}" in prompt)
            persisted, _ = load_session(self.session_id, self.token)
            self.assertEqual(list(persisted["views"]), seen)
            self.assertEqual(references, [source, *[png(VIEW_TYPES.index(view)) for view in seen]])
            seen.append(target)
            return png(VIEW_TYPES.index(target)), {}

        with patch.object(app_module, "generate_view", side_effect=produce):
            response = self.post("/views", json={"views": ["back", "right", "front", "left"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(seen, list(VIEW_TYPES))
        self.assertEqual(response.json()["pending_views"], [])

    def test_single_step_invalidates_remaining_old_views_and_resumes_with_new_images(self):
        before = self.generate()["views"]
        self.accepted_review()
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            response = self.post("/views", json={"views": list(VIEW_TYPES), "single_step": True})
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once()
        self.assertEqual(len(generate.call_args.args[0]), 1)
        body = response.json()
        self.assertEqual(body["pending_views"], ["left", "right", "back"])
        for view in body["pending_views"]:
            self.assertEqual(body["views"][view]["url"], before[view]["url"])
            self.assertEqual(body["views"][view]["prompt_revision"], 0)
        self.assertEqual(self.post("/views/review").status_code, 409)
        with patch.object(app_module, "generate_view", return_value=(png(6), {})) as generate:
            response = self.post("/views", json={"views": body["pending_views"], "single_step": True})
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[0][1:], [png(5)])
        self.assertEqual(response.json()["pending_views"], ["right", "back"])

    def test_batch_yields_before_starting_another_long_call(self):
        with patch.object(app_module, "time", wraps=app_module.time) as clock, \
                patch.object(app_module, "generate_view", return_value=(png(), {})) as generate:
            clock.monotonic.side_effect = [0, 11]
            response = self.post("/views", json={"views": list(VIEW_TYPES)})
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once()
        self.assertEqual(response.json()["pending_views"], ["left", "right", "back"])
        self.assertEqual(response.json()["status"], "VIEWS_REVIEW")

    def test_single_step_chain_keeps_all_six_originals_before_previous_views(self):
        state, version = load_session(self.session_id, self.token)
        blobs = app_module.PublicBlobStore()
        for index in range(5):
            url = blobs.put(f"image3d/{self.session_id}/extra-{index}.png", png(index + 5), "image/png")
            state["reference_images"].append({"url": url})
        save_session(self.session_id, state, version)
        originals = [app_module.fetch_bytes(item["url"]) for item in state["reference_images"]]
        pending = list(VIEW_TYPES)
        for index, view in enumerate(VIEW_TYPES):
            with patch.object(app_module, "generate_view", return_value=(png(index), {})) as generate:
                response = self.post("/views", json={"views": pending, "single_step": True})
            self.assertEqual(response.status_code, 200, response.text)
            generate.assert_called_once()
            self.assertEqual(generate.call_args.args[0], originals + [png(i) for i in range(index)])
            self.assertIn("前6张是原始主体证据", generate.call_args.args[1])
            for prior_index, prior in enumerate(VIEW_TYPES[:index]):
                self.assertIn(f"第{7 + prior_index}张是已生成的{VIEW_NAMES[prior]}", generate.call_args.args[1])
            pending = response.json()["pending_views"]
        self.assertEqual(pending, [])
        self.assertEqual(self.post("/confirm").status_code, 409)
        self.assertTrue(self.accepted_review()["views_approved"])

    def test_stale_and_different_mode_views_are_not_used_as_references(self):
        self.generate()
        state, version = load_session(self.session_id, self.token)
        state["views"]["left"]["prompt_revision"] = 0
        state["views"]["right"]["view_mode"] = "print_sculpture"
        save_session(self.session_id, state, version)
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            response = self.post("/views", json={"views": ["back"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_args.args[0][1:], [png(0)])

    def test_rejected_earlier_view_stays_excluded_across_serial_requests(self):
        self.generate()
        result = accepted_result()
        result["front"].update(same_subject=False, issue="恢复缺失配件")
        with patch.object(app_module, "review_views", return_value=(normalize_review(result), {})):
            self.assertEqual(self.post("/views/review").status_code, 200)
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            response = self.post("/views", json={"views": ["right"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_args.args[0][1:], [png(1)])
        with patch.object(app_module, "generate_view", return_value=(png(6), {})) as generate:
            response = self.post("/views", json={"views": ["back"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(generate.call_args.args[0][1:], [png(1), png(5)])

    def test_review_corrections_survive_between_single_step_retries(self):
        self.generate()
        result = accepted_result()
        result["left"].update(same_subject=False, issue="恢复左侧饰品")
        result["right"].update(same_subject=False, issue="恢复右侧口袋")
        with patch.object(app_module, "review_views", return_value=(normalize_review(result), {})):
            self.assertEqual(self.post("/views/review").status_code, 200)
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            response = self.post("/views", json={"views": ["right", "left"], "single_step": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("恢复左侧饰品", generate.call_args.args[1])
        with patch.object(app_module, "generate_view", return_value=(png(6), {})) as generate:
            response = self.post("/views", json={"views": ["right"], "single_step": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("恢复右侧口袋", generate.call_args.args[1])
        self.assertEqual(generate.call_args.args[0][1:], [png(0), png(5)])

    def test_exact_duplicates_block_confirmation_without_any_paid_review(self):
        self.generate(duplicate=True)
        with patch.object(app_module, "review_views") as review:
            response = self.post("/views/review")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["view_review"]["failed_views"], ["left", "right", "back"])
            review.assert_not_called()
        self.assertEqual(self.post("/confirm").status_code, 409)
        self.assertEqual(self.post("/generate", json={}).status_code, 409)

    def test_review_is_required_cached_and_invalidated_after_single_retry(self):
        self.generate()
        self.assertEqual(self.post("/confirm").status_code, 409)
        review_result = normalize_review(accepted_result())
        with patch.object(app_module, "review_views", return_value=(review_result, {})) as reviewer:
            self.assertTrue(self.post("/views/review").json()["views_approved"])
            self.assertTrue(self.post("/views/review").json()["views_approved"])
            self.assertEqual(self.post("/confirm").status_code, 200)
            self.assertEqual(self.post("/views/review").status_code, 200)
            reviewer.assert_called_once()
        # Return to review, as when a user chooses to correct a generated set.
        state, version = load_session(self.session_id, self.token)
        state["status"] = "VIEWS_REVIEW"
        previous = copy.deepcopy(state["views"])
        save_session(self.session_id, state, version)
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            retried = self.post("/views", json={"views": ["left"]})
        self.assertEqual(retried.status_code, 200, retried.text)
        generate.assert_called_once()
        body = retried.json()
        self.assertIsNone(body["view_review"])
        self.assertFalse(body["views_approved"])
        self.assertIsNone(body["confirmed_at"])
        for view in ("front", "right", "back"):
            self.assertEqual(body["views"][view], previous[view])
        self.assertEqual(self.post("/confirm").status_code, 409)

    def test_semantic_failure_provides_feedback_to_targeted_retry(self):
        self.generate()
        result = accepted_result()
        result["right"].update(actual_view="left", duplicate_of="left", issue="移到主体右侧，正面朝画面左")
        with patch.object(app_module, "review_views", return_value=(normalize_review(result), {})) as review:
            body = self.post("/views/review").json()
            self.assertEqual(body["view_review"]["failed_views"], ["right"])
            self.post("/views/review")
            review.assert_called_once()
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            response = self.post("/views", json={"views": ["right"]})
        self.assertEqual(response.status_code, 200, response.text)
        generate.assert_called_once()
        self.assertIn("移到主体右侧", generate.call_args.args[1])

    def test_review_failure_keeps_images_and_only_retries_review(self):
        original = self.generate()["views"]
        with patch.object(app_module, "review_views", side_effect=WorkflowError("VIEW_REVIEW_UNAVAILABLE", "timeout", 502)):
            self.assertEqual(self.post("/views/review").status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(state["status"], "VIEWS_REVIEW")
        self.assertEqual(state["views"], original)
        self.assertFalse(app_module._views_approved(state))
        with patch.object(app_module, "generate_view") as generate:
            self.assertTrue(self.accepted_review()["views_approved"])
            generate.assert_not_called()

    def test_failed_regeneration_does_not_accept_the_previous_image(self):
        self.generate()
        self.accepted_review()
        with patch.object(app_module, "generate_view", side_effect=WorkflowError("OPENAI_ERROR", "failure", 502)):
            self.assertEqual(self.post("/views", json={"views": ["right"]}).status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertIn("url", state["views"]["right"])
        self.assertFalse(app_module._views_current(state))
        self.assertEqual(self.post("/views/review").status_code, 409)
        self.assertEqual(self.post("/confirm").status_code, 409)

    def test_partial_generation_retains_other_results_and_blocks_review(self):
        def produce(references, prompt, model, quality):
            if "一张右视图" in prompt:
                raise WorkflowError("OPENAI_ERROR", "failure", 502)
            return png(), {}
        with patch.object(app_module, "generate_view", side_effect=produce):
            self.assertEqual(self.post("/views", json={"views": list(VIEW_TYPES)}).status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(set(state["views"]), {"front", "left"})
        self.assertEqual(self.post("/views/review").status_code, 409)

    def test_refine_includes_prior_view_before_target_and_requires_new_review(self):
        self.generate()
        self.accepted_review()
        with patch.object(app_module, "generate_view", return_value=(png(5), {})) as generate:
            response = self.post("/views/left/refine", json={"prompt": "恢复左侧纹理"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(generate.call_args.args[0]), 3)
        self.assertEqual(generate.call_args.args[0][1:], [png(0), png(1)])
        self.assertIn("第2张是已生成的正视图", generate.call_args.args[1])
        self.assertIn("最后一张是待修正的左视图", generate.call_args.args[1])
        self.assertIsNone(response.json()["view_review"])
        self.assertEqual(self.post("/confirm").status_code, 409)

    def test_refine_failure_unlocks_session_and_preserves_original(self):
        before = self.generate()["views"]["left"]["url"]
        with patch.object(app_module, "generate_view", side_effect=WorkflowError("OPENAI_ERROR", "failure", 502)):
            response = self.post("/views/left/refine", json={"prompt": "调整"})
        self.assertEqual(response.status_code, 502)
        state, _ = load_session(self.session_id, self.token)
        self.assertEqual(state["status"], "VIEWS_REVIEW")
        self.assertEqual(state["views"]["left"]["url"], before)
        self.assertFalse(app_module._views_approved(state))

    def test_busy_review_blocks_mutations_and_timeout_allows_review_retry(self):
        self.generate()
        state, version = load_session(self.session_id, self.token)
        state["status"] = "REVIEWING_VIEWS"
        save_session(self.session_id, state, version)
        self.assertEqual(self.post("/views/review").status_code, 409)
        self.assertEqual(self.post("/views", json={"views": ["left"]}).status_code, 409)
        self.assertEqual(self.client.patch(self.path + "/prompt", headers=self.headers, json={"prompt": "new"}).status_code, 409)
        state, version = load_session(self.session_id, self.token)
        state["updated_at"] = 1
        save_session(self.session_id, state, version)
        with patch.object(app_module, "generate_view") as generate:
            self.assertTrue(self.accepted_review()["views_approved"])
            generate.assert_not_called()

    def test_legacy_confirmation_cannot_bypass_review_or_library_gate(self):
        self.generate()
        state, version = load_session(self.session_id, self.token)
        state["status"] = "CONFIRMED"
        state["confirmed_snapshot"] = {"views": copy.deepcopy(state["views"]), "confirmed_at": 1}
        save_session(self.session_id, state, version)
        self.assertEqual(self.post("/generate", json={}).json()["code"], "VIEW_REVIEW_REQUIRED")
        saved = self.client.post("/api/view-library/folders", headers=self.headers,
                                 json={"session_id": self.session_id, "name": "wrong views"})
        self.assertEqual(saved.status_code, 409)
        self.assertTrue(self.accepted_review()["views_approved"])
        self.assertEqual(self.post("/confirm").status_code, 200)

    def test_manual_upload_keeps_existing_confirmation_flow(self):
        response = self.client.post("/api/sessions", files={"image": ("front.png", png(), "image/png")},
                                    data={"view_source": "uploaded"})
        body = response.json()
        path = f"/api/sessions/{body['session_id']}"
        headers = {"X-Session-Token": body["access_token"]}
        with patch.object(app_module, "review_views") as reviewer:
            for i, view in enumerate(("left", "right", "back"), start=1):
                uploaded = self.client.post(path + f"/views/{view}/upload", headers=headers,
                                             files={"image": (view + ".png", png(i), "image/png")})
                self.assertEqual(uploaded.status_code, 200)
            self.assertEqual(self.client.post(path + "/confirm", headers=headers).status_code, 200)
            reviewer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
