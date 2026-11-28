from __future__ import annotations

import hashlib
import struct
import time
import unittest
from unittest.mock import patch

import numpy as np

from vercel_app import mesh_quality as mesh
from vercel_app import mesh_repair as repair
from vercel_app import mesh_smoothing as smoothing


def binary(triangles):
    records = np.zeros(len(triangles), dtype=mesh._RECORD)
    records["vertices"] = triangles
    return b"test repair".ljust(80, b"\0") + struct.pack("<I", len(triangles)) + records.tobytes()


def tetra():
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    return points[[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]]


def sphere(levels=4):
    points = np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]], dtype=float)
    faces = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4],
                      [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]], dtype=np.int32)
    for _ in range(levels):
        data = smoothing._edges(faces, time.monotonic() + 10)
        points, faces, _ = smoothing._subdivide(points, faces, data,
                                                np.zeros(len(points), bool), np.zeros(len(data["counts"]), bool))
        points /= np.linalg.norm(points, axis=1)[:, None]
    return points[faces]


class MeshRepairTests(unittest.TestCase):
    def assert_verified(self, report, output):
        self.assertIn(report["status"], ("repaired", "unchanged"), report)
        self.assertIsNotNone(output)
        after, _, _ = mesh._inspect(output, time.monotonic() + 10)
        for key in ("boundary_edges", "nonmanifold_edges", "inconsistent_edges", "duplicate_faces", "degenerate_faces"):
            self.assertEqual(after["stats"][key], 0, report)
        self.assertEqual(hashlib.sha256(output).hexdigest(), report["after"]["sha256"])

    def test_closed_valid_mesh_passes_through_without_rewriting(self):
        raw = binary(tetra())
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["status"], "unchanged")
        self.assertIs(output, raw)

    def test_duplicate_degenerate_and_winding_are_repaired(self):
        triangles = tetra()
        triangles[0] = triangles[0, [0, 2, 1]]
        raw = binary(np.concatenate((triangles, triangles[:1], np.zeros((1, 3, 3)))))
        original = bytes(raw)
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["status"], "repaired")
        self.assertEqual(report["stats"]["removed_duplicate_faces"], 1)
        self.assertEqual(report["stats"]["removed_degenerate_faces"], 1)
        self.assertEqual(report["stats"]["faces_after"], 4)
        self.assertEqual(raw, original)

    def test_small_triangle_hole_is_closed_and_original_vertices_preserved(self):
        triangles = sphere()
        raw = binary(triangles[1:])
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["stats"]["filled_holes"], 1)
        self.assertEqual(report["stats"]["added_faces"], 1)
        before_points = np.unique(mesh._parse(raw, time.monotonic() + 10).reshape(-1, 3), axis=0)
        after_points = np.unique(mesh._parse(output, time.monotonic() + 10).reshape(-1, 3), axis=0)
        np.testing.assert_array_equal(before_points, after_points)

    def test_small_quad_hole_is_triangulated_with_consistent_winding(self):
        triangles = sphere()
        raw = binary(np.delete(triangles, [0, 3], axis=0))
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["stats"]["filled_holes"], 1)
        self.assertEqual(report["stats"]["added_faces"], 2)

    def test_small_nonmanifold_fin_is_removed(self):
        base = tetra()
        fin = np.array([[[0, 0, 0], [1, 0, 0], [.5, -.001, 0]]])
        raw = binary(np.concatenate((base, fin)))
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["stats"]["removed_conflict_faces"], 1)
        self.assertEqual(report["stats"]["faces_after"], 4)

    def test_nonmanifold_cleanup_and_hole_filling_can_run_in_same_attempt(self):
        base = sphere()
        a, b, c = base[100]
        tiny = (a + b) / 2 + (c - (a + b) / 2) * .001
        raw = binary(np.concatenate((base[1:], [[a, b, tiny]])))
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["stats"]["removed_conflict_faces"], 1)
        self.assertEqual(report["stats"]["filled_holes"], 1)

    def test_every_original_component_is_preserved_including_small_parts(self):
        base = sphere()[1:]
        small_part = tetra() * .01 + 3
        raw = binary(np.concatenate((base, small_part)))
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["before"]["stats"]["components"], 2)
        self.assertEqual(report["after"]["stats"]["components"], 2)
        after_points = set(map(tuple, mesh._parse(output, time.monotonic() + 10).reshape(-1, 3)))
        for point in small_part.astype(np.float32).reshape(-1, 3):
            self.assertIn(tuple(point), after_points)

    def test_large_hole_requests_upgrade_and_never_delivers_partial_cleanup(self):
        raw = binary(np.concatenate((tetra()[:-1], tetra()[:1])))
        report, output = repair.process_repair(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)
        self.assertIn("升级", report["message"])
        self.assertEqual(report["attempts"][-1]["status"], "rejected")

    def test_per_component_budget_does_not_destroy_small_bad_component(self):
        base = tetra() * .001 + 4
        fin = np.array([[[0, 0, 0], [1, 0, 0], [.5, -.5, 0]]]) * .001 + 4
        raw = binary(np.concatenate((sphere(), base, fin)))
        report, output = repair.process_repair(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)

    def test_vertex_only_pinched_shells_are_rejected(self):
        raw = binary(np.concatenate((tetra(), -tetra())))
        report, output = repair.process_repair(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)
        self.assertIn("顶点", report["message"])

    def test_unchanged_ascii_precision_is_preserved(self):
        triangles = tetra() * .1234567890123
        text = "solid exact\n"
        for triangle in triangles:
            text += "facet normal 0 0 0\nouter loop\n"
            for vertex in triangle:
                text += "vertex " + " ".join(map(repr, map(float, vertex))) + "\n"
            text += "endloop\nendfacet\n"
        raw = (text + "endsolid exact\n").encode()
        report, output = repair.process_repair(raw)
        self.assert_verified(report, output)
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(output, raw)

    def test_corrupt_export_is_rechecked_before_returning(self):
        triangles = tetra()
        raw = binary(np.concatenate((triangles, triangles[:1])))
        with patch.object(mesh, "_encode", return_value=binary(triangles[:-1])):
            report, output = repair.process_repair(raw)
        self.assertEqual(report["status"], "rejected", report)
        self.assertIsNone(output)
        self.assertIn("复检", report["message"])

    def test_invalid_input_and_limits_do_not_return_files(self):
        for raw in (b"invalid", binary(tetra())[:-1], binary(np.zeros((1, 3, 3)))):
            report, output = repair.process_repair(raw)
            self.assertEqual(report["status"], "invalid", report)
            self.assertIsNone(output)
        with patch.object(repair, "MAX_REPAIR_FACES", 3), patch.object(mesh, "_inspect") as inspect:
            report, output = repair.process_repair(binary(tetra()))
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(output)
        inspect.assert_not_called()
        with patch.object(repair, "REPAIR_SECONDS", -1):
            report, output = repair.process_repair(binary(tetra()))
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(output)

    def test_processing_slot_is_shared_and_released(self):
        mesh._SLOT.acquire()
        try:
            report, output = repair.process_repair(binary(tetra()))
        finally:
            mesh._SLOT.release()
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(output)
        self.assert_verified(*repair.process_repair(binary(tetra())))


if __name__ == "__main__":
    unittest.main()
