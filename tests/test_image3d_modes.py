from pathlib import Path
import copy
import unittest
from unittest.mock import Mock, patch

import app as app_module
import test_mesh_smoothing_workflow as smoothing_tests
from vercel_app.store import load_session

from vercel_app.workflow import (
    QUALITY_LEVELS,
    WorkflowError,
    _meshy_balance_cache,
    query_meshy,
    query_meshy_balance,
    submit_hunyuan,
    submit_meshy,
)


ROOT = Path(__file__).resolve().parents[1]


class Image3DModeTests(unittest.TestCase):
    def test_backend_defaults_to_fixed_detailed_geometry(self):
        self.assertEqual(QUALITY_LEVELS, {"good"})
        self.assertEqual(app_module.Generate3DRequest().quality, "good")

    def test_frontend_does_not_offer_or_describe_quality_levels(self):
        html = (ROOT / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "image3d_frontend" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn('id="generation-quality"', html)
        self.assertNotIn('id="generate-type"', html)
        self.assertNotIn('id="enable-pbr"', html)
        self.assertNotIn('$("#generation-quality")', javascript)
        for label in ("普通效果", "好效果", "生成更快、花费较少"):
            self.assertNotIn(label, html + javascript)

    def test_frontend_defaults_to_meshy_and_sends_selected_provider(self):
        html = (ROOT / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "image3d_frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('name="model-provider" value="hunyuan"', html)
        self.assertIn('name="model-provider" value="meshy" checked', html)
        self.assertIn('model_provider: selectedModelProvider()', javascript)
        self.assertIn('state?.model_task || state?.hunyuan_task', javascript)
        self.assertIn('id="meshy-balance"', html)
        self.assertIn('id="task-credit-usage"', html)
        self.assertIn('/api/meshy/balance', javascript)
        self.assertIn('consumed_credits', javascript)

    def test_optional_iterative_texture_editor_is_inside_upload_step(self):
        html = (ROOT / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "image3d_frontend" / "app.js").read_text(encoding="utf-8")

        self.assertLess(html.index('id="texture-content"'), html.index('id="upload-submit"'))
        self.assertNotIn('id="enable-texture-edit"', html)
        self.assertNotIn('id="initial-prompt"', html)
        self.assertIn('id="generate-texture"', html)
        self.assertIn('/texture`', javascript)
        self.assertIn('active_texture_image', javascript)

    @patch("vercel_app.workflow._hunyuan_config")
    @patch("vercel_app.workflow.httpx.Client")
    def test_hunyuan_31_sends_multiview_geometry_stl_request(self, client_class, config):
        config.return_value = Mock(base_url="https://example.invalid", api_key="test-key")
        response = Mock(status_code=200)
        response.json.return_value = {"id": "123", "status": "queued"}
        client_class.return_value.__enter__.return_value.post.return_value = response

        submit_hunyuan(
            "https://example.com/front.png",
            {
                "left": "https://example.com/left.png",
                "right": "https://example.com/right.png",
                "back": "https://example.com/back.png",
            },
            {"quality": "good", "result_format": "STL"},
            "hy-3d-3.1",
        )

        payload = client_class.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "hy-3d-3.1")
        self.assertEqual(payload["image_url"], "https://example.com/front.png")
        self.assertEqual(payload["multi_view_images"], [
            {"view_type": "left", "view_image_url": "https://example.com/left.png"},
            {"view_type": "right", "view_image_url": "https://example.com/right.png"},
            {"view_type": "back", "view_image_url": "https://example.com/back.png"},
        ])
        self.assertEqual(payload["generate_type"], "Geometry")
        self.assertFalse(payload["enable_pbr"])
        self.assertEqual(payload["face_count"], 1_000_000)
        self.assertEqual(payload["result_format"], "STL")

    @patch("vercel_app.workflow._hunyuan_config")
    @patch("vercel_app.workflow.httpx.Client")
    def test_hunyuan_legacy_and_omitted_quality_keep_million_face_mapping(self, client_class, config):
        config.return_value = Mock(base_url="https://example.invalid", api_key="test-key")
        response = Mock(status_code=200)
        response.json.return_value = {"id": "123", "status": "queued"}
        client_class.return_value.__enter__.return_value.post.return_value = response

        for quality in ({}, {"quality": "normal"}, {"quality": "good"}):
            with self.subTest(quality=quality):
                submit_hunyuan(
                    "https://example.com/front.png",
                    {view: f"https://example.com/{view}.png" for view in ("left", "right", "back")},
                    {**quality, "result_format": "STL"}, "hy-3d-3.1",
                )
                payload = client_class.return_value.__enter__.return_value.post.call_args.kwargs["json"]
                self.assertEqual(payload["generate_type"], "Geometry")
                self.assertFalse(payload["enable_pbr"])
                self.assertEqual(payload["face_count"], 1_000_000)
                self.assertEqual(payload["result_format"], "STL")

    @patch("vercel_app.workflow._meshy_config")
    @patch("vercel_app.workflow.httpx.Client")
    def test_meshy_71_sends_four_views_to_native_multi_image_api(self, client_class, config):
        config.return_value = Mock(base_url="https://api.meshy.ai", api_key="test-key")
        response = Mock(status_code=202)
        response.json.return_value = {"result": "018a210d-8ba4-705c-b111-1f1776f7f578"}
        client_class.return_value.__enter__.return_value.post.return_value = response

        created, model = submit_meshy(
            "https://example.com/front.png",
            {
                "left": "https://example.com/left.png",
                "right": "https://example.com/right.png",
                "back": "https://example.com/back.png",
            },
            {"quality": "good", "result_format": "STL"},
        )

        request = client_class.return_value.__enter__.return_value.post
        self.assertEqual(request.call_args.args[0], "/openapi/v1/multi-image-to-3d")
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["image_urls"], [
            "https://example.com/front.png",
            "https://example.com/left.png",
            "https://example.com/right.png",
            "https://example.com/back.png",
        ])
        self.assertEqual(payload["ai_model"], "meshy-7.1")
        self.assertEqual(payload["geometry_resolution"], "2k")
        self.assertFalse(payload["image_enhancement"])
        self.assertEqual(payload["target_formats"], ["stl"])
        self.assertFalse(payload["should_texture"])
        self.assertFalse(payload["enable_pbr"])
        self.assertFalse(payload["should_remesh"])
        self.assertNotIn("target_polycount", payload)
        self.assertEqual(created["id"], "018a210d-8ba4-705c-b111-1f1776f7f578")
        self.assertEqual(model, "meshy-7.1")

    @patch("vercel_app.workflow._meshy_config")
    @patch("vercel_app.workflow.httpx.Client")
    def test_meshy_legacy_and_omitted_quality_keep_2k_without_remeshing(self, client_class, config):
        config.return_value = Mock(base_url="https://api.meshy.ai", api_key="test-key")
        response = Mock(status_code=202)
        response.json.return_value = {"result": "018a210d-8ba4-705c-b111-1f1776f7f578"}
        client_class.return_value.__enter__.return_value.post.return_value = response

        for quality in ({}, {"quality": "normal"}, {"quality": "good"}):
            with self.subTest(quality=quality):
                submit_meshy(
                    "https://example.com/front.png",
                    {view: f"https://example.com/{view}.png" for view in ("left", "right", "back")},
                    {**quality, "result_format": "STL"},
                )
                payload = client_class.return_value.__enter__.return_value.post.call_args.kwargs["json"]
                self.assertEqual(payload["geometry_resolution"], "2k")
                self.assertFalse(payload["image_enhancement"])
                self.assertFalse(payload["should_remesh"])
                self.assertNotIn("topology", payload)
                self.assertNotIn("target_polycount", payload)
                self.assertEqual(payload["target_formats"], ["stl"])

    @patch("vercel_app.workflow.httpx.Client")
    def test_invalid_quality_is_rejected_before_any_provider_request(self, client):
        views = {view: f"https://example.com/{view}.png" for view in ("left", "right", "back")}
        for submit, model in ((submit_meshy, "meshy-7.1"), (submit_hunyuan, "hy-3d-3.1")):
            for quality in ("invalid", "standard", "NORMAL", "", None, [], {}, 1, True):
                with self.subTest(provider=submit.__name__, quality=quality):
                    with self.assertRaises(WorkflowError) as caught:
                        submit("https://example.com/front.png", views, {"quality": quality}, model)
                    self.assertEqual(caught.exception.status, 422)
                    self.assertEqual(caught.exception.code, "INVALID_OPTION")
        client.assert_not_called()

    @patch("vercel_app.workflow._meshy_config")
    @patch("vercel_app.workflow.httpx.Client")
    def test_meshy_task_is_normalized_for_existing_polling_flow(self, client_class, config):
        config.return_value = Mock(base_url="https://api.meshy.ai", api_key="test-key")
        response = Mock(status_code=200)
        response.json.return_value = {
            "status": "SUCCEEDED",
            "progress": 100,
            "finished_at": 1730000000000,
            "thumbnail_url": "https://assets.meshy.ai/preview.png",
            "model_urls": {"stl": "https://assets.meshy.ai/model.stl"},
            "consumed_credits": 20,
            "task_error": None,
        }
        client_class.return_value.__enter__.return_value.get.return_value = response

        result = query_meshy("018a210d-8ba4-705c-b111-1f1776f7f578")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["progress"], 100)
        self.assertEqual(result["data"], [{
            "type": "stl",
            "url": "https://assets.meshy.ai/model.stl",
            "preview_image_url": "https://assets.meshy.ai/preview.png",
        }])
        self.assertEqual(result["result_credit_details"]["consumed_credits"], 20)

    @patch("vercel_app.workflow._meshy_config")
    @patch("vercel_app.workflow.httpx.Client")
    def test_meshy_balance_is_normalized_and_cached(self, client_class, config):
        _meshy_balance_cache.update({"expires_at": 0.0, "value": None})
        config.return_value = Mock(base_url="https://api.meshy.ai", api_key="test-key")
        response = Mock(status_code=200)
        response.json.return_value = {
            "balance": 1000,
            "monthly_credits": 600,
            "permanent_credits": 400,
            "monthly_resets_at": 1793491200000,
        }
        client_class.return_value.__enter__.return_value.get.return_value = response

        first = query_meshy_balance()
        second = query_meshy_balance()

        self.assertEqual(first["balance"], 1000)
        self.assertEqual(first["monthly_credits"], 600)
        self.assertEqual(first["permanent_credits"], 400)
        self.assertEqual(second, first)
        client_class.return_value.__enter__.return_value.get.assert_called_once()


