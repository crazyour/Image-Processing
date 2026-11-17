from pathlib import Path
import unittest

from vercel_app.workflow import GENERATE_TYPES


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


if __name__ == "__main__":
    unittest.main()
