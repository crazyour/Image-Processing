from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from vercel_app.workflow import GENERATE_TYPES, submit_tripo


ROOT = Path(__file__).resolve().parents[1]


class Image3DModeTests(unittest.TestCase):
    def test_backend_accepts_colored_and_geometry_modes(self):
        self.assertEqual(GENERATE_TYPES, {"Normal", "Geometry"})

    def test_frontend_lists_and_describes_all_generation_modes(self):
        html = (ROOT / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "image3d_frontend" / "app.js").read_text(encoding="utf-8")

        for generate_type in GENERATE_TYPES:
            self.assertIn(f'value="{generate_type}"', html)
            self.assertIn(f"{generate_type}:", javascript)
        self.assertNotIn('value="LowPoly"', html)
        self.assertNotIn('value="Sketch"', html)
        self.assertIn('generateType === "Geometry"', javascript)
        self.assertIn("Smart Low Poly", javascript)

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
    def test_tripo_sends_each_view_as_a_separate_input(self, client_class, config):
        config.return_value = Mock(base_url="https://example.invalid", api_key="test-key")
        response = Mock(status_code=200)
        response.json.return_value = {"id": "123", "status": "queued"}
        client_class.return_value.__enter__.return_value.post.return_value = response

        submit_tripo(
            "https://example.com/front.png",
            {
                "left": "https://example.com/left.png",
                "right": "https://example.com/right.png",
                "back": "https://example.com/back.png",
            },
            {"generate_type": "Geometry", "enable_pbr": False},
        )

        payload = client_class.return_value.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertEqual(payload["inputs"], [
            {"front": "https://example.com/front.png"},
            {"left": "https://example.com/left.png"},
            {"right": "https://example.com/right.png"},
            {"back": "https://example.com/back.png"},
        ])
        self.assertTrue(all(len(item) == 1 for item in payload["inputs"]))


if __name__ == "__main__":
    unittest.main()
