from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from vercel_app.usage import extract_cost, find_cost, normalize_usage


def png_bytes() -> bytes:
    import cv2
    import numpy as np

    canvas = np.full((256, 256, 3), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("test image encoding failed")
    return encoded.tobytes()


class UsageLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.saved_environment = {
            name: os.environ.get(name)
            for name in ("LOCAL_DATA_DIR", "ADMIN_USERNAME", "ADMIN_PASSWORD", "ADMIN_NAME")
        }
        os.environ.update({
            "LOCAL_DATA_DIR": self.tempdir.name,
            "ADMIN_USERNAME": "owner",
            "ADMIN_PASSWORD": "owner-password-123",
            "ADMIN_NAME": "负责人",
        })
        self.admin = TestClient(app_module.app)
        response = self.admin.post(
            "/api/auth/login",
            json={"username": "owner", "password": "owner-password-123"},
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.admin.close()
        for name, value in self.saved_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.tempdir.cleanup()

    def create_member(self, features: list[str]) -> dict:
        response = self.admin.post("/api/admin/users", json={
            "username": "designer",
            "name": "设计成员",
            "password": "designer-password-123",
            "role": "member",
            "features": features,
        })
        self.assertEqual(response.status_code, 201)
        return response.json()["user"]

    def login_member(self) -> TestClient:
        client = TestClient(app_module.app)
        response = client.post("/api/auth/login", json={
            "username": "designer", "password": "designer-password-123",
        })
        self.assertEqual(response.status_code, 200)
        return client

    def test_provider_price_is_used_when_the_response_carries_one(self):
        self.assertEqual(extract_cost({"cost": 0.42}), 0.42)
        self.assertEqual(extract_cost({"usage": {"total_cost": 1.5}}), 1.5)
        self.assertEqual(extract_cost({"usage": {"cost": {"total": 0.25}}}), 0.25)
        self.assertIsNone(extract_cost({"input_tokens": 10}))
        normalized = normalize_usage(
            {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150, "cost": 0.42},
            "gpt-image-2.5-flare", "text_to_image",
        )
        self.assertEqual(normalized["input_tokens"], 120)
        self.assertEqual(normalized["output_tokens"], 30)
        self.assertEqual(normalized["total_tokens"], 150)
        self.assertEqual(normalized["cost_usd"], 0.42)
        self.assertEqual(normalized["cost_source"], "接口返回")
        self.assertEqual(normalized["cost_field"], "cost")

    def test_price_beside_the_usage_block_is_still_captured(self):
        payload = {"usage": {"total_tokens": 1200}, "cost": 0.21, "request_id": "abc"}
        self.assertEqual(extract_cost(payload), 0.21)
        self.assertEqual(find_cost(payload), (0.21, "cost"))
        with_usage = {"usage": {"total_tokens": 1200, "cost": 0.14}}
        self.assertEqual(find_cost(with_usage), (0.14, "usage.cost"))
        self.assertEqual(find_cost({"usage": {"total_tokens": 1200}}), (None, None))

    def test_generation_usage_is_recorded_for_the_signed_in_account(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            created = member.post(
                "/api/text-image/tasks", json={"prompts": ["一只木椅"]},
            )
            self.assertEqual(created.status_code, 201)
            body = created.json()
            task_id = body["task_id"]
            headers = {"X-Session-Token": body["access_token"]}
            with patch.object(
                app_module, "generate_image",
                return_value=(png_bytes(), {
                    "input_tokens": 100, "output_tokens": 40, "total_tokens": 140, "cost": 0.42,
                }),
            ):
                generated = member.post(
                    f"/api/text-image/tasks/{task_id}/items/0/generate", headers=headers,
                )
            self.assertEqual(generated.status_code, 200)

            report = self.admin.get("/api/admin/usage").json()
            self.assertEqual(report["grand_total_usd"], 0.42)
            self.assertEqual(report["grand_total_calls"], 1)
            self.assertEqual(len(report["accounts"]), 1)
            account = report["accounts"][0]
            self.assertEqual(account["name"], "设计成员")
            self.assertEqual(account["total_usd"], 0.42)
            self.assertEqual(account["total_tokens"], 140)
            feature = account["features"][0]
            self.assertEqual(feature["feature"], "text_to_image")
            self.assertEqual(feature["label"], "文生图")
            self.assertEqual(feature["calls"], 1)
            self.assertEqual(feature["cost_usd"], 0.42)
            self.assertEqual(report["feature_totals"][0]["feature"], "text_to_image")
            sources = account["sources"]
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["source"], "接口返回（cost）")
            self.assertEqual(sources[0]["cost_usd"], 0.42)
        finally:
            member.close()

    def test_usage_report_is_admin_only(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            self.assertEqual(member.get("/api/admin/usage").status_code, 403)
        finally:
            member.close()

    def test_image_to_3d_steps_are_recorded_per_account(self):
        self.create_member(["image_to_3d"])
        member = self.login_member()
        try:
            created = member.post(
                "/api/sessions",
                files={"image": ("ref.png", png_bytes(), "image/png")},
                data={"prompt": "木椅", "view_source": "generated"},
            )
            self.assertIn(created.status_code, (200, 201))
            body = created.json()
            session_id = body["session_id"]
            headers = {"X-Session-Token": body["access_token"]}
            with patch.object(app_module, "analyze_images", return_value=(
                {
                    "subject": "木椅", "geometry": "四腿", "materials": ["木"],
                    "colors": ["棕色"], "occlusion_regions": [],
                    "hidden_geometry_assumptions": [], "image_suitability": "GOOD",
                    "source_view": "FRONT", "warnings": [], "suggested_prompt_zh": "浅色橡木餐椅",
                },
                {"input_tokens": 3000, "output_tokens": 700, "total_tokens": 3700, "cost": 0.09},
            )):
                analyzed = member.post(
                    f"/api/sessions/{session_id}/analyze", headers=headers,
                )
            self.assertEqual(analyzed.status_code, 200)
            member.post(f"/api/sessions/{session_id}/description/confirm", headers=headers)
            with patch.object(
                app_module, "generate_view",
                return_value=(png_bytes(), {
                    "input_tokens": 900, "output_tokens": 300, "total_tokens": 1200, "cost": 0.14,
                }),
            ):
                views = member.post(
                    f"/api/sessions/{session_id}/views", headers=headers,
                    json={"views": ["front", "left", "right", "back"]},
                )
            self.assertEqual(views.status_code, 200)

            report = self.admin.get("/api/admin/usage").json()
            account = report["accounts"][0]
            self.assertEqual(account["name"], "设计成员")
            feature = account["features"][0]
            self.assertEqual(feature["feature"], "image_to_3d")
            self.assertEqual(feature["calls"], 5)
            self.assertEqual(feature["cost_usd"], 0.65)
        finally:
            member.close()

    def test_failed_generation_does_not_break_the_request(self):
        self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            created = member.post(
                "/api/text-image/tasks", json={"prompts": ["一只木椅"]},
            )
            body = created.json()
            headers = {"X-Session-Token": body["access_token"]}
            with patch.object(
                app_module, "generate_image",
                side_effect=app_module.WorkflowError("IMAGE_FAILED", "生成失败", 502),
            ):
                failed = member.post(
                    f"/api/text-image/tasks/{body['task_id']}/items/0/generate", headers=headers,
                )
            self.assertEqual(failed.status_code, 502)
            report = self.admin.get("/api/admin/usage").json()
            self.assertEqual(report["grand_total_usd"], 0)
            self.assertEqual(report["accounts"], [])
        finally:
            member.close()


if __name__ == "__main__":
    unittest.main()
