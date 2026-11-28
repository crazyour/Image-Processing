from __future__ import annotations

import hashlib
import json
import struct
import time
import unittest
from unittest.mock import patch

import numpy as np

from vercel_app import mesh_quality as mesh
from vercel_app import mesh_reconstruction as reconstruction
from vercel_app.mesh_smoothing import process_smoothing


def cube():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                      [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                      [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]])
    return vertices[faces]


def binary(triangles):
    records = np.zeros(len(triangles), dtype=mesh._RECORD)
    records["vertices"] = triangles
    return b"test".ljust(80, b"\0") + struct.pack("<I", len(triangles)) + records.tobytes()


class MeshReconstructionTests(unittest.TestCase):
    def setUp(self):
        settings = patch.object(reconstruction, "ATTEMPT_SETTINGS", ((16, 0), (12, 1), (8, 2)))
        settings.start()
        self.addCleanup(settings.stop)

    def assert_verified(self, report, output):
        self.assertEqual(report["status"], "repaired", report)
        self.assertIsNotNone(output)
        self.assertEqual(report["method"], "voxel_reconstruction")
        stats = report["after"]["stats"]
        for key in ("boundary_edges", "nonmanifold_edges", "inconsistent_edges", "duplicate_faces", "degenerate_faces"):
            self.assertEqual(stats[key], 0, (key, report))
        self.assertTrue(stats["edge_closed"])
        self.assertEqual(report["stats"]["source_components"], report["stats"]["output_components"])
        self.assertEqual(report["after"]["sha256"], hashlib.sha256(output).hexdigest())
        self.assertLessEqual(stats["faces"], reconstruction.MAX_RECONSTRUCTION_FACES)
        self.assertEqual(len(output), 84 + stats["faces"] * 50)
        json.dumps(report, allow_nan=False)

    def test_open_cube_reconstructs_actual_closed_copy_that_can_be_smoothed(self):
        raw = binary(cube()[:-1])
        original = bytes(raw)
        report, output = reconstruction.process_reconstruction(raw)
        self.assert_verified(report, output)
        self.assertEqual(raw, original)
        self.assertNotEqual(output, raw)
        self.assertEqual(report["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertGreater(report["before"]["stats"]["boundary_edges"], 0)
        self.assertTrue(report["stats"]["details_changed"])
        self.assertTrue(report["stats"]["thickening_possible"])
        smooth_report, _ = process_smoothing(output)
        self.assertIn(smooth_report["status"], ("smoothed", "unchanged"), smooth_report)

    def test_nonmanifold_shared_edge_and_bad_winding_are_rebuilt(self):
        # A three-page open fan has one edge belonging to three faces.
        triangles = np.array([[[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                              [[0, 0, 0], [1, 0, 0], [0, 0, 1]],
                              [[0, 0, 0], [1, 0, 0], [0, -1, 0]]], dtype=float)
        report, output = reconstruction.process_reconstruction(binary(triangles))
        self.assert_verified(report, output)
        self.assertGreater(report["before"]["stats"]["nonmanifold_edges"], 0)

    def test_tiny_independent_component_is_preserved_with_its_own_grid(self):
        raw = binary(np.concatenate((cube()[:-1], cube() * .03 + [3, 0, 0])))
        report, output = reconstruction.process_reconstruction(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["after"]["stats"]["components"], 2)
        self.assertLess(report["stats"]["voxel_pitch_min"], report["stats"]["voxel_pitch_max"] / 10)
        points = mesh._parse(output, time.monotonic() + 10).reshape(-1, 3)
        tiny = points[points[:, 0] > 2]
        self.assertGreater(len(tiny), 0)
        self.assertGreater(tiny[:, 0].max(), 3.02)
        self.assertLess(tiny[:, 0].min(), 3.01)

    def test_output_budget_drives_lower_resolution_without_dropping_components(self):
        with patch.object(reconstruction, "MAX_RECONSTRUCTION_FACES", 4000), \
                patch.object(reconstruction, "ATTEMPT_SETTINGS", ((32, 0), (16, 1), (8, 2))):
            report, output = reconstruction.process_reconstruction(binary(cube()[:-1]))
            self.assert_verified(report, output)
        self.assertGreater(len(report["attempts"]), 1)
        self.assertEqual(report["attempts"][0]["status"], "retry")
        self.assertLess(report["stats"]["resolution"], 32)

    def test_no_resolution_may_bypass_output_budget(self):
        with patch.object(reconstruction, "MAX_RECONSTRUCTION_FACES", 10):
            report, output = reconstruction.process_reconstruction(binary(cube()[:-1]))
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)
        self.assertEqual(len(report["attempts"]), 3)
        self.assertTrue(all(item["status"] == "retry" for item in report["attempts"]))

    def test_surface_sampling_and_grid_limits_retry_instead_of_dropping_faces(self):
        for setting in ("MAX_SURFACE_SAMPLES", "MAX_GRID_CELLS", "MAX_ACTIVE_CELLS"):
            with patch.object(reconstruction, setting, 1):
                report, output = reconstruction.process_reconstruction(binary(cube()[:-1]))
            self.assertEqual(report["status"], "rejected", (setting, report))
            self.assertIsNone(output)
            self.assertTrue(all(item["status"] == "retry" for item in report["attempts"]))

    def test_component_limit_never_selects_only_largest_part(self):
        with patch.object(reconstruction, "MAX_COMPONENTS", 1):
            report, output = reconstruction.process_reconstruction(binary(np.concatenate((cube(), cube() + 3))))
        self.assertEqual(report["status"], "incomplete", report)
        self.assertIsNone(output)
        self.assertIn("没有删除", report["message"])

    def test_export_is_rechecked_and_failure_never_returns_bytes(self):
        inspect = mesh._inspect
        calls = 0

        def changed_output(raw, deadline):
            nonlocal calls
            calls += 1
            report, triangles, flips = inspect(raw, deadline)
            if calls > 1:
                report["stats"]["boundary_edges"] = 1
            return report, triangles, flips

        with patch.object(mesh, "_inspect", side_effect=changed_output):
            report, output = reconstruction.process_reconstruction(binary(cube()[:-1]))
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)
        self.assertEqual(calls, 4)

    def test_invalid_input_limits_and_shared_slot(self):
        nonfinite = cube()
        nonfinite[0, 0, 0] = np.nan
        for raw in (b"", b"broken", binary(nonfinite), binary(np.zeros((1, 3, 3)))):
            report, output = reconstruction.process_reconstruction(raw)
            self.assertEqual(report["status"], "invalid", report)
            self.assertIsNone(output)
            json.dumps(report, allow_nan=False)
        raw = binary(cube()[:-1])
        for setting in ("MAX_RECONSTRUCTION_BYTES", "RECONSTRUCTION_SECONDS"):
            with patch.object(reconstruction, setting, -1):
                report, output = reconstruction.process_reconstruction(raw)
            self.assertEqual(report["status"], "incomplete", report)
            self.assertIsNone(output)
        with mesh._SLOT:
            report, output = reconstruction.process_reconstruction(raw)
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(output)

    def test_surface_voxelization_batches_large_and_small_triangles(self):
        triangle = cube()[:1]
        expected, origin, count = reconstruction._surface_grid(triangle, .1, time.monotonic() + 10, 100000)
        with patch.object(reconstruction, "SAMPLE_BATCH", 2):
            actual, actual_origin, actual_count = reconstruction._surface_grid(triangle, .1, time.monotonic() + 10, 100000)
        np.testing.assert_array_equal(expected, actual)
        np.testing.assert_array_equal(origin, actual_origin)
        self.assertEqual(count, actual_count)


if __name__ == "__main__":
    unittest.main()
