from __future__ import annotations

import hashlib
import time
import unittest
from unittest.mock import patch

import app as app_module
from vercel_app.mesh_processing import PROCESSOR_VERSION
from vercel_app import mesh_quality as mesh
from vercel_app import mesh_reconstruction as reconstruction
from vercel_app.store import PublicBlobStore, StoreError, load_session
import test_mesh_smoothing_workflow as smoothing_tests
from test_mesh_reconstruction import binary, cube


class ProcessingWorkflowTests(unittest.TestCase):
    setUp = smoothing_tests.SmoothingWorkflowTests.setUp
    model = smoothing_tests.SmoothingWorkflowTests.model
    update = smoothing_tests.SmoothingWorkflowTests.update

    def post(self):
        return self.client.post(self.path + "/process", headers=self.headers)

    def stage_report(self, raw, stage, status="repaired", **extra):
        return {"source_sha256": hashlib.sha256(raw).hexdigest(), "stage": stage,
                "status": status, "message": "test", **extra}

    def test_real_repair_and_smoothing_checkpoint_then_one_final_file(self):
        first = self.model(self.post())
        self.assertEqual(first["processing"]["status"], "pending", first["processing"])
        self.assertEqual(first["processing"]["stage"], "smooth")
        self.assertNotIn("processed", first)
        self.assertNotIn("quality_job", first)
        final = self.model(self.post())
        self.assertEqual(final["processing"]["status"], "completed", final["processing"])
        self.assertEqual(final["processing"]["stage"], "done")
        self.assertEqual(final["processed"]["source_sha256"], self.digest)
        self.assertEqual(final["processed"]["report"]["smoothing"]["status"], "unchanged")
        self.assertNotEqual(final["processed"]["url"], self.url)
        self.assertEqual(app_module.fetch_bytes(final["processed"]["url"]), self.raw)
        self.assertEqual(app_module.fetch_bytes(self.url), self.raw)
        with patch.object(app_module, "process_stage") as process:
            self.assertEqual(self.model(self.post())["processed"], final["processed"])
            process.assert_not_called()

    def test_unrepairable_mesh_automatically_advances_to_reconstruction(self):
        def step(raw, stage):
            if stage == "repair":
                return self.stage_report(raw, stage, "rejected"), None, "reconstruct"
            if stage == "reconstruct":
                return self.stage_report(raw, stage, method="voxel_reconstruction"), self.output, "smooth"
            self.assertEqual(raw, self.output)
            return self.stage_report(raw, stage, "unchanged"), raw, "done"

        with patch.object(app_module, "process_stage", side_effect=step) as process, \
                patch.object(app_module, "submit_meshy") as paid:
            first = self.model(self.post())
            self.assertEqual(first["processing"]["stage"], "reconstruct")
            self.assertNotIn("processed", first)
            second = self.model(self.post())
            self.assertEqual(second["processing"]["stage"], "smooth")
            self.assertNotIn("processed", second)
            final = self.model(self.post())
            self.assertTrue(final["processed"]["reconstructed"])
            self.assertEqual(len(final["processing"]["attempts"]), 3)
            self.assertIn("重建", final["processing"]["message"])
            self.assertEqual(process.call_count, 3)
            paid.assert_not_called()

    def test_real_open_surface_advances_through_reconstruction_and_smoothing(self):
        # An open sheet is outside the local hole-filling budget.
        raw = binary(cube()[:2])
        digest = hashlib.sha256(raw).hexdigest()
        url = PublicBlobStore().put(f"image3d/{self.sid}/models/open.stl", raw, "model/stl")
        self.update(lambda state: state["model_task"]["models"].update(
            stl={"url": url, "sha256": digest, "size_bytes": len(raw)}))
        with patch.object(reconstruction, "ATTEMPT_SETTINGS", ((16, 0), (12, 1), (8, 2))):
            first = self.model(self.post())
            self.assertEqual(first["processing"]["stage"], "reconstruct", first["processing"])
            second = self.model(self.post())
            self.assertEqual(second["processing"]["stage"], "smooth", second["processing"])
            final = self.model(self.post())
            for _ in range(2):
                if final["processing"]["status"] != "pending":
                    break
                final = self.model(self.post())
        self.assertEqual(final["processing"]["status"], "completed", final["processing"])
        self.assertTrue(final["processed"]["reconstructed"])
        self.assertEqual(final["processed"]["source_sha256"], digest)
        self.assertNotEqual(app_module.fetch_bytes(final["processed"]["url"]), raw)
        self.assertEqual(app_module.fetch_bytes(url), raw)
        self.assertIn("smooth_cautious", [item["stage"] for item in final["processing"]["attempts"]])
        stats = final["processed"]["report"]["smoothing"]["after"]["stats"]
        self.assertTrue(stats["edge_closed"])

    def test_busy_worker_waits_without_consuming_repair_options(self):
        with mesh._SLOT:
            model = self.model(self.post())
        self.assertEqual(model["processing"]["stage"], "repair")
        self.assertEqual(model["processing"]["status"], "pending")
        self.assertGreater(model["processing"]["retry_after"], time.time())
        self.assertEqual(model["processing"]["attempts"], [])
        resumed = self.model(self.post())
        self.assertEqual(resumed["processing"]["stage"], "smooth")
        self.assertNotIn("retry_after", resumed["processing"])

    def test_smoothing_changes_strategy_but_never_compounds_failed_geometry(self):
        seen = []

        def step(raw, stage):
            seen.append(stage)
            if stage == "repair":
                return self.stage_report(raw, stage), self.output, "smooth"
            self.assertEqual(raw, self.output)
            if stage == "smooth":
                return self.stage_report(raw, stage, "rejected"), None, "smooth_cautious"
            if stage == "smooth_cautious":
                return self.stage_report(raw, stage, "rejected"), None, "smooth_minimal"
            return self.stage_report(raw, stage, "unchanged"), raw, "done"

        with patch.object(app_module, "process_stage", side_effect=step):
            for _ in range(3):
                model = self.model(self.post())
                self.assertEqual(model["processing"]["status"], "pending")
                self.assertNotIn("processed", model)
            self.assertEqual(self.model(self.post())["processing"]["status"], "completed")
        self.assertEqual(seen, ["repair", "smooth", "smooth_cautious", "smooth_minimal"])

    def test_failed_attempts_do_not_expose_an_unprocessed_file_as_final(self):
        with patch.object(app_module, "process_stage", return_value=(self.stage_report(self.raw, "repair", "invalid"), None, None)):
            model = self.model(self.post())
        self.assertEqual(model["processing"]["status"], "failed")
        self.assertNotIn("processed", model)
        self.assertEqual(model["url"], self.url)
        self.assertNotIn("quality_job", model)
        self.assertEqual(self.model(self.post())["processing"]["stage"], "smooth")

    def test_wrong_token_missing_model_and_busy_request_never_process(self):
        with patch.object(app_module, "process_stage") as process:
            wrong = self.client.post(self.path + "/process", headers={"X-Session-Token": "wrong"})
            self.assertIn(wrong.status_code, (401, 403, 404))
            self.update(lambda state: state["model_task"]["models"]["stl"].update(
                quality_job={"operation": "process", "started_at": time.time() - 300}))
            self.assertEqual(self.post().status_code, 409)
            for operation in ("check", "repair", "smooth"):
                response = self.client.post(self.path + "/" + operation, headers=self.headers, json={})
                self.assertEqual(response.status_code, 409)
            self.update(lambda state: state.update(status="GENERATING_3D"))
            self.assertEqual(self.post().status_code, 409)
            process.assert_not_called()

    def test_checkpoint_survives_reload_and_expired_lock(self):
        self.model(self.post())
        self.update(lambda state: state["model_task"]["models"]["stl"].update(
            quality_job={"operation": "process", "started_at": time.time() - 361}))
        response = self.client.get(f"/api/sessions/{self.sid}", headers=self.headers)
        self.assertEqual(self.model(response)["processing"]["stage"], "smooth")
        self.assertEqual(self.model(self.post())["processing"]["status"], "completed")

    def test_changed_working_content_cannot_be_delivered(self):
        self.model(self.post())
        with patch.object(app_module, "fetch_bytes", return_value=self.output), \
                patch.object(app_module, "process_stage") as process:
            model = self.model(self.post())
        self.assertEqual(model["processing"]["status"], "failed")
        self.assertNotIn("processed", model)
        process.assert_not_called()

    def test_final_upload_failure_does_not_claim_completion(self):
        self.model(self.post())
        with patch.object(app_module.PublicBlobStore, "put", side_effect=StoreError("failed")):
            model = self.model(self.post())
        self.assertEqual(model["processing"]["status"], "failed")
        self.assertNotIn("processed", model)
        self.assertNotIn("quality_job", model)
        self.assertTrue(model["processing"]["retryable"])
        self.assertEqual(model["processing"]["stage"], "smooth")
        # Retain the repaired checkpoint and retry only the final stage.
        final = self.model(self.post())
        self.assertEqual(final["processing"]["status"], "completed")
        self.assertEqual([item["stage"] for item in final["processing"]["attempts"]],
                         ["repair", "smooth", "smooth"])

    def test_concurrent_session_change_is_not_overwritten(self):
        def changed(raw, stage):
            self.update(lambda state: state.update(status="VIEWS_REVIEW", model_task=None))
            return self.stage_report(raw, stage), raw, "smooth"

        with patch.object(app_module, "process_stage", side_effect=changed):
            self.assertEqual(self.post().status_code, 409)
        state, _ = load_session(self.sid, self.token)
        self.assertEqual(state["status"], "VIEWS_REVIEW")
        self.assertIsNone(state["model_task"])

    def test_invalid_old_pipeline_version_restarts_from_original(self):
        self.model(self.post())
        self.update(lambda state: state["model_task"]["models"]["stl"]["processing"].update(version="old"))
        with patch.object(app_module, "process_stage", return_value=(self.stage_report(self.raw, "repair"), self.raw, "smooth")) as process:
            model = self.model(self.post())
        process.assert_called_once_with(self.raw, "repair")
        self.assertEqual(model["processing"]["version"], PROCESSOR_VERSION)

    def test_generation_marks_processing_pending_without_paying_for_another_model(self):
        self.update(lambda state: (state.update(status="GENERATING_3D"), state["model_task"].update(models={}, model="meshy-7.1")))
        with patch.object(app_module, "query_meshy", return_value={"status": "completed", "data": [{"type": "stl", "url": "https://example.invalid/model.stl"}]}), \
                patch.object(app_module, "fetch_bytes", return_value=self.raw), patch.object(app_module, "process_stage") as process:
            model = self.model(self.client.get(self.path, headers=self.headers))
        self.assertEqual(model["processing"]["status"], "pending")
        self.assertEqual(model["processing"]["stage"], "repair")
        self.assertNotIn("processed", model)
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
