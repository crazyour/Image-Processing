from pathlib import Path
import unittest

from vercel_app.workflow import GENERATE_TYPES


ROOT = Path(__file__).resolve().parents[1]


class Image3DModeTests(unittest.TestCase):
    def test_backend_accepts_all_hunyuan_generation_modes(self):
        self.assertEqual(
            GENERATE_TYPES,
            {"Normal", "LowPoly", "Geometry", "Sketch"},
        )

    def test_frontend_lists_and_describes_all_generation_modes(self):
        html = (ROOT / "image3d_frontend" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "image3d_frontend" / "app.js").read_text(encoding="utf-8")

        for generate_type in GENERATE_TYPES:
            self.assertIn(f'value="{generate_type}"', html)
            self.assertIn(f"{generate_type}:", javascript)
        self.assertIn('generateType === "Geometry"', javascript)
        self.assertIn("只生成无纹理白模", javascript)


if __name__ == "__main__":
    unittest.main()