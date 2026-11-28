from __future__ import annotations

import hashlib
import struct
import time
import unittest
from unittest.mock import patch

import numpy as np

from vercel_app import mesh_quality as mesh
from vercel_app import mesh_smoothing as smoothing


def binary(triangles):
    records = np.zeros(len(triangles), dtype=mesh._RECORD)
    records["vertices"] = np.asarray(triangles).reshape(-1, 3, 3)
    return b"test".ljust(80, b"\0") + struct.pack("<I", len(triangles)) + records.tobytes()


def ascii_stl(triangles):
    parts = ["solid smoothing\n"]
    for triangle in triangles:
        parts.append("facet normal 0 0 0\nouter loop\n")
        parts.extend("vertex " + " ".join(repr(float(x)) for x in vertex) + "\n" for vertex in triangle)
        parts.append("endloop\nendfacet\n")
    return ("".join(parts) + "endsolid smoothing\n").encode("ascii")


def tetra():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    return vertices[[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]]


def sphere(noise=.015, levels=3):
    vertices = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
    faces = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4],
                      [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]], dtype=np.int32)
    for _ in range(levels):
        data = smoothing._edges(faces, time.monotonic() + 10)
        vertices, faces, _ = smoothing._subdivide(vertices, faces, data,
                                                  np.zeros(len(vertices), bool), np.zeros(len(data["counts"]), bool))
        vertices /= np.linalg.norm(vertices, axis=1)[:, None]
    radii = 1 + noise * np.random.default_rng(42).uniform(-1, 1, len(vertices))
    vertices *= radii[:, None]
    return vertices[faces]


def patch_mesh(size=7):
    vertices = np.array([[x, y, .4 * x + .2 * y + (.14 * (-1) ** (x + y) if 0 < x < size - 1 and 0 < y < size - 1 else 0)]
                         for y in range(size) for x in range(size)], dtype=float)
    faces = []
    for y in range(size - 1):
        for x in range(size - 1):
            a = y * size + x
            faces.extend([[a, a + 1, a + size], [a + 1, a + size + 1, a + size]])
    return vertices[np.asarray(faces)]


def points(raw):
    return np.unique(mesh._parse(raw, time.monotonic() + 10).reshape(-1, 3), axis=0)


