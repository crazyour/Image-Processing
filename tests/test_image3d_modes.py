from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from vercel_app.workflow import (
    GENERATE_TYPES,
    _meshy_balance_cache,
    query_meshy,
    query_meshy_balance,
    submit_hunyuan,
    submit_meshy,
)


ROOT = Path(__file__).resolve().parents[1]


class Image3DModeTests(unittest.TestCase):
    def test_backend_accepts_colored_and_geometry_modes(self):
        self.assertEqual(GENERATE_TYPES, {"Normal", "Geometry"})

    def test_frontend_lists_and_describes_all_generation_modes(self):
        html = (ROOT / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "image3d_frontend" / "app.js").read_text(encoding="utf-8")

        for generate_type in GENERATE_TYPES:
            self.assertIn(f'value="{generate_type}"', html)
        self.assertNotIn('value="LowPoly"', html)
        self.assertNotIn('value="Sketch"', html)
        self.assertIn('generateType === "Geometry"', javascript)
        self.assertIn("带颜色和表面纹理", javascript)
        self.assertIn("无纹理白模", javascript)
        self.assertIn("腾讯混元 HY 3.1", javascript)

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
            {"generate_type": "Geometry", "enable_pbr": True, "result_format": "STL"},
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
            {"generate_type": "Geometry", "enable_pbr": True, "result_format": "STL"},
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
        self.assertEqual(payload["target_formats"], ["stl"])
        self.assertFalse(payload["should_texture"])
        self.assertFalse(payload["enable_pbr"])
        self.assertEqual(created["id"], "018a210d-8ba4-705c-b111-1f1776f7f578")
        self.assertEqual(model, "meshy-7.1")

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


if __name__ == "__main__":
    unittest.main()
