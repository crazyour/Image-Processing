from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from vercel_app.auth import database_connection
from vercel_app.usage import extract_cost, find_cost, normalize_usage, record_usage, usage_report


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

    def test_token_usage_is_converted_with_per_million_prices(self):
        with patch.dict(os.environ, {
            "USAGE_PRICE_VISION_INPUT_PER_MILLION": "4",
            "USAGE_PRICE_VISION_OUTPUT_PER_MILLION": "20",
        }):
            normalized = normalize_usage({
                "input_tokens": 250_000,
                "output_tokens": 50_000,
                "total_tokens": 300_000,
            }, "gpt-6-luna", "image_to_3d")
        self.assertEqual(normalized["input_tokens"], 250_000)
        self.assertEqual(normalized["output_tokens"], 50_000)
        self.assertEqual(normalized["total_tokens"], 300_000)
        self.assertEqual(normalized["cost_usd"], 2.0)
        self.assertEqual(normalized["cost_source"], "Token 单价估算")
        self.assertIsNone(normalized["cost_field"])

    def test_image_model_is_priced_by_tokens(self):
        normalized = normalize_usage({
            "input_tokens": 25,
            "output_tokens": 424,
            "total_tokens": 449,
        }, "gpt-image-2.5-flare", "text_to_image")
        # 25 text input tokens at $5/1M plus 424 output tokens at $30/1M.
        self.assertEqual(normalized["cost_usd"], 0.01292)
        self.assertEqual(normalized["cost_source"], "Token 单价估算")

    def test_reference_images_are_priced_at_the_image_input_rate(self):
        normalized = normalize_usage({
            "input_tokens": 28_400,
            "output_tokens": 1_056,
            "total_tokens": 29_456,
            "input_tokens_details": {"text_tokens": 40, "image_tokens": 28_360},
        }, "gpt-image-2.5-flare", "image_to_3d")
        # 40 text tokens at $5/1M, 28360 image tokens at $8/1M, 1056 output at $30/1M.
        self.assertEqual(normalized["cost_usd"], 0.25876)
        self.assertEqual(normalized["cost_source"], "Token 单价估算")

    def test_vision_model_uses_the_published_rates(self):
        normalized = normalize_usage({
            "input_tokens": 1_250,
            "output_tokens": 700,
            "total_tokens": 1_950,
        }, "gpt-6-luna", "image_to_3d")
        # gpt-6-luna bills $0.10/1M input and $0.50/1M output.
        self.assertEqual(normalized["cost_usd"], 0.000475)
        self.assertEqual(normalized["cost_source"], "Token 单价估算")

    def test_tasks_without_tokens_keep_their_flat_price(self):
        normalized = normalize_usage(
            {"id": "task-1", "status": "queued"}, "hy-3d-3.1", "image_to_3d",
        )
        self.assertEqual(normalized["cost_usd"], 0.5)
        self.assertEqual(normalized["cost_source"], "预设价格")

    def test_total_tokens_without_a_split_is_still_priced_by_tokens(self):
        normalized = normalize_usage(
            {"total_tokens": 446}, "gpt-image-2.5-flare", "text_to_image",
        )
        # No input/output split reported, so the whole count is billed as output.
        self.assertEqual(normalized["total_tokens"], 446)
        self.assertEqual(normalized["cost_usd"], round(446 * 30 / 1_000_000, 6))
        self.assertEqual(normalized["cost_source"], "Token 单价估算")

    def test_stored_rows_are_repriced_from_their_tokens(self):
        user = {"id": "legacy-user", "username": "legacy", "name": "旧账号"}
        record_usage(user, "text_to_image", "gpt-image-2.5-flare", {
            "input_tokens": 25,
            "output_tokens": 424,
            "total_tokens": 449,
        })
        report = usage_report([user])
        self.assertEqual(report["accounts"][0]["total_tokens"], 449)
        self.assertEqual(report["accounts"][0]["total_usd"], 0.01292)
        self.assertEqual(report["accounts"][0]["sources"][0]["source"], "Token 单价估算")

    def test_rows_stored_with_a_flat_price_are_repriced(self):
        user = {"id": "flat-user", "username": "flat", "name": "旧账号"}
        record_usage(user, "text_to_image", "gpt-image-2.5-flare", {
            "input_tokens": 25,
            "output_tokens": 424,
            "total_tokens": 449,
        })
        with database_connection() as connection:
            connection.execute(
                "UPDATE auth_usage_events SET cost_usd = 0.14, cost_source = ?",
                ("预设价格",),
            )
            connection.commit()

        report = usage_report([user])
        self.assertEqual(report["accounts"][0]["total_usd"], 0.01292)
        self.assertEqual(report["accounts"][0]["sources"][0]["source"], "Token 单价估算")

    def test_existing_unpriced_token_rows_are_backfilled_after_prices_are_configured(self):
        user = {"id": "legacy-user", "username": "legacy", "name": "旧账号"}
        with patch.dict(os.environ, {
            "USAGE_PRICE_VISION_INPUT_PER_MILLION": "0",
            "USAGE_PRICE_VISION_OUTPUT_PER_MILLION": "0",
        }):
            record_usage(user, "image_to_3d", "gpt-6-luna", {
                "input_tokens": 250_000,
                "output_tokens": 50_000,
                "total_tokens": 300_000,
            })
        with patch.dict(os.environ, {
            "USAGE_PRICE_VISION_INPUT_PER_MILLION": "4",
            "USAGE_PRICE_VISION_OUTPUT_PER_MILLION": "20",
        }):
            report = usage_report([user])
        self.assertEqual(report["accounts"][0]["total_tokens"], 300_000)
        self.assertEqual(report["accounts"][0]["total_usd"], 2.0)
        self.assertEqual(report["accounts"][0]["sources"][0]["source"], "Token 单价估算")

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
            self.assertEqual(report["grand_input_tokens"], 100)
            self.assertEqual(report["grand_output_tokens"], 40)
            self.assertEqual(report["grand_total_tokens"], 140)
            self.assertEqual(len(report["accounts"]), 1)
            account = report["accounts"][0]
            self.assertEqual(account["name"], "设计成员")
            self.assertEqual(account["total_usd"], 0.42)
            self.assertEqual(account["input_tokens"], 100)
            self.assertEqual(account["output_tokens"], 40)
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

    def test_admin_can_set_and_reset_each_period_without_deleting_history(self):
        user = self.create_member(["text_to_image"])
        record_usage(user, "text_to_image", "gpt-image-2.5-flare", {"cost": 0.42})

        saved = self.admin.put(f"/api/admin/users/{user['id']}/usage-limits", json={
            "daily_limit_usd": 1,
            "weekly_limit_usd": 2,
            "monthly_limit_usd": 3,
        })
        self.assertEqual(saved.status_code, 200)
        periods = saved.json()["limits"]["periods"]
        self.assertEqual(periods["day"]["used_usd"], 0.42)
        self.assertEqual(periods["week"]["used_usd"], 0.42)
        self.assertEqual(periods["month"]["used_usd"], 0.42)

        reset = self.admin.post(
            f"/api/admin/users/{user['id']}/usage-limits/reset", json={"period": "day"},
        )
        self.assertEqual(reset.status_code, 200)
        periods = reset.json()["limits"]["periods"]
        self.assertEqual(periods["day"]["used_usd"], 0)
        self.assertEqual(periods["week"]["used_usd"], 0.42)
        self.assertEqual(periods["month"]["used_usd"], 0.42)
        self.assertEqual(self.admin.get("/api/admin/usage").json()["grand_total_usd"], 0.42)

    def test_zero_limit_blocks_only_that_account_before_provider_call(self):
        user = self.create_member(["text_to_image"])
        configured = self.admin.put(f"/api/admin/users/{user['id']}/usage-limits", json={
            "daily_limit_usd": 0,
            "weekly_limit_usd": None,
            "monthly_limit_usd": None,
        })
        self.assertEqual(configured.status_code, 200)
        member = self.login_member()
        try:
            created = member.post("/api/text-image/tasks", json={"prompts": ["一只木椅"]})
            body = created.json()
            headers = {"X-Session-Token": body["access_token"]}
            with patch.object(app_module, "generate_image") as provider:
                blocked = member.post(
                    f"/api/text-image/tasks/{body['task_id']}/items/0/generate", headers=headers,
                )
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(blocked.json()["code"], "AI_USAGE_LIMIT_EXCEEDED")
            self.assertEqual(blocked.json()["period"], "day")
            provider.assert_not_called()

            own_task = self.admin.post("/api/text-image/tasks", json={"prompts": ["管理员的图"]}).json()
            with patch.object(
                app_module, "generate_image",
                return_value=(png_bytes(), {"total_tokens": 10, "cost": 0.14}),
            ) as admin_provider:
                allowed = self.admin.post(
                    f"/api/text-image/tasks/{own_task['task_id']}/items/0/generate",
                    headers={"X-Session-Token": own_task["access_token"]},
                )
            self.assertEqual(allowed.status_code, 200)
            admin_provider.assert_called_once()
        finally:
            member.close()

    def test_usage_limit_management_is_admin_only(self):
        user = self.create_member(["text_to_image"])
        member = self.login_member()
        try:
            response = member.get(f"/api/admin/users/{user['id']}/usage-limits")
            self.assertEqual(response.status_code, 403)
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