class FixedQualityGenerationTests(unittest.TestCase):
    setUp = smoothing_tests.SmoothingWorkflowTests.setUp
    update = smoothing_tests.SmoothingWorkflowTests.update

    def ready(self):
        views = {view: {"url": f"https://example.invalid/{view}.png", "prompt_revision": 1}
                 for view in ("front", "left", "right", "back")}
        self.update(lambda state: state.update(
            status="VIEWS_CONFIRMED", prompt_revision=1, view_source="uploaded",
            views=views, confirmed_snapshot={"views": copy.deepcopy(views)},
            model_task=None, hunyuan_task=None))

    def test_new_explicit_and_legacy_requests_always_record_and_submit_good(self):
        for provider in ("meshy", "hunyuan"):
            for quality in ({}, {"quality": "good"}, {"quality": "normal"}):
                with self.subTest(provider=provider, quality=quality):
                    self.ready()
                    model_name = app_module.MESHY_IMAGE3D_MODEL if provider == "meshy" else app_module.IMAGE3D_MODEL
                    with patch.object(app_module, f"submit_{provider}", return_value=(
                            {"id": "fixed-quality-task", "status": "queued"}, model_name)) as submit, \
                            patch.object(app_module, "reserve_usage", return_value="test-reservation"), \
                            patch.object(app_module, "record_usage") as usage, \
                            patch.object(app_module, "process_stl") as repair, \
                            patch.object(app_module, "process_smoothing") as smooth:
                        response = self.client.post(f"/api/sessions/{self.sid}/generate", headers=self.headers,
                                                    json={"model_provider": provider, **quality})
                    self.assertEqual(response.status_code, 200, response.text)
                    task = response.json()["model_task"]
                    self.assertEqual(task["provider"], provider)
                    expected = {"quality": "good", "result_format": "STL"}
                    if provider == "meshy":
                        expected["image_enhancement"] = False
                    self.assertEqual(task["request"], expected)
                    submit.assert_called_once()
                    self.assertEqual(submit.call_args.args[2], expected)
                    self.assertEqual(submit.call_args.args[3], model_name)
                    usage.assert_called_once()
                    repair.assert_not_called()
                    smooth.assert_not_called()
                    saved, _ = load_session(self.sid, self.token)
                    self.assertEqual(saved["model_task"]["request"]["quality"], "good")

    def test_invalid_request_quality_never_submits_or_reserves_usage(self):
        self.ready()
        before = load_session(self.sid, self.token)
        with patch.object(app_module, "submit_meshy") as meshy, \
                patch.object(app_module, "submit_hunyuan") as hunyuan, \
                patch.object(app_module, "reserve_usage") as reserve:
            for quality in ("standard", "invalid", "", None, [], 1):
                response = self.client.post(f"/api/sessions/{self.sid}/generate", headers=self.headers,
                                            json={"quality": quality})
                self.assertEqual(response.status_code, 422, response.text)
            meshy.assert_not_called()
            hunyuan.assert_not_called()
            reserve.assert_not_called()
        self.assertEqual(load_session(self.sid, self.token), before)


if __name__ == "__main__":
    unittest.main()
