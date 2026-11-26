from __future__ import annotations

import hashlib
import os
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

import app as app_module
from vercel_app import mesh_quality as mesh
from vercel_app.store import PublicBlobStore, StoreError, create_session, load_session, save_session


def tetra(offset=0):
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float) + offset
    return vertices[[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]]


def binary(triangles):
    records = np.zeros(len(triangles), dtype=mesh._RECORD)
    records["vertices"] = np.asarray(triangles).reshape(-1, 3, 3)
    return b"test".ljust(80, b"\0") + struct.pack("<I", len(triangles)) + records.tobytes()


def ascii_stl(triangles):
    parts = ["solid sample\n"]
    for triangle in triangles:
        parts.append("facet normal 0 0 0\nouter loop\n")
        parts.extend("vertex " + " ".join(str(n) for n in vertex) + "\n" for vertex in triangle)
        parts.append("endloop\nendfacet\n")
    return ("".join(parts) + "endsolid sample\n").encode("ascii")


class MeshQualityTests(unittest.TestCase):
    def test_closed_mesh_passes_both_formats(self):
        for data in (binary(tetra()), ascii_stl(tetra())):
            report, output = mesh.process_stl(data)
            self.assertEqual(report["status"], "passed", report)
            self.assertEqual(report["stats"]["boundary_edges"], 0)
            self.assertEqual(report["stats"]["components"], 1)
            self.assertEqual(report["stats"]["dimensions"], [1, 1, 1])
            self.assertIsNone(output)

    def test_binary_header_can_start_with_solid(self):
        raw = binary(tetra())
        raw = b"solid binary".ljust(80, b"\0") + raw[80:]
        self.assertEqual(mesh.process_stl(raw)[0]["status"], "passed")

    def test_hole_is_reported_not_filled(self):
        report, output = mesh.process_stl(binary(tetra()[:-1]), repair=True)
        self.assertEqual(report["stats"]["boundary_edges"], 3)
        self.assertEqual(report["status"], "issues")
        self.assertFalse(report["repairable"])
        self.assertIsNone(output)

    def test_separate_parts_are_not_removed(self):
        raw = binary(np.concatenate([tetra(), tetra(4)]))
        report, output = mesh.process_stl(raw, repair=True)
        self.assertEqual(report["stats"]["components"], 2)
        self.assertEqual(report["stats"]["faces"], 8)
        self.assertIsNone(output)

    def test_duplicates_degenerates_and_winding_repair_preserves_geometry(self):
        faces = tetra()
        faces[1] = faces[1, [0, 2, 1]]
        raw = binary(np.concatenate([faces, faces[:1, [0, 2, 1]], np.zeros((1, 3, 3))]))
        before = bytes(raw)
        report, output = mesh.process_stl(raw, repair=True)
        self.assertEqual(raw, before)
        self.assertEqual(report["stats"]["duplicate_faces"], 1)
        self.assertEqual(report["stats"]["degenerate_faces"], 1)
        self.assertEqual(report["stats"]["repair_flip_faces"], 1)
        self.assertEqual(report["repair"]["report"]["status"], "passed")
        self.assertEqual(len(output), 84 + 4 * 50)
        repaired = mesh._parse(output, time.monotonic() + 5)
        original_points = set(map(tuple, tetra().reshape(-1, 3)))
        self.assertEqual(set(map(tuple, repaired.reshape(-1, 3))), original_points)

    def test_cleanup_does_not_claim_remaining_hole_fixed(self):
        raw = binary(np.concatenate([tetra()[:-1], tetra()[:1]]))
        report, output = mesh.process_stl(raw, repair=True)
        self.assertIsNotNone(output)
        self.assertEqual(report["repair"]["report"]["status"], "issues")
        self.assertEqual(report["repair"]["report"]["stats"]["boundary_edges"], 3)

    def test_nonmanifold_edge_blocks_orientation_repair(self):
        triangles = np.array([[[0, 0, 0], [1, 0, 0], [0, i, j]] for i, j in [(1, 0), (0, 1), (-1, 0)]])
        report, output = mesh.process_stl(binary(triangles), repair=True)
        self.assertEqual(report["stats"]["nonmanifold_edges"], 1)
        self.assertEqual(report["stats"]["components"], 1)
        self.assertEqual(report["stats"]["repair_flip_faces"], 0)
        self.assertIsNone(output)

    def test_tiny_nonzero_faces_are_not_erased(self):
        report, _ = mesh.process_stl(binary(tetra() * 1e-12))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["stats"]["degenerate_faces"], 0)

    def test_small_gaps_are_not_welded(self):
        faces = tetra()
        faces[0] += 0.00001
        report, _ = mesh.process_stl(binary(faces))
        self.assertGreater(report["stats"]["boundary_edges"], 0)

    def test_invalid_files_never_pass_or_get_repaired(self):
        nonfinite = tetra()
        nonfinite[0, 0, 0] = np.nan
        samples = [b"", b"<html>bad gateway</html>", binary(tetra())[:-1],
                   binary(tetra()) + b"unexpected", binary([]), binary(nonfinite),
                   binary(np.zeros((1, 3, 3))), ascii_stl(tetra()).replace(b"endloop", b"badloop", 1)]
        for raw in samples:
            report, output = mesh.process_stl(raw, repair=True)
            self.assertEqual(report["status"], "invalid", report)
            self.assertIsNone(output)

    def test_limits_and_busy_instance_are_incomplete(self):
        raw = binary(tetra())
        for setting, value in [("MAX_CHECK_BYTES", 10), ("MAX_CHECK_FACES", 3), ("CHECK_SECONDS", -1)]:
            with patch.object(mesh, setting, value):
                report, output = mesh.process_stl(raw, repair=True)
                self.assertEqual(report["status"], "incomplete", report)
                self.assertIsNone(output)
        with mesh._SLOT:
            self.assertEqual(mesh.process_stl(raw)[0]["status"], "incomplete")
        self.assertEqual(mesh.process_stl(raw)[0]["status"], "passed")

    def test_high_precision_ascii_never_silently_quantized(self):
        faces = tetra() * 0.123456789012345
        report, output = mesh.process_stl(ascii_stl(np.concatenate([faces, faces[:1]])), repair=True)
        self.assertIsNone(output)
        self.assertFalse(report["repairable"])
        self.assertTrue(any("精度" in issue for issue in report["issues"]))

    def test_all_inward_faces_are_not_silently_flipped_outward(self):
        report, output = mesh.process_stl(binary(tetra()[:, [0, 2, 1]]), repair=True)
        self.assertEqual(report["status"], "passed")
        self.assertIsNone(output)  # Basic consistency is not a volume certificate.

    def test_repair_timeout_does_not_return_unverified_output(self):
        inspect = mesh._inspect
        calls = 0

        def expire_on_recheck(raw, deadline):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise mesh.CheckLimit("复检超时")
            return inspect(raw, deadline)

        with patch.object(mesh, "_inspect", side_effect=expire_on_recheck):
            report, output = mesh.process_stl(binary(np.concatenate([tetra(), tetra()[:1]])), repair=True)
        self.assertEqual(report["status"], "incomplete")
        self.assertIsNone(output)


class MeshWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = {"LOCAL_DATA_DIR": self.tmp.name, "ADMIN_USERNAME": "mesh-admin",
                       "ADMIN_PASSWORD": "mesh-password", "ADMIN_NAME": "测试管理员",
                       **{key: "" for key in ("DATABASE_URL", "NEON_DATABASE_URL", "POSTGRES_URL",
                                              "DATABASE_URL_POOLED", "BLOB_READ_WRITE_TOKEN", "VERCEL")}}
        self.env = patch.dict(os.environ, environment)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/auth/login", json={"username": "mesh-admin", "password": "mesh-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.sid = "a" * 32
        self.token = "mesh-session-token-01234567890123456789"
        self.path = f"/api/sessions/{self.sid}/3d"
        self.headers = {"X-Session-Token": self.token}
        self.raw = binary(np.concatenate([tetra(), tetra()[:1]]))
        self.url = PublicBlobStore().put(f"image3d/{self.sid}/models/stl.stl", self.raw, "model/stl")
        create_session(self.sid, self.token, {
            "session_id": self.sid, "status": "SUCCEEDED", "model_task": {
                "id": "mesh-task", "provider": "meshy", "model": "test", "status": "completed",
                "models": {"stl": {"url": self.url, "size_bytes": len(self.raw),
                                   "sha256": hashlib.sha256(self.raw).hexdigest(), "quality": {"status": "pending"}}},
            },
        })

    def post(self, suffix):
        return self.client.post(self.path + suffix, headers=self.headers)

    def model(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["model_task"]["models"]["stl"]

    def update(self, callback):
        state, version = load_session(self.sid, self.token)
        callback(state)
        save_session(self.sid, state, version)

    def test_check_caches_and_repair_preserves_original(self):
        with patch.object(app_module, "process_stl", wraps=mesh.process_stl) as process:
            model = self.model(self.post("/check"))
            self.assertEqual(model["quality"]["status"], "issues")
            self.model(self.post("/check"))
            self.assertEqual(process.call_count, 1)
            model = self.model(self.post("/repair"))
            self.assertEqual(model["url"], self.url)
            self.assertNotEqual(model["repaired"]["url"], self.url)
            self.assertEqual(model["repaired"]["report"]["status"], "passed")
            self.model(self.post("/repair"))
            self.assertEqual(process.call_count, 2)
            self.assertEqual(app_module.fetch_bytes(self.url), self.raw)

    def test_wrong_token_cannot_read_or_repair(self):
        with patch.object(app_module, "process_stl") as process:
            response = self.client.post(self.path + "/repair", headers={"X-Session-Token": "wrong"})
            self.assertIn(response.status_code, (401, 403, 404))
            process.assert_not_called()

    def test_oversized_file_is_not_downloaded(self):
        self.update(lambda state: state["model_task"]["models"]["stl"].update(size_bytes=mesh.MAX_CHECK_BYTES + 1))
        with patch.object(app_module, "fetch_bytes") as fetch:
            self.assertEqual(self.model(self.post("/check"))["quality"]["status"], "incomplete")
            fetch.assert_not_called()

    def test_changed_content_is_not_repaired(self):
        with patch.object(app_module, "fetch_bytes", return_value=binary(tetra())):
            model = self.model(self.post("/repair"))
        self.assertEqual(model["quality"]["status"], "incomplete")
        self.assertNotIn("repaired", model)
        self.assertNotIn("quality_job", model)

    def test_timeout_and_unexpected_failure_unlock_without_regeneration(self):
        for error in [app_module.WorkflowError("TIMEOUT", "test timeout", 504), RuntimeError("test failure")]:
            with patch.object(app_module, "fetch_bytes", side_effect=error), patch.object(app_module, "query_meshy") as query, \
                    patch.object(app_module.logging, "exception"):
                model = self.model(self.post("/check"))
                self.assertEqual(model["quality"]["status"], "incomplete")
                self.assertNotIn("quality_job", model)
                query.assert_not_called()
        self.assertEqual(self.model(self.post("/check"))["quality"]["status"], "issues")

    def test_busy_lock_and_expired_lock(self):
        self.update(lambda state: state["model_task"]["models"]["stl"].update(quality_job={"started_at": time.time()}))
        self.assertEqual(self.post("/check").status_code, 409)
        self.update(lambda state: state["model_task"]["models"]["stl"].update(quality_job={"started_at": time.time() - 181}))
        self.assertEqual(self.model(self.post("/check"))["quality"]["status"], "issues")

    def test_blob_failure_preserves_report_and_allows_repair_retry(self):
        self.post("/check")
        with patch.object(app_module.PublicBlobStore, "put", side_effect=StoreError("failed")):
            model = self.model(self.post("/repair"))
        self.assertEqual(model["quality"]["status"], "issues")
        self.assertIn("repair_error", model["quality"])
        self.assertNotIn("repaired", model)
        self.assertIn("repaired", self.model(self.post("/repair")))

    def test_completed_poll_does_not_repeat_provider_request(self):
        with patch.object(app_module, "query_meshy") as query:
            response = self.client.get(self.path, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        query.assert_not_called()

    def test_generation_saves_original_and_marks_pending_before_check(self):
        self.update(lambda state: (state.update(status="GENERATING_3D"), state["model_task"].update(models={})))
        with patch.object(app_module, "query_meshy", return_value={"status": "completed", "data": [{"type": "stl", "url": "https://example.invalid/model.stl"}]}), \
                patch.object(app_module, "fetch_bytes", return_value=self.raw), patch.object(app_module, "process_stl") as process:
            model = self.model(self.client.get(self.path, headers=self.headers))
        self.assertEqual(model["quality"]["status"], "pending")
        self.assertEqual(model["sha256"], hashlib.sha256(self.raw).hexdigest())
        process.assert_not_called()

    def test_old_session_without_hash_or_quality_can_be_checked(self):
        self.update(lambda state: state["model_task"]["models"].update(stl={"url": self.url}))
        model = self.model(self.post("/check"))
        self.assertEqual(model["quality"]["status"], "issues")
        self.assertEqual(model["quality"]["sha256"], model["sha256"])

    def test_generation_not_finished_blocks_check(self):
        self.update(lambda state: state.update(status="GENERATING_3D"))
        self.assertEqual(self.post("/check").status_code, 409)

    def test_new_checker_version_invalidates_cache(self):
        self.post("/check")
        self.update(lambda state: state["model_task"]["models"]["stl"]["quality"].update(version="old-version"))
        with patch.object(app_module, "process_stl", wraps=mesh.process_stl) as process:
            self.model(self.post("/check"))
            process.assert_called_once()

    def test_concurrent_session_change_is_not_overwritten(self):
        def change_session(raw, **kwargs):
            self.update(lambda state: state.update(status="VIEWS_REVIEW", model_task=None))
            return mesh.process_stl(raw, **kwargs)

        with patch.object(app_module, "process_stl", side_effect=change_session):
            self.assertEqual(self.post("/check").status_code, 409)
        state, _ = load_session(self.sid, self.token)
        self.assertEqual(state["status"], "VIEWS_REVIEW")
        self.assertIsNone(state["model_task"])


if __name__ == "__main__":
    unittest.main()
