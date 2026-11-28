from __future__ import annotations

import copy
import hashlib
import unittest
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import app as app_module
from vercel_app.store import PublicBlobStore, load_session
import test_mesh_smoothing_workflow as smoothing_tests


class ProcessingDisabledWorkflowTests(unittest.TestCase):
    setUp = smoothing_tests.SmoothingWorkflowTests.setUp
    model = smoothing_tests.SmoothingWorkflowTests.model
    update = smoothing_tests.SmoothingWorkflowTests.update

    def post(self):
        return self.client.post(self.path + "/process", headers=self.headers)

    @contextmanager
    def no_postprocessing(self):
        targets = (
            "app.process_stl", "app.process_smoothing",
            "vercel_app.mesh_processing.process_stage",
            "vercel_app.mesh_repair.process_repair",
            "vercel_app.mesh_repair.process_preserving_repair",
            "vercel_app.mesh_reconstruction.process_reconstruction",
            "app.submit_meshy", "app.submit_hunyuan",
        )
        with ExitStack() as stack:
            mocks = [stack.enter_context(patch(target)) for target in targets]
            yield
            for mocked in mocks:
                mocked.assert_not_called()

    def test_completed_generation_saves_only_exact_original_without_processing(self):
        # Both providers return their original STL, preserving the chosen quality.
        for provider in ("meshy", "hunyuan"):
            with self.subTest(provider=provider):
                def generating(state):
                    state.update(status="GENERATING_3D", hunyuan_task=None)
                    state["model_task"].update(provider=provider, models={}, model="provider-test-model",
                                               request={"quality": "good", "result_format": "STL"})
                self.update(generating)
                provider_result = {"status": "completed", "data": [
                    {"type": "stl", "url": "https://example.invalid/provider-original.stl"}
                ]}
                original_put = PublicBlobStore.put
                with self.no_postprocessing(), \
                        patch.object(app_module, f"query_{provider}", return_value=provider_result) as query, \
                        patch.object(app_module, "fetch_bytes", return_value=self.raw) as fetch, \
                        patch.object(PublicBlobStore, "put", autospec=True, side_effect=original_put) as put:
                    response = self.client.get(self.path, headers=self.headers)
                    result = self.model(response)
                    query.assert_called_once()
                    fetch.assert_called_once_with("https://example.invalid/provider-original.stl")
                    self.assertEqual(put.call_count, 1)
                    self.assertEqual(put.call_args.args[2], self.raw)
                self.assertEqual(response.json()["status"], "SUCCEEDED")
                self.assertEqual(result["sha256"], hashlib.sha256(self.raw).hexdigest())
                self.assertEqual(result["size_bytes"], len(self.raw))
                self.assertNotIn("processing", result)
                for field in ("quality", "processed", "repaired", "smoothed", "quality_job"):
                    self.assertNotIn(field, result)
                self.assertEqual(app_module.fetch_bytes(result["url"]), self.raw)
                saved, _ = load_session(self.sid, self.token)
                self.assertEqual(saved["model_task"]["request"]["quality"], "good")
                self.assertNotIn("processing", saved["model_task"]["models"]["stl"])

    def test_reading_v1_result_does_not_migrate_or_start_new_processing(self):
        old = {"url": "https://example.invalid/old-voxel.stl", "version": "stl-auto-v1",
               "source_sha256": self.digest, "reconstructed": True}
        self.update(lambda state: state["model_task"]["models"]["stl"].update(
            processed=old, processing={"version": "stl-auto-v1", "status": "completed", "stage": "done",
                                       "source_sha256": self.digest}))
        saved_before, version_before = load_session(self.sid, self.token)
        expected = copy.deepcopy(saved_before["model_task"]["models"]["stl"])
        with self.no_postprocessing(), patch.object(PublicBlobStore, "put") as put:
            result = self.model(self.client.get(f"/api/sessions/{self.sid}", headers=self.headers))
            put.assert_not_called()
        self.assertEqual(result, expected)
        self.assertEqual(result["url"], self.url)
        self.assertNotIn("previous_processed", result)
        self.assertEqual(load_session(self.sid, self.token), (saved_before, version_before))
        self.assertEqual(app_module.fetch_bytes(result["url"]), self.raw)

    def test_old_pending_or_running_processing_is_read_only_and_cannot_resume(self):
        for status, stage in (("pending", "repair"), ("running", "preserve"),
                              ("failed", "smooth"), ("completed", "done")):
            with self.subTest(status=status):
                self.update(lambda state: state["model_task"]["models"]["stl"].update(
                    processing={"version": "stl-auto-v2", "status": status, "stage": stage,
                                "source_sha256": self.digest,
                                "working_file": {"url": "https://example.invalid/working.stl"}},
                    quality_job={"operation": "process", "started_at": 1}))
                before = load_session(self.sid, self.token)
                with self.no_postprocessing(), patch.object(app_module, "fetch_bytes") as fetch, \
                        patch.object(PublicBlobStore, "put") as put, patch.object(app_module, "_save") as save:
                    response = self.post()
                    self.assertEqual(response.status_code, 410, response.text)
                    self.assertEqual(response.json()["code"], "STL_PROCESSING_DISABLED")
                    fetch.assert_not_called()
                    put.assert_not_called()
                    save.assert_not_called()
                self.assertEqual(load_session(self.sid, self.token), before)
                visible = self.model(self.client.get(f"/api/sessions/{self.sid}", headers=self.headers))
                self.assertEqual(visible["processing"]["status"], status)
                self.assertEqual(visible["url"], self.url)
        self.assertEqual(app_module.fetch_bytes(self.url), self.raw)

    def test_disabled_endpoint_still_authenticates_session_token(self):
        with self.no_postprocessing():
            for headers in ({}, {"X-Session-Token": "wrong"},
                            {"X-Session-Token": "another-valid-length-token-01234567890123456789"}):
                response = self.client.post(self.path + "/process", headers=headers)
                self.assertIn(response.status_code, (401, 403, 404), response.text)
            missing = self.client.post("/api/sessions/" + "f" * 32 + "/3d/process", headers=self.headers)
            self.assertIn(missing.status_code, (401, 403, 404), missing.text)
            self.assertEqual(self.post().status_code, 410)

    def test_disabled_endpoint_does_not_recover_or_write_legacy_session_state(self):
        # _load historically migrates assembly states. A retired endpoint must
        # authenticate without triggering that unrelated state mutation.
        self.update(lambda state: state.update(status="ASSEMBLY_REVIEW"))
        before = load_session(self.sid, self.token)
        with self.no_postprocessing(), patch.object(app_module, "_save") as save:
            response = self.post()
            self.assertEqual(response.status_code, 410, response.text)
            save.assert_not_called()
        self.assertEqual(load_session(self.sid, self.token), before)

    def test_disabled_endpoint_is_gone_even_when_model_is_not_ready(self):
        self.update(lambda state: (state.update(status="GENERATING_3D"),
                                   state["model_task"].update(models={})))
        before = load_session(self.sid, self.token)
        with self.no_postprocessing():
            response = self.post()
        self.assertEqual(response.status_code, 410, response.text)
        self.assertEqual(load_session(self.sid, self.token), before)

    def test_polling_saved_original_never_starts_postprocessing(self):
        before = load_session(self.sid, self.token)
        with self.no_postprocessing(), patch.object(app_module, "query_meshy") as query, \
                patch.object(PublicBlobStore, "put") as put:
            result = self.model(self.client.get(self.path, headers=self.headers))
            query.assert_not_called()
            put.assert_not_called()
        self.assertEqual(result["url"], self.url)
        self.assertNotIn("processing", result)
        self.assertEqual(load_session(self.sid, self.token), before)
        self.assertEqual(app_module.fetch_bytes(result["url"]), self.raw)


if __name__ == "__main__":
    unittest.main()
