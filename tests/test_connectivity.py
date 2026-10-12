from __future__ import annotations

import unittest

import cv2
import numpy as np

from vercel_app.connectivity import analyze_connectivity, create_repair_mask, remove_disconnected_islands


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

        report, _ = analyze_connectivity(image)

        self.assertTrue(report["passed"])
        self.assertEqual(report["black_component_count"], 1)
        self.assertEqual(report["island_count"], 0)

    def test_islands_are_counted_and_colored_separately(self):
        def shapes(canvas):
            cv2.rectangle(canvas, (10, 10), (55, 75), (0, 0, 0), -1)
            cv2.rectangle(canvas, (70, 12), (79, 21), (0, 0, 0), -1)
            cv2.rectangle(canvas, (72, 60), (84, 72), (0, 0, 0), -1)

        report, diagnostic = analyze_connectivity(png_with_shapes(shapes))
        decoded = cv2.imdecode(np.frombuffer(diagnostic, np.uint8), cv2.IMREAD_COLOR)
        colors = {tuple(pixel) for row in decoded for pixel in row}

        self.assertFalse(report["passed"])
        self.assertEqual(report["disconnected_island_count"], 2)
        self.assertEqual(report["island_count"], 2)
        self.assertIn((255, 255, 255), colors)
        self.assertIn((235, 99, 37), colors)  # blue subject, represented as BGR
        self.assertIn((68, 68, 239), colors)  # every island is red in BGR
        self.assertEqual(len(colors), 3)  # white background, blue subject, red islands
        self.assertEqual(len(report["issue_regions"]), 2)

    def test_one_pixel_neck_is_a_repair_target(self):
        def shapes(canvas):
            cv2.rectangle(canvas, (10, 25), (35, 65), (0, 0, 0), -1)
            cv2.rectangle(canvas, (60, 25), (85, 65), (0, 0, 0), -1)
            cv2.line(canvas, (35, 45), (60, 45), (0, 0, 0), 1)

        report, _ = analyze_connectivity(png_with_shapes(shapes))

        self.assertEqual(report["black_component_count"], 1)
        self.assertEqual(report["disconnected_island_count"], 0)
        self.assertEqual(report["fragile_connection_count"], 1)
        self.assertEqual(report["island_count"], 1)
        self.assertFalse(report["passed"])

    def test_repair_mask_opens_issue_and_connection_corridor(self):
        def shapes(canvas):
            cv2.rectangle(canvas, (10, 20), (50, 76), (0, 0, 0), -1)
            cv2.circle(canvas, (76, 45), 6, (0, 0, 0), -1)

        encoded = png_with_shapes(shapes)
        decoded = cv2.imdecode(
            np.frombuffer(create_repair_mask(encoded), np.uint8), cv2.IMREAD_UNCHANGED,
        )

        self.assertEqual(decoded.shape[2], 4)
        alpha = decoded[:, :, 3]
        self.assertEqual(int(alpha[45, 76]), 0)  # island is editable
        self.assertEqual(int(alpha[45, 60]), 0)  # white gap corridor is editable
        self.assertEqual(int(alpha[90, 90]), 255)  # unrelated background is protected

    def test_local_cleanup_keeps_only_largest_component(self):
        def shapes(canvas):
            cv2.rectangle(canvas, (10, 10), (60, 80), (0, 0, 0), -1)
            cv2.circle(canvas, (82, 20), 7, (0, 0, 0), -1)

        cleaned = remove_disconnected_islands(png_with_shapes(shapes))
        report, _ = analyze_connectivity(cleaned)

        self.assertTrue(report["passed"])
        self.assertEqual(report["black_component_count"], 1)


if __name__ == "__main__":
    unittest.main()
