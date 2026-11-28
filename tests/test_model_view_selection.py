import io
import itertools
import unittest
from unittest.mock import Mock, patch
from PIL import Image

import app as app_module
import test_mesh_smoothing_workflow as fixtures
from vercel_app.store import load_session
from vercel_app.workflow import submit_meshy, submit_hunyuan


VIEWS = ("front", "left", "right", "back")


class ModelViewSelectionTests(unittest.TestCase):
    setUp = fixtures.SmoothingWorkflowTests.setUp
    update = fixtures.SmoothingWorkflowTests.update

    def ready(self, **overrides):
        self.base = f"/api/sessions/{self.sid}"
        views = {view: {"url": f"https://example.invalid/{view}.png", "prompt_revision": 1,
                        "revision": 1, "sha256": view} for view in VIEWS}
        self.update(lambda state: state.update(
            {"status": "VIEWS_REVIEW", "prompt_revision": 1, "user_prompt": "雕塑",
             "view_source": "generated", "views": views, "excluded_3d_views": [],
             "confirmed_snapshot": None, "model_task": None, "hunyuan_task": None, **overrides}))

    def select(self, excluded):
        return self.client.patch(self.base + "/model-views", headers=self.headers, json={"excluded_views": excluded})

    def confirm(self):
        return self.client.post(self.base + "/confirm", headers=self.headers)

    def generate(self, provider="meshy", **options):
        return self.client.post(self.base + "/generate", headers=self.headers, json={"model_provider": provider, **options})

    def test_meshy_enhancement_choice_reaches_provider_and_is_saved_with_task(self):
        for options in ({}, {"image_enhancement": False}, {"image_enhancement": True}):
            with self.subTest(options=options):
                self.ready()
                self.assertEqual(self.confirm().status_code, 200)
                with patch("vercel_app.workflow._meshy_config", return_value=Mock(base_url="https://example.invalid", api_key="fake")), \
                        patch("vercel_app.workflow.httpx.Client") as client, \
                        patch.object(app_module, "reserve_usage", return_value="reserved"), \
                        patch.object(app_module, "record_usage"):
                    post = client.return_value.__enter__.return_value.post
                    post.return_value = Mock(status_code=202)
                    post.return_value.json.return_value = {"result": "enhancement-task"}
                    response = self.generate(**options)
                self.assertEqual(response.status_code, 200, response.text)
                expected = options.get("image_enhancement", False)
                payload = post.call_args.kwargs["json"]
                self.assertEqual(payload["image_enhancement"], expected)
                self.assertEqual(payload["geometry_resolution"], "2k")
                self.assertFalse(payload["should_remesh"])
                self.assertEqual(response.json()["model_task"]["request"]["image_enhancement"], expected)
                saved, _ = load_session(self.sid, self.token)
                self.assertEqual(saved["model_task"]["request"]["image_enhancement"], expected)

    def test_every_nonempty_subset_is_confirmed_and_only_selected_urls_are_submitted(self):
        for count in range(1, 5):
            for selected in itertools.combinations(VIEWS, count):
                with self.subTest(selected=selected):
                    self.ready()
                    before = load_session(self.sid, self.token)[0]
                    excluded = [view for view in VIEWS if view not in selected]
                    with patch.object(app_module, "reserve_usage", return_value="reserved") as reserve, \
                            patch.object(app_module, "record_usage"), \
                            patch.object(app_module, "submit_meshy", return_value=({"id": "selected-task"}, "meshy-7.1")) as submit:
                        response = self.select(excluded)
                        self.assertEqual(response.status_code, 200, response.text)
                        self.assertEqual(response.json()["views"], before["views"])
                        self.assertEqual(self.confirm().status_code, 200)
                        reserve.assert_not_called()
                        submit.assert_not_called()
                        saved = load_session(self.sid, self.token)[0]
                        self.assertEqual(list(saved["confirmed_snapshot"]["views"]), list(selected))
                        self.assertEqual(self.client.get(self.base, headers=self.headers).json()["excluded_3d_views"], excluded)
                        generated = self.generate()
                    self.assertEqual(generated.status_code, 200, generated.text)
                    self.assertEqual(submit.call_args.args[0], f"https://example.invalid/{selected[0]}.png")
                    self.assertEqual(submit.call_args.args[1], {view: f"https://example.invalid/{view}.png" for view in selected[1:]})
                    self.assertEqual(generated.json()["model_task"]["input_views"], list(selected))
                    self.assertEqual(generated.json()["model_task"]["primary_view"], selected[0])
                    reserve.assert_called_once()

    def test_restore_invalidates_confirmation_but_keeps_the_images(self):
        self.ready()
        self.select(["right"])
        self.confirm()
        before = load_session(self.sid, self.token)[0]
        self.assertEqual(self.select(["right"]).json()["confirmed_at"], before["confirmed_snapshot"]["confirmed_at"])
        restored = self.select([])
        self.assertEqual(restored.status_code, 200)
        self.assertIsNone(restored.json()["confirmed_at"])
        self.assertEqual(restored.json()["status"], "VIEWS_REVIEW")
        self.assertEqual(restored.json()["views"], before["views"])
        with patch.object(app_module, "reserve_usage") as reserve:
            self.assertEqual(self.generate().status_code, 409)
            reserve.assert_not_called()

    def test_missing_or_stale_excluded_views_do_not_block_confirmation_but_library_still_requires_four(self):
        self.ready()
        self.update(lambda state: (state["views"].pop("right"), state["views"]["left"].update(prompt_revision=0)))
        self.assertEqual(self.confirm().status_code, 409)
        chosen = self.select(["left", "right"])
        self.assertTrue(chosen.json()["model_views_ready"])
        self.assertFalse(chosen.json()["views_approved"])
        self.assertEqual(self.confirm().status_code, 200)
        self.assertEqual(self.client.post("/api/view-library/folders", headers=self.headers,
                         json={"session_id": self.sid, "name": "partial"}).status_code, 409)
        self.select(["right"])
        self.assertEqual(self.confirm().status_code, 409)

    def test_excluded_images_can_still_be_archived_with_the_full_set(self):
        self.ready(view_source="uploaded")
        self.select(["left", "right"])
        response = self.client.post("/api/view-library/folders", headers=self.headers,
                                    json={"session_id": self.sid, "name": "all views"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(response.json()["views"]), set(VIEWS))

    def test_hunyuan_requires_front_before_reserving_or_submitting(self):
        self.ready()
        self.select(["front"])
        self.confirm()
        with patch.object(app_module, "reserve_usage") as reserve, patch.object(app_module, "submit_hunyuan") as submit:
            response = self.generate("hunyuan")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "FRONT_VIEW_REQUIRED")
            reserve.assert_not_called()
            submit.assert_not_called()

    def test_single_front_can_be_submitted_to_hunyuan(self):
        self.ready()
        self.select(["left", "right", "back"])
        self.confirm()
        with patch.object(app_module, "reserve_usage", return_value="reserved"), patch.object(app_module, "record_usage"), \
                patch.object(app_module, "submit_hunyuan", return_value=({"id": "selected-task"}, "hy-3d-3.1")) as submit:
            response = self.generate("hunyuan", image_enhancement=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(submit.call_args.args[1], {})
        self.assertNotIn("image_enhancement", submit.call_args.args[2])
        self.assertNotIn("image_enhancement", response.json()["model_task"]["request"])

    def test_stale_confirmation_cannot_reintroduce_an_excluded_view(self):
        self.ready()
        self.confirm()
        self.update(lambda state: state.update(excluded_3d_views=["left"]))
        with patch.object(app_module, "reserve_usage") as reserve:
            response = self.generate()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "NOT_CONFIRMED")
            reserve.assert_not_called()

    def test_selected_image_changed_after_confirmation_is_blocked(self):
        self.ready()
        self.confirm()
        self.update(lambda state: state["views"]["front"].update(url="https://example.invalid/new.png"))
        self.assertEqual(self.generate().json()["code"], "NOT_CONFIRMED")

    def test_selection_validates_input_and_locks_running_and_finished_tasks(self):
        self.ready()
        before = load_session(self.sid, self.token)
        for excluded in (list(VIEWS), ["unknown"], ["front", "front"], "front"):
            with self.subTest(excluded=excluded):
                self.assertEqual(self.select(excluded).status_code, 422)
                self.assertEqual(load_session(self.sid, self.token), before)
        for status in ("GENERATING_VIEWS", "REVIEWING_VIEWS", "SUBMITTING_3D", "GENERATING_3D", "SUCCEEDED"):
            self.update(lambda state: state.update(status=status))
            self.assertEqual(self.select(["left"]).status_code, 409)
        self.ready()
        for headers in ({}, {"X-Session-Token": "wrong"}):
            response = self.client.patch(self.base + "/model-views", headers=headers, json={"excluded_views": ["left"]})
            self.assertIn(response.status_code, (401, 403, 404))

    def test_manual_single_side_upload_keeps_direction_and_replacement_stays_excluded(self):
        image = io.BytesIO()
        Image.new("RGB", (128, 128), "red").save(image, "PNG")
        raw = image.getvalue()
        created = self.client.post("/api/sessions", files={"image": ("left.png", raw, "image/png")},
                                   data={"view_source": "uploaded", "initial_view": "left"})
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        self.assertEqual(set(body["views"]), {"left"})
        base = f"/api/sessions/{body['session_id']}"
        headers = {"X-Session-Token": body["access_token"]}
        selected = self.client.patch(base + "/model-views", headers=headers,
                                     json={"excluded_views": ["front", "right", "back"]})
        self.assertTrue(selected.json()["model_views_ready"])
        self.assertEqual(self.client.post(base + "/confirm", headers=headers).status_code, 200)
        replaced = self.client.post(base + "/views/right/upload", headers=headers,
                                    files={"image": ("right.png", raw, "image/png")})
        self.assertEqual(replaced.status_code, 200)
        self.assertIn("right", replaced.json()["excluded_3d_views"])
        self.assertIsNone(replaced.json()["confirmed_at"])


