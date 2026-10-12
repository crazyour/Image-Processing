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


class AnimalCutoutFlowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.previous_data_dir = os.environ.get("LOCAL_DATA_DIR")
        os.environ["LOCAL_DATA_DIR"] = self.tempdir.name
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.client.close()
        if self.previous_data_dir is None:
            os.environ.pop("LOCAL_DATA_DIR", None)
        else:
            os.environ["LOCAL_DATA_DIR"] = self.previous_data_dir
        self.tempdir.cleanup()

    def create_task(self) -> tuple[str, dict[str, str]]:
        source = connected_image()
        created = self.client.post(
            "/api/animal-cutout/tasks",
            files={"image": ("animal.png", source, "image/png")},
            data={
                "prompt": "保留动物",
                "image_model": "test-image-model",
                "image_quality": "medium",
                "size": "1024x1024",
            },
        )
        self.assertEqual(created.status_code, 201)
        task = created.json()
        task_id = task["task_id"]
        headers = {"X-Session-Token": task["access_token"]}
        return task_id, headers

    def test_generation_two_automatic_repairs_and_restore(self):
        task_id, headers = self.create_task()

        generated = image_with_island()
        still_broken = image_with_island()
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
            self.assertEqual(first.json()["status"], "REPAIR_REQUIRED")
            self.assertEqual(first.json()["connectivity"]["island_count"], 1)

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

            self.assertEqual(image_edit.call_count, 3)
            first_repair_inputs = image_edit.call_args_list[1].args[0]
            self.assertEqual(len(first_repair_inputs), 2)
            diagnostic = cv2.imdecode(
                np.frombuffer(first_repair_inputs[1], np.uint8), cv2.IMREAD_COLOR,
            )
            diagnostic_colors = {tuple(pixel) for row in diagnostic for pixel in row}
            self.assertIn((235, 99, 37), diagnostic_colors)
            self.assertGreaterEqual(len(diagnostic_colors), 3)

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
        broken = image_with_island()
        with patch.object(
            app_module,
            "generate_view",
            side_effect=[(broken, {}), (broken, {}), (broken, {})],
        ) as image_edit:
            state = self.client.post(
                f"/api/animal-cutout/tasks/{task_id}/generate", headers=headers,
            ).json()
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

        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["status"], "SUCCEEDED")
        self.assertTrue(body["connectivity"]["passed"])
        self.assertEqual(body["repair_attempts"], 0)
        self.assertEqual(image_edit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
