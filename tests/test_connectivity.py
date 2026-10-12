from __future__ import annotations

import unittest

import cv2
import numpy as np

from vercel_app.connectivity import analyze_connectivity


def png_with_shapes(draw_shapes) -> bytes:
    image = np.full((96, 96, 3), 255, dtype=np.uint8)
    draw_shapes(image)
    ok, output = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError("test image encoding failed")
    return output.tobytes()


class ConnectivityTests(unittest.TestCase):
    def test_single_subject_passes(self):
        image = png_with_shapes(lambda canvas: cv2.rectangle(canvas, (18, 18), (78, 78), (0, 0, 0), -1))

        report, _, _ = analyze_connectivity(image)

        self.assertTrue(report["passed"])
        self.assertEqual(report["black_component_count"], 1)
        self.assertEqual(report["island_count"], 0)

    def test_islands_are_counted_and_colored_separately(self):
        def shapes(canvas):
            cv2.rectangle(canvas, (10, 10), (55, 75), (0, 0, 0), -1)
            cv2.rectangle(canvas, (70, 12), (79, 21), (0, 0, 0), -1)
            cv2.rectangle(canvas, (72, 60), (84, 72), (0, 0, 0), -1)

        report, diagnostic, repair_guide = analyze_connectivity(png_with_shapes(shapes))
        decoded = cv2.imdecode(np.frombuffer(diagnostic, np.uint8), cv2.IMREAD_COLOR)
        colors = {tuple(pixel) for row in decoded for pixel in row}
        guide = cv2.imdecode(np.frombuffer(repair_guide, np.uint8), cv2.IMREAD_COLOR)
        guide_colors = {tuple(pixel) for row in guide for pixel in row}

        self.assertFalse(report["passed"])
        self.assertEqual(report["disconnected_island_count"], 2)
        self.assertEqual(report["island_count"], 2)
        self.assertIn((255, 255, 255), colors)
        self.assertIn((235, 99, 37), colors)  # blue subject, represented as BGR
        self.assertGreaterEqual(len(colors), 4)  # white, blue, and two island colors
        self.assertIn((0, 0, 0), guide_colors)
        self.assertIn((255, 255, 255), guide_colors)
        self.assertIn((0, 0, 255), guide_colors)  # red issue rings in BGR
        self.assertEqual(len(report["issue_regions"]), 2)

    def test_one_pixel_neck_is_a_repair_target(self):
        def shapes(canvas):
            cv2.rectangle(canvas, (10, 25), (35, 65), (0, 0, 0), -1)
            cv2.rectangle(canvas, (60, 25), (85, 65), (0, 0, 0), -1)
            cv2.line(canvas, (35, 45), (60, 45), (0, 0, 0), 1)

        report, _, repair_guide = analyze_connectivity(png_with_shapes(shapes))
        guide = cv2.imdecode(np.frombuffer(repair_guide, np.uint8), cv2.IMREAD_COLOR)
        guide_colors = {tuple(pixel) for row in guide for pixel in row}

        self.assertEqual(report["black_component_count"], 1)
        self.assertEqual(report["disconnected_island_count"], 0)
        self.assertEqual(report["fragile_connection_count"], 1)
        self.assertEqual(report["island_count"], 1)
        self.assertFalse(report["passed"])
        self.assertIn((0, 140, 255), guide_colors)  # orange weak-link ring in BGR


if __name__ == "__main__":
    unittest.main()
