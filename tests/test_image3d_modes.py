from pathlib import Path
import unittest

from vercel_app.workflow import GENERATE_TYPES, hunyuan_model_for_generate_type


ROOT = Path(__file__).resolve().parents[1]


class Image3DModeTests(unittest.TestCase):
    def test_hunyuan_31_unsupported_modes_fall_back_to_30(self):
        self.assertEqual(hunyuan_model_for_generate_type("hy-3d-3.1", "LowPoly"), "hy-3d-3.0")
        self.assertEqual(hunyuan_model_for_generate_type("hy-3d-3.1", "Sketch"), "hy-3d-3.0")
        self.assertEqual(hunyuan_model_for_generate_type("hy-3d-3.1", "Normal"), "hy-3d-3.1")
        self.assertEqual(hunyuan_model_for_generate_type("hy-3d-3.1", "Geometry"), "hy-3d-3.1")

    def test_non_31_model_is_not_overridden(self):
        self.assertEqual(hunyuan_model_for_generate_type("hy-3d-3.0", "LowPoly"), "hy-3d-3.0")

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
        self.assertIn("不生成颜色和纹理", javascript)


if __name__ == "__main__":
    unittest.main()