class ProviderSelectionPayloadTests(unittest.TestCase):
    def test_provider_payloads_keep_one_to_four_selected_images(self):
        for provider, submit in (("meshy", submit_meshy), ("hunyuan", submit_hunyuan)):
            for extra in ({}, {"back": "https://example.invalid/back.png"},
                          {view: f"https://example.invalid/{view}.png" for view in VIEWS[1:]}):
                with self.subTest(provider=provider, extra=extra), \
                        patch(f"vercel_app.workflow._{provider}_config", return_value=Mock(base_url="https://example.invalid", api_key="fake")), \
                        patch("vercel_app.workflow.httpx.Client") as client:
                    response = Mock(status_code=200)
                    response.json.return_value = {"result": "test-task", "id": "test-task"}
                    post = client.return_value.__enter__.return_value.post
                    post.return_value = response
                    submit("https://example.invalid/front.png", extra, {"quality": "good", "result_format": "STL"}, "test-model")
                    payload = post.call_args.kwargs["json"]
                    if provider == "meshy":
                        self.assertEqual(payload["image_urls"], ["https://example.invalid/front.png", *extra.values()])
                    elif extra:
                        self.assertEqual(payload["multi_view_images"], [{"view_type": view, "view_image_url": url} for view, url in extra.items()])
                    else:
                        self.assertNotIn("multi_view_images", payload)


if __name__ == "__main__":
    unittest.main()
