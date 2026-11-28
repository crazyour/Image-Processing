from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

import app as app_module
from vercel_app import mesh_quality
from vercel_app.store import PublicBlobStore, StoreError, create_session, load_session, save_session


class SmoothingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        environment = {"LOCAL_DATA_DIR": self.tmp.name, "ADMIN_USERNAME": "smooth-admin",
                       "ADMIN_PASSWORD": "smooth-password", "ADMIN_NAME": "测试管理员",
                       **{key: "" for key in ("DATABASE_URL", "NEON_DATABASE_URL", "POSTGRES_URL",
                                              "DATABASE_URL_POOLED", "BLOB_READ_WRITE_TOKEN", "VERCEL")}}
        env = patch.dict(os.environ, environment)
        env.start()
        self.addCleanup(env.stop)
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)
        response = self.client.post("/api/auth/login", json={"username": "smooth-admin", "password": "smooth-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.sid = "b" * 32
        self.token = "smooth-session-token-01234567890123456789"
        self.path = f"/api/sessions/{self.sid}/3d"
        self.headers = {"X-Session-Token": self.token}
        vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
        triangles = vertices[[[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]]
        self.raw = mesh_quality._encode(triangles)
        # Workflow tests stub geometry; the algorithm suite checks actual smoothing.
        self.output = mesh_quality._encode(triangles * 0.999)
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.url = PublicBlobStore().put(f"image3d/{self.sid}/models/original.stl", self.raw, "model/stl")
        create_session(self.sid, self.token, {
            "session_id": self.sid, "status": "SUCCEEDED", "model_task": {
                "id": "smooth-task", "provider": "meshy", "status": "completed",
                "models": {"stl": {"url": self.url, "size_bytes": len(self.raw),
                                   "sha256": self.digest, "quality": {"status": "pending"}}},
            },
        })

    def processed(self, raw, *, strength, subdivide):
        self.assertEqual(raw, self.raw)  # Never compound a previous smoothing result.
        return {"version": app_module.SMOOTHER_VERSION, "source_sha256": self.digest,
                "strength": strength, "subdivide": subdivide, "status": "smoothed",
                "message": "已生成平滑副本", "stats": {"faces_before": 4, "faces_after": 4},
                "before": {}, "after": {}, "limitations": [], "elapsed_ms": 1}, self.output

    def post(self, strength="gentle", subdivide=False):
        return self.client.post(self.path + "/smooth", headers=self.headers,
                                json={"strength": strength, "subdivide": subdivide})

    def model(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["model_task"]["models"]["stl"]

    def update(self, callback):
        state, version = load_session(self.sid, self.token)
        callback(state)
        save_session(self.sid, state, version)

    def test_separate_copy_cache_and_each_setting_starts_from_original(self):
        with patch.object(app_module, "process_smoothing", side_effect=self.processed) as process:
            model = self.model(self.post())
            self.assertEqual(model["url"], self.url)
            self.assertEqual(model["quality"], {"status": "pending"})
            self.assertNotEqual(model["smoothed"]["url"], self.url)
            self.assertEqual(model["smoothed"]["source_sha256"], self.digest)
            self.assertEqual(app_module.fetch_bytes(model["smoothed"]["url"]), self.output)
            self.assertEqual(app_module.fetch_bytes(self.url), self.raw)
            self.assertNotIn("quality_job", model)
            self.model(self.post())
            self.assertEqual(process.call_count, 1)
            self.model(self.post("standard", True))
            self.assertEqual(process.call_count, 2)

    def test_invalid_requests_and_unfinished_model_do_not_process(self):
        with patch.object(app_module, "process_smoothing") as process:
            self.assertEqual(self.post("extreme").status_code, 422)
            wrong = self.client.post(self.path + "/smooth", json={}, headers={"X-Session-Token": "wrong"})
            self.assertIn(wrong.status_code, (401, 403, 404))
            self.update(lambda state: state.update(status="GENERATING_3D"))
            self.assertEqual(self.post().status_code, 409)
            process.assert_not_called()

    def test_oversized_and_changed_file_never_processed(self):
        self.update(lambda state: state["model_task"]["models"]["stl"].update(size_bytes=app_module.MAX_SMOOTH_BYTES + 1))
        with patch.object(app_module, "fetch_bytes") as fetch:
            self.assertEqual(self.model(self.post())["smoothing"]["status"], "incomplete")
            fetch.assert_not_called()
        self.update(lambda state: state["model_task"]["models"]["stl"].update(size_bytes=len(self.raw)))
        with patch.object(app_module, "fetch_bytes", return_value=self.output), patch.object(app_module, "process_smoothing") as process:
            model = self.model(self.post())
            self.assertEqual(model["smoothing"]["status"], "incomplete")
            self.assertNotIn("smoothed", model)
            self.assertNotIn("quality_job", model)
            process.assert_not_called()

    def test_failures_preserve_previous_copy_and_can_retry(self):
        with patch.object(app_module, "process_smoothing", side_effect=self.processed):
            previous = copy.deepcopy(self.model(self.post())["smoothed"])
            with patch.object(app_module.PublicBlobStore, "put", side_effect=StoreError("failed")):
                model = self.model(self.post("standard"))
            self.assertEqual(model["smoothed"], previous)
            self.assertEqual(model["smoothing"]["status"], "incomplete")
            self.assertNotIn("quality_job", model)
            # Restore a matching earlier result without doing the work again.
            with patch.object(app_module, "process_smoothing") as process:
                model = self.model(self.post())
                self.assertEqual(model["smoothing"], previous["report"])
                process.assert_not_called()
            self.assertEqual(self.model(self.post("standard"))["smoothing"]["status"], "smoothed")

    def test_rejected_and_unchanged_results_have_no_download(self):
        for status in ("rejected", "unchanged", "incomplete", "invalid"):
            report, _ = self.processed(self.raw, strength="gentle", subdivide=False)
            report.update(status=status, message="未生成副本")
            with patch.object(app_module, "process_smoothing", return_value=(report, None)):
                model = self.model(self.post())
                self.assertEqual(model["smoothing"]["status"], status)
                self.assertNotIn("smoothed", model)
                self.assertEqual(model["url"], self.url)

    def test_unverified_output_is_not_saved(self):
        report, _ = self.processed(self.raw, strength="gentle", subdivide=False)
        report["status"] = "rejected"
        with patch.object(app_module, "process_smoothing", return_value=(report, self.output)), \
                patch.object(app_module.PublicBlobStore, "put") as put:
            self.assertEqual(self.model(self.post())["smoothing"]["status"], "incomplete")
            put.assert_not_called()

    def test_smoothing_lock_blocks_all_mesh_work_for_360_seconds(self):
        self.update(lambda state: state["model_task"]["models"]["stl"].update(
            quality_job={"started_at": time.time() - 300, "operation": "smooth"}))
        for suffix in ("/check", "/repair", "/smooth"):
            response = self.client.post(self.path + suffix, headers=self.headers, json={})
            self.assertEqual(response.status_code, 409, response.text)
        self.update(lambda state: state["model_task"]["models"]["stl"].update(
            quality_job={"started_at": time.time() - 361, "operation": "smooth"}))
        with patch.object(app_module, "process_smoothing", side_effect=self.processed):
            self.assertEqual(self.model(self.post())["smoothing"]["status"], "smoothed")

    def test_check_and_repair_lock_blocks_smoothing(self):
        for operation in ("check", "repair"):
            self.update(lambda state: state["model_task"]["models"]["stl"].update(
                quality_job={"started_at": time.time(), "operation": operation}))
            self.assertEqual(self.post().status_code, 409)

    def test_exception_unlocks_without_provider_call(self):
        with patch.object(app_module, "process_smoothing", side_effect=RuntimeError("test error")), \
                patch.object(app_module.logging, "exception"), patch.object(app_module, "query_meshy") as provider:
            model = self.model(self.post())
            self.assertEqual(model["smoothing"]["status"], "incomplete")
            self.assertNotIn("quality_job", model)
            provider.assert_not_called()

    def test_concurrent_new_session_state_is_not_overwritten(self):
        def concurrent(raw, **kwargs):
            self.update(lambda state: state.update(status="VIEWS_REVIEW", model_task=None))
            return self.processed(raw, **kwargs)

        with patch.object(app_module, "process_smoothing", side_effect=concurrent):
            self.assertEqual(self.post().status_code, 409)
        state, _ = load_session(self.sid, self.token)
        self.assertEqual(state["status"], "VIEWS_REVIEW")
        self.assertIsNone(state["model_task"])

    def test_old_hunyuan_session_and_missing_hash_are_supported(self):
        def legacy(state):
            task = state.pop("model_task")
            task["provider"] = "hunyuan"
            task["models"]["stl"].pop("sha256")
            state["hunyuan_task"] = task

        self.update(legacy)
        with patch.object(app_module, "process_smoothing", side_effect=self.processed):
            model = self.model(self.post())
            self.assertEqual(model["sha256"], self.digest)
        state, _ = load_session(self.sid, self.token)
        self.assertEqual(state["hunyuan_task"], state["model_task"])

    def test_completed_poll_does_not_auto_smooth(self):
        with patch.object(app_module, "process_smoothing") as process:
            self.model(self.client.get(self.path, headers=self.headers))
            process.assert_not_called()

    def test_repair_then_smooth_uses_verified_repair_without_changing_original(self):
        from test_mesh_smoothing import binary, sphere

        triangles = sphere()
        self.raw = binary(np.concatenate((triangles, triangles[:1])))
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.url = PublicBlobStore().put(f"image3d/{self.sid}/models/duplicates.stl", self.raw, "model/stl")
        self.update(lambda state: state["model_task"]["models"].update(
            stl={"url": self.url, "sha256": self.digest, "size_bytes": len(self.raw)}))
        self.assertEqual(self.model(self.post())["smoothing"]["status"], "rejected")
        repaired = self.model(self.client.post(self.path + "/repair", headers=self.headers))["repaired"]
        model = self.model(self.post())
        self.assertEqual(model["smoothing"]["status"], "smoothed", model["smoothing"])
        self.assertEqual(model["smoothed"]["source_kind"], "repaired")
        self.assertEqual(model["smoothed"]["source_sha256"], repaired["sha256"])
        self.assertEqual(model["smoothed"]["original_sha256"], self.digest)
        self.assertEqual(app_module.fetch_bytes(self.url), self.raw)
        self.assertEqual(model["url"], self.url)
        with patch.object(app_module, "process_smoothing") as process:
            self.model(self.post())
            process.assert_not_called()
        # A broken/stale repair association must never replace the source.
        self.update(lambda state: state["model_task"]["models"]["stl"]["repaired"].update(sha256="changed"))
        self.assertEqual(self.model(self.post())["smoothing"]["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
