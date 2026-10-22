from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

import app as app_module


def png(shapes) -> bytes:
    canvas = np.full((128, 128, 3), 255, dtype=np.uint8)
    shapes(canvas)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("test image encoding failed")
    return encoded.tobytes()


def image_with_island() -> bytes:
    def shapes(canvas):
        cv2.rectangle(canvas, (15, 15), (85, 110), (0, 0, 0), -1)
        cv2.circle(canvas, (108, 30), 7, (0, 0, 0), -1)

    return png(shapes)


def connected_image() -> bytes:
    return png(lambda canvas: cv2.rectangle(canvas, (15, 15), (110, 110), (0, 0, 0), -1))


def image_with_fragile_connection() -> bytes:
    def shapes(canvas):
        cv2.rectangle(canvas, (15, 25), (48, 100), (0, 0, 0), -1)
        cv2.rectangle(canvas, (78, 25), (112, 100), (0, 0, 0), -1)
        cv2.line(canvas, (48, 62), (78, 62), (0, 0, 0), 1)

    return png(shapes)


class AnimalCutoutFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("LOCAL_DATA_DIR")
        self.previous_admin_username = os.environ.get("ADMIN_USERNAME")
        self.previous_admin_password = os.environ.get("ADMIN_PASSWORD")
        os.environ["LOCAL_DATA_DIR"] = self.tempdir.name
        os.environ["ADMIN_USERNAME"] = "test-admin"
        os.environ["ADMIN_PASSWORD"] = "test-admin-password"
        self.client = TestClient(app_module.app)
        response = self.client.post(
            "/api/auth/login",
            json={"username": "test-admin", "password": "test-admin-password"},
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.client.close()
        if self.previous_data_dir is None:
            os.environ.pop("LOCAL_DATA_DIR", None)
        else:
            os.environ["LOCAL_DATA_DIR"] = self.previous_data_dir
        for name, value in (
            ("ADMIN_USERNAME", self.previous_admin_username),
            ("ADMIN_PASSWORD", self.previous_admin_password),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def create_task(self, generation_count: int = 1) -> tuple[str, dict[str, str]]:
        source = connected_image()
        created = self.client.post(
            "/api/animal-cutout/tasks",
            files={"image": ("animal.png", source, "image/png")},
            data={
                "prompt": "保留照片中的主要视觉主体",
                "image_model": "test-image-model",
                "image_quality": "medium",
                "size": "1024x1024",
                "generation_count": str(generation_count),
            },
        )
        self.assertEqual(created.status_code, 201)
        task = created.json()
        task_id = task["task_id"]
        headers = {"X-Session-Token": task["access_token"]}
        return task_id, headers

    def select_candidate(self, task_id: str, headers: dict[str, str], index: int = 0):
        return self.client.post(
            f"/api/animal-cutout/tasks/{task_id}/select",
            headers=headers,
            json={"candidate_index": index},
        )

    def test_generation_two_automatic_repairs_and_restore(self):
        task_id, headers = self.create_task()

        generated = image_with_island()
        still_broken = image_with_fragile_connection()
        repaired = connected_image()
        with patch.object(
            app_module,
            "generate_view",
            side_effect=[(generated, {"step": "generate"}),
                         (still_broken, {"step": "repair-1"}),
                         (repaired, {"step": "repair-2"})],
        ) as image_edit:
            first = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers,
            )
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "CANDIDATES_READY")
            self.assertIsNone(first.json()["connectivity"])
            first = self.select_candidate(task_id, headers)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "REPAIR_REQUIRED")
            self.assertEqual(first.json()["connectivity"]["island_count"], 1)
            original_process = first.json()["process_details"]["original_result"]
            self.assertIn("diagnostic_url", original_process["connectivity"])
            self.assertNotIn("repair_guide_url", original_process["connectivity"])

            second = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/repair",
                headers=headers,
                json={"automatic": True},
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["status"], "REPAIR_REQUIRED")
            self.assertEqual(second.json()["automatic_repair_attempts"], 1)

            third = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/repair",
                headers=headers,
                json={"automatic": True},
            )
            body = third.json()
            self.assertEqual(third.status_code, 200)
            self.assertEqual(body["status"], "SUCCEEDED")
            self.assertTrue(body["connectivity"]["passed"])
            self.assertEqual(body["automatic_repair_attempts"], 2)
            self.assertTrue(body["can_restore_original"])
            self.assertEqual(len(body["process_details"]["repairs"]), 2)
            self.assertEqual(body["process_details"]["repairs"][0]["status"], "COMPLETED")
            first_repair_prompt = body["process_details"]["repairs"][0]["prompt"]
            self.assertIn("本图有 1 个断开孤岛", first_repair_prompt)
            self.assertIn("图片1是必须保留的当前纯黑白镂空图", first_repair_prompt)
            self.assertIn("透明蒙版是唯一允许修改的局部", first_repair_prompt)

            self.assertEqual(image_edit.call_count, 3)
            first_repair_call = image_edit.call_args_list[1]
            first_repair_inputs = first_repair_call.args[0]
            self.assertEqual(len(first_repair_inputs), 1)
            self.assertEqual(first_repair_inputs[0], generated)
            self.assertEqual(first_repair_call.args[2], "gpt-image-2.5-sunburst")
            self.assertEqual(first_repair_call.args[3], "high")
            repair_mask = cv2.imdecode(
                np.frombuffer(first_repair_call.args[5], np.uint8), cv2.IMREAD_UNCHANGED,
            )
            self.assertEqual(repair_mask.shape[2], 4)
            self.assertIn(0, repair_mask[:, :, 3])
            self.assertIn(255, repair_mask[:, :, 3])
            self.assertIn("不能只重新描绘原图", first_repair_call.args[1])
            self.assertNotIn("repair_model", body["process_details"])
            self.assertNotIn("repair_quality", body["process_details"])
            self.assertNotIn("model", body["process_details"]["repairs"][0])
            self.assertNotIn("input_diagnostic_url", body["process_details"]["repairs"][0])
            self.assertIn("mask_url", body["process_details"]["repairs"][0])

        restored = self.client.post(
            f"/api/animal-cutout/tasks/{task_id}/restore-original", headers=headers,
        )
        restored_body = restored.json()
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored_body["status"], "REVIEW_REQUIRED")
        self.assertEqual(restored_body["connectivity"]["island_count"], 1)
        self.assertTrue(restored_body["can_manual_repair"])
        self.assertFalse(restored_body["can_restore_original"])

    def test_automatic_repairs_stop_after_two_failed_checks(self):
        task_id, headers = self.create_task()
        broken = image_with_fragile_connection()
        with patch.object(
            app_module,
            "generate_view",
            side_effect=[(broken, {}), (broken, {}), (broken, {})],
        ) as image_edit:
            state = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers,
            ).json()
            self.assertEqual(state["status"], "CANDIDATES_READY")
            state = self.select_candidate(task_id, headers).json()
            for _ in range(2):
                state = self.client.post(
                    f"/api/animal-cutout/tasks/{task_id}/repair",
                    headers=headers,
                    json={"automatic": True},
                ).json()

            self.assertEqual(state["status"], "REVIEW_REQUIRED")
            self.assertEqual(state["automatic_repair_attempts"], 2)
            self.assertEqual(state["connectivity"]["island_count"], 1)
            self.assertTrue(state["can_manual_repair"])

            capped = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/repair",
                headers=headers,
                json={"automatic": True},
            )
            self.assertEqual(capped.status_code, 200)
            self.assertEqual(capped.json()["status"], "REVIEW_REQUIRED")
            self.assertEqual(image_edit.call_count, 3)

    def test_clean_generation_finishes_without_a_repair_call(self):
        task_id, headers = self.create_task()
        with patch.object(
            app_module, "generate_view", return_value=(connected_image(), {"step": "generate"}),
        ) as image_edit:
            response = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "CANDIDATES_READY")
            response = self.select_candidate(task_id, headers)

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "SUCCEEDED")
        self.assertTrue(body["connectivity"]["passed"])
        self.assertEqual(body["repair_attempts"], 0)
        self.assertEqual(image_edit.call_count, 1)

    def test_automatic_repair_never_deletes_disconnected_parts(self):
        task_id, headers = self.create_task()
        generated = image_with_island()

        with patch.object(
            app_module,
            "generate_view",
            side_effect=[(generated, {"step": "generate"}), (generated, {"step": "repair"})],
        ) as image_edit:
            first = self.client.post(f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "CANDIDATES_READY")
            first = self.select_candidate(task_id, headers)
            self.assertEqual(first.json()["status"], "REPAIR_REQUIRED")

            second = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/repair",
                headers=headers,
                json={"automatic": True},
            )
            body = second.json()
            self.assertEqual(second.status_code, 200)
            self.assertEqual(body["status"], "REPAIR_REQUIRED")
            self.assertFalse(body["connectivity"]["passed"])
            self.assertEqual(body["connectivity"]["disconnected_island_count"], 1)
            self.assertEqual(body["repair_attempts"], 1)
            self.assertEqual(image_edit.call_count, 2)
            self.assertNotIn("local_cleanup_applied", body["process_details"]["repairs"][0])

    def test_generation_and_repair_use_separate_model_settings(self):
        source = connected_image()
        created = self.client.post(
            "/api/animal-cutout/tasks",
            files={"image": ("animal.png", source, "image/png")},
            data={
                "prompt": "保留动物",
                "image_model": "frontend-must-not-control-this",
                "image_quality": "max",
                "repair_model": "frontend-must-not-control-this",
                "repair_quality": "low",
                "size": "1536x1024",
                "generation_count": "1",
            },
        )
        self.assertEqual(created.status_code, 201)
        task_id = created.json()["task_id"]
        headers = {"X-Session-Token": created.json()["access_token"]}

        generated = image_with_island()
        repaired = connected_image()
        with patch.object(
            app_module,
            "generate_view",
            side_effect=[(generated, {"step": "generate"}), (repaired, {"step": "repair"})],
        ) as image_edit:
            first = self.client.post(f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers)
            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["status"], "CANDIDATES_READY")
            first = self.select_candidate(task_id, headers)
            self.assertEqual(first.json()["status"], "REPAIR_REQUIRED")

            second = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/repair",
                headers=headers,
                json={"automatic": True},
            )
            self.assertEqual(second.status_code, 200)
            self.assertEqual(second.json()["status"], "SUCCEEDED")

        self.assertEqual(image_edit.call_count, 2)
        self.assertEqual(image_edit.call_args_list[0].args[2], "gpt-image-2.5-flare")
        self.assertEqual(image_edit.call_args_list[0].args[3], "medium")
        self.assertEqual(image_edit.call_args_list[1].args[2], "gpt-image-2.5-sunburst")
        self.assertEqual(image_edit.call_args_list[1].args[3], "high")

    def test_final_result_can_be_downloaded_as_dxf(self):
        task_id, headers = self.create_task()
        with patch.object(app_module, "generate_view", return_value=(connected_image(), {})):
            generated = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers,
            )
        self.assertEqual(generated.status_code, 200)
        selected = self.select_candidate(task_id, headers)
        self.assertEqual(selected.json()["status"], "SUCCEEDED")

        downloaded = self.client.get(
            f"/api/animal-cutout/tasks/{task_id}/dxf", headers=headers,
        )
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.headers["content-type"], "application/dxf")
        self.assertIn("LWPOLYLINE", downloaded.text)
        self.assertTrue(downloaded.text.endswith("0\nEOF\n"))

    def test_public_pages_do_not_offer_model_controls(self):
        page = self.client.get("/text-to-image").text
        self.assertNotIn("模型设置", page)
        self.assertNotIn('id="image-model"', page)
        self.assertNotIn('id="animal-image-model"', page)
        self.assertNotIn("我有权使用这张照片", page)
        self.assertNotIn('id="animal-consent"', page)

    def test_generates_requested_candidates_and_checks_only_selected_one(self):
        task_id, headers = self.create_task(generation_count=3)
        images = [connected_image(), image_with_island(), connected_image()]
        with (
            patch.object(
                app_module,
                "generate_view",
                side_effect=[(image, {"candidate": index}) for index, image in enumerate(images)],
            ) as image_edit,
            patch.object(app_module, "_inspect_and_store_cutout", wraps=app_module._inspect_and_store_cutout) as inspect,
        ):
            generated = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers,
            )
            self.assertEqual(generated.status_code, 200)
            body = generated.json()
            self.assertEqual(body["status"], "CANDIDATES_READY")
            self.assertEqual(body["generation_count"], 3)
            self.assertEqual(len(body["candidates"]), 3)
            self.assertIsNone(body["connectivity"])
            self.assertEqual(inspect.call_count, 0)
            self.assertEqual(image_edit.call_count, 3)

            selected = self.select_candidate(task_id, headers, 1)
            self.assertEqual(selected.status_code, 200)
            selected_body = selected.json()
            self.assertEqual(selected_body["selected_candidate_index"], 1)
            self.assertIsNotNone(selected_body["connectivity"])
            self.assertEqual(inspect.call_count, 1)


if __name__ == "__main__":
    unittest.main()
