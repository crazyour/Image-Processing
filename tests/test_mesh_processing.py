from __future__ import annotations

import unittest
import hashlib
from unittest.mock import patch

import numpy as np

from vercel_app import mesh_quality as mesh
from vercel_app.mesh_processing import STAGES, process_stage
from vercel_app import mesh_processing as processing
from test_mesh_repair import binary, tetra
from test_mesh_smoothing import sphere, points


class MeshProcessingBusyTests(unittest.TestCase):
    def test_worker_contention_preserves_every_processing_stage(self):
        raw = binary(tetra())
        with mesh._SLOT:
            for stage in sorted(STAGES):
                with self.subTest(stage=stage):
                    report, output, next_stage = process_stage(raw, stage)
                    self.assertEqual(report["code"], "MESH_BUSY")
                    self.assertEqual(report["status"], "incomplete")
                    self.assertIsNone(output)
                    self.assertEqual(next_stage, stage)

    def test_repair_resumes_normally_after_other_worker_releases_slot(self):
        raw = binary(tetra())
        with mesh._SLOT:
            _, _, next_stage = process_stage(raw, "repair")
        report, output, next_stage = process_stage(raw, next_stage)
        self.assertEqual(report["status"], "unchanged")
        self.assertEqual(output, raw)
        self.assertEqual(next_stage, "smooth")
        self.assertNotIn("code", report)


class MeshProcessingTests(unittest.TestCase):
    def test_real_surface_is_repaired_then_smoothed(self):
        raw = binary(sphere())
        source_hash = hashlib.sha256(raw).hexdigest()
        repair, working, stage = process_stage(raw, "repair")
        self.assertEqual(stage, "smooth", repair)
        report, final, stage = process_stage(working, stage)
        self.assertEqual(stage, "done", report)
        self.assertEqual(report["status"], "smoothed")
        self.assertGreater(report["stats"]["moved_vertices"], 0)
        self.assertLess(np.std(np.linalg.norm(points(final), axis=1)),
                        np.std(np.linalg.norm(points(raw), axis=1)) * .85)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), source_hash)
        for key in processing._STRUCTURAL_PROBLEMS:
            self.assertEqual(report["after"]["stats"][key], 0)

    def test_all_three_smoothing_strengths_are_used_without_compounding(self):
        raw = binary(tetra())
        report = {"source_sha256": hashlib.sha256(raw).hexdigest(), "status": "rejected"}
        with patch.object(processing, "process_smoothing", side_effect=lambda *args, **kwargs: (dict(report), None)) as smooth:
            stage = "smooth"
            for strength, next_stage in (("gentle", "smooth_cautious"), ("cautious", "smooth_minimal"), ("minimal", None)):
                _, output, stage = process_stage(raw, stage)
                self.assertIsNone(output)
                self.assertEqual(stage, next_stage)
                smooth.assert_called_with(raw, strength=strength, subdivide=False, preserve_defects=True)

    def test_unverified_output_is_discarded_before_fallback(self):
        raw = binary(tetra())
        invalid = {"source_sha256": hashlib.sha256(raw).hexdigest(), "status": "smoothed"}
        with patch.object(processing, "process_smoothing", return_value=(invalid, raw)):
            report, output, stage = process_stage(raw, "smooth")
        self.assertEqual(report["code"], "STL_OUTPUT_UNVERIFIED")
        self.assertIsNone(output)
        self.assertEqual(stage, "smooth_cautious")

    def test_unchanged_must_still_have_verified_closed_geometry(self):
        raw = binary(tetra())
        report = {"source_sha256": hashlib.sha256(raw).hexdigest(), "status": "unchanged",
                  "before": {"sha256": hashlib.sha256(raw).hexdigest(),
                             "stats": {"faces": 4, **{key: 0 for key in processing._STRUCTURAL_PROBLEMS}}}}
        with patch.object(processing, "process_smoothing", return_value=(report, None)):
            _, output, stage = process_stage(raw, "smooth")
            self.assertEqual(output, raw)
            self.assertEqual(stage, "done")
            report["before"]["stats"]["boundary_edges"] = 1
            _, output, stage = process_stage(raw, "smooth")
        self.assertIsNone(output)
        self.assertEqual(stage, "smooth_cautious")

    def test_wrong_source_cannot_complete_or_fallback_as_same_model(self):
        with patch.object(processing, "process_smoothing", return_value=({"source_sha256": "other", "status": "unchanged"}, None)):
            with self.assertRaisesRegex(ValueError, "source digest"):
                process_stage(binary(tetra()), "smooth")


if __name__ == "__main__":
    unittest.main()