class MeshSmoothingTests(unittest.TestCase):
    def test_preserving_mode_smooths_sound_surface_without_erasing_a_nonmanifold_fin(self):
        surface = sphere()
        a, b = surface[0, :2]
        fin = np.array([[a, b, (a + b) / 2 * 1.025]])
        raw = binary(np.concatenate((surface, fin, tetra() * .02 + 4)))
        before, triangles, _ = mesh._inspect(raw, time.monotonic() + 10)
        vertices, inverse = np.unique(triangles.reshape(-1, 3), axis=0, return_inverse=True)
        faces = inverse.reshape(-1, 3).astype(np.int32)
        data = smoothing._edges(faces, time.monotonic() + 10)
        bad = smoothing._vertex_fan_mask(faces, data, time.monotonic() + 10)
        bad[data["vertices"][data["counts"] > 2].ravel()] = True
        bad[data["vertices"][np.any(bad[data["vertices"]], axis=1)].ravel()] = True

        rejected, _ = smoothing.process_smoothing(raw)
        self.assertEqual(rejected["status"], "rejected")
        report, output = smoothing.process_smoothing(raw, preserve_defects=True)
        self.assertEqual(report["status"], "smoothed", report)
        self.assertTrue(report["needs_review"])
        self.assertEqual(report["method"], "surface_preserving")
        self.assertTrue(report["stats"]["connectivity_preserved"])
        self.assertGreater(report["stats"]["moved_vertices"], 0)
        # Only the independent tetrahedron is a reliable closed volume; the
        # sphere region touches an overused edge shared with its attached fin.
        self.assertEqual(report["stats"]["volume_checked_components"], 1)
        after_triangles = mesh._parse(output, time.monotonic() + 10)
        # Exact face order/connection correspondence; defective corners never move.
        np.testing.assert_array_equal(after_triangles[bad[faces]], triangles[bad[faces]])
        for key in ("faces", "components", "nonmanifold_edges", "boundary_edges", "inconsistent_edges"):
            self.assertEqual(report["after"]["stats"][key], before["stats"][key])

    def test_preserving_mode_never_thickens_or_resamples_an_open_sheet(self):
        raw = binary(patch_mesh())
        report, output = smoothing.process_smoothing(raw, preserve_defects=True)
        self.assertEqual(report["status"], "smoothed", report)
        self.assertTrue(report["needs_review"])
        self.assertEqual(report["before"]["stats"]["faces"], report["after"]["stats"]["faces"])
        self.assertEqual(report["before"]["stats"]["boundary_edges"], report["after"]["stats"]["boundary_edges"])
        invalid, output = smoothing.process_smoothing(raw, preserve_defects=True, subdivide=True)
        self.assertEqual(invalid["status"], "invalid")
        self.assertIsNone(output)

    def test_smoothing_changes_real_surface_and_preserves_original_and_topology(self):
        raw = binary(sphere())
        original = bytes(raw)
        report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "smoothed", report)
        self.assertIsNotNone(output)
        self.assertEqual(raw, original)
        self.assertEqual(report["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertGreater(report["stats"]["moved_vertices"], 0)
        before_radii = np.linalg.norm(points(raw), axis=1)
        after_radii = np.linalg.norm(points(output), axis=1)
        self.assertLess(np.std(after_radii), np.std(before_radii) * .85)
        self.assertFalse(np.array_equal(points(output), points(raw)))
        for key in ("faces", "components", "boundary_edges", "nonmanifold_edges", "inconsistent_edges"):
            self.assertEqual(report["before"]["stats"][key], report["after"]["stats"][key])
        self.assertEqual(report["after"]["status"], "passed")
        self.assertLessEqual(report["stats"]["max_displacement"], report["stats"]["max_allowed_displacement"])
        self.assertLessEqual(report["stats"]["dimension_change_ratio"], .01)
        self.assertLessEqual(report["stats"]["volume_change_ratio"], .025)

    def test_standard_is_a_bounded_stronger_option(self):
        raw = binary(sphere(noise=.025))
        gentle, gentle_raw = smoothing.process_smoothing(raw)
        standard, standard_raw = smoothing.process_smoothing(raw, strength="standard")
        self.assertEqual(gentle["status"], "smoothed", gentle)
        self.assertEqual(standard["status"], "smoothed", standard)
        self.assertGreater(standard["stats"]["max_displacement"], gentle["stats"]["max_displacement"])
        self.assertLess(np.std(np.linalg.norm(points(standard_raw), axis=1)), np.std(np.linalg.norm(points(gentle_raw), axis=1)))

    def test_sharp_corners_and_separate_components_preserved(self):
        sharp = tetra() + 4
        raw = binary(np.concatenate([sphere(), sharp]))
        report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "smoothed", report)
        self.assertEqual(report["before"]["stats"]["components"], 2)
        self.assertEqual(report["after"]["stats"]["components"], 2)
        exported = set(map(tuple, points(output)))
        for vertex in sharp.reshape(-1, 3):
            self.assertIn(tuple(vertex), exported)

    def test_open_boundary_is_frozen_but_interior_smoothed(self):
        raw = binary(patch_mesh())
        report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "smoothed", report)
        before = points(raw)
        boundary = before[(before[:, 0] == 0) | (before[:, 0] == 6) | (before[:, 1] == 0) | (before[:, 1] == 6)]
        after = set(map(tuple, points(output)))
        self.assertTrue(all(tuple(vertex) in after for vertex in boundary))
        self.assertEqual(report["before"]["stats"]["boundary_edges"], report["after"]["stats"]["boundary_edges"])
        after_points = points(output)
        before_residual = before[:, 2] - .4 * before[:, 0] - .2 * before[:, 1]
        after_residual = after_points[:, 2] - .4 * after_points[:, 0] - .2 * after_points[:, 1]
        self.assertLess(np.std(after_residual), np.std(before_residual))

    def test_fully_sharp_model_is_unchanged(self):
        report, output = smoothing.process_smoothing(binary(tetra()))
        self.assertEqual(report["status"], "unchanged", report)
        self.assertIsNone(output)
        self.assertEqual(report["stats"]["moved_vertices"], 0)

    def test_zero_effective_movement_does_not_deliver_quantized_ascii_copy(self):
        with patch.object(smoothing, "_taubin", side_effect=lambda vertices, *args: vertices.copy()):
            report, output = smoothing.process_smoothing(ascii_stl(sphere()))
        self.assertEqual(report["status"], "unchanged", report)
        self.assertIsNone(output)

    def test_subdivision_is_exactly_four_times_faces_then_moves_geometry(self):
        raw = binary(sphere(levels=2))
        report, output = smoothing.process_smoothing(raw, subdivide=True)
        self.assertEqual(report["status"], "smoothed", report)
        self.assertEqual(report["stats"]["faces_after"], 4 * report["stats"]["faces_before"])
        self.assertEqual(len(output), 84 + 50 * report["stats"]["faces_after"])
        self.assertGreater(report["stats"]["moved_vertices"], 0)
        self.assertLessEqual(report["stats"]["dimension_change_ratio"], .01)

    def test_subdivision_budget_rejected_before_work_and_never_decimates(self):
        raw = binary(sphere(levels=2))
        with patch.object(smoothing, "MAX_SMOOTH_FACES", 200), patch.object(mesh, "_inspect") as inspect:
            report, output = smoothing.process_smoothing(raw, subdivide=True)
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(output)
        inspect.assert_not_called()

    def test_dimension_and_volume_safety_gates(self):
        for key in ("dimension_limit", "volume_limit"):
            config = {**smoothing._SETTINGS["gentle"], key: 0}
            with patch.dict(smoothing._SETTINGS, {"gentle": config}):
                report, output = smoothing.process_smoothing(binary(sphere()))
            self.assertEqual(report["status"], "rejected", report)
            self.assertIsNone(output)

    def test_open_sheet_does_not_exempt_closed_component_from_volume_check(self):
        sheet = np.array([[[3, 0, 0], [3, 1, 0], [3, 0, 1]]], dtype=float)
        raw = binary(np.concatenate([sphere(), sheet]))
        report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "smoothed", report)
        self.assertFalse(report["before"]["stats"]["edge_closed"])
        self.assertEqual(report["stats"]["volume_checked_components"], 1)
        self.assertGreater(report["stats"]["volume_change_ratio"], 0)
        config = {**smoothing._SETTINGS["gentle"], "volume_limit": 0}
        with patch.dict(smoothing._SETTINGS, {"gentle": config}):
            report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIn("体积", report["message"])
        self.assertIsNone(output)

    def test_malformed_and_nonfinite_are_invalid(self):
        invalid = sphere()
        invalid[0, 0, 0] = np.nan
        for raw in (b"", b"not stl", binary(sphere())[:-1], binary(invalid), binary(np.zeros((1, 3, 3)))):
            report, output = smoothing.process_smoothing(raw)
            self.assertEqual(report["status"], "invalid", report)
            self.assertIsNone(output)

    def test_abnormal_topology_is_rejected_without_cleanup(self):
        base = sphere()
        flipped = base.copy()
        flipped[0] = flipped[0, [0, 2, 1]]
        nonmanifold = np.array([[[0, 0, 0], [1, 0, 0], [0, i, j]] for i, j in [(1, 0), (0, 1), (-1, 0)]])
        for triangles in (np.concatenate([base, base[:1]]), np.concatenate([base, np.zeros((1, 3, 3))]), flipped, nonmanifold):
            report, output = smoothing.process_smoothing(binary(triangles))
            self.assertEqual(report["status"], "rejected", report)
            self.assertIsNone(output)

    def test_pinched_vertex_is_rejected_even_without_nonmanifold_edge(self):
        # Two tetrahedra share only the origin; each edge still has two faces.
        raw = binary(np.concatenate([tetra(), -tetra()[:, [0, 2, 1]]]))
        report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIn("非流形顶点", report["message"])
        self.assertIsNone(output)

    def test_high_precision_frozen_ascii_points_are_never_changed(self):
        raw = ascii_stl(np.concatenate([sphere(), tetra() + 4.123456789012345]))
        report, output = smoothing.process_smoothing(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIn("精度", report["message"])
        self.assertIsNone(output)

    def test_ascii_quantization_cannot_overwhelm_smoothing(self):
        report, output = smoothing.process_smoothing(ascii_stl(sphere() + 1e6))
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)

    def test_limits_invalid_options_and_shared_busy_slot(self):
        raw = binary(sphere())
        for setting, value in (("MAX_SMOOTH_BYTES", 10), ("MAX_SMOOTH_FACES", 3), ("SMOOTH_SECONDS", -1)):
            with patch.object(smoothing, setting, value):
                report, output = smoothing.process_smoothing(raw)
                self.assertEqual(report["status"], "incomplete", report)
                self.assertIsNone(output)
        with mesh._SLOT:
            report, output = smoothing.process_smoothing(raw)
            self.assertEqual(report["status"], "incomplete")
            self.assertIsNone(output)
        for kwargs in ({"strength": "high"}, {"subdivide": 1}, {"strength": []}):
            report, output = smoothing.process_smoothing(raw, **kwargs)
            self.assertEqual(report["status"], "invalid")
            self.assertIsNone(output)
        self.assertEqual(smoothing.process_smoothing(raw)[0]["status"], "smoothed")

    def test_timeout_during_export_recheck_returns_no_file(self):
        inspect = mesh._inspect
        calls = 0

        def inspect_or_expire(raw, deadline):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise mesh.CheckLimit("复检超时")
            return inspect(raw, deadline)

        with patch.object(mesh, "_inspect", side_effect=inspect_or_expire):
            report, output = smoothing.process_smoothing(binary(sphere()))
        self.assertEqual(report["status"], "incomplete", report)
        self.assertIsNone(output)
        self.assertEqual(calls, 2)

    def test_failed_export_recheck_returns_no_file(self):
        inspect = mesh._inspect
        calls = 0

        def inspect_or_damage(raw, deadline):
            nonlocal calls
            calls += 1
            report, triangles, flips = inspect(raw, deadline)
            if calls == 2:
                report["stats"]["components"] += 1
            return report, triangles, flips

        with patch.object(mesh, "_inspect", side_effect=inspect_or_damage):
            report, output = smoothing.process_smoothing(binary(sphere()))
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)
        self.assertEqual(calls, 2)

    def test_report_contract_is_json_serializable_on_every_status(self):
        import json
        cases = [smoothing.process_smoothing(binary(sphere())), smoothing.process_smoothing(binary(tetra())),
                 smoothing.process_smoothing(b"bad")]
        fields = {"version", "source_sha256", "strength", "subdivide", "status", "message", "stats",
                  "before", "after", "limitations", "elapsed_ms"}
        for report, _ in cases:
            self.assertEqual(set(report), fields)
            json.dumps(report, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
