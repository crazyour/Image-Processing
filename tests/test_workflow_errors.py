from __future__ import annotations

import unittest
import gzip
from unittest.mock import patch

import httpx

from vercel_app.workflow import WorkflowError, _provider_error, fetch_bytes


class RemoteFileIntegrityTests(unittest.TestCase):
    def download(self, response):
        with patch("vercel_app.workflow.httpx.stream") as stream:
            stream.return_value.__enter__.return_value = response
            return fetch_bytes("https://example.invalid/model.stl")

    def test_partial_response_is_not_saved_as_a_complete_original(self):
        response = httpx.Response(206, content=b"partial", headers={"Content-Range": "bytes 0-6/190000000"})
        with self.assertRaises(WorkflowError) as caught:
            self.download(response)
        self.assertEqual(caught.exception.code, "REMOTE_FILE_INCOMPLETE")

    def test_cleanly_ended_but_short_response_is_rejected(self):
        response = httpx.Response(200, content=b"short", headers={"Content-Length": "190000000"})
        with self.assertRaises(WorkflowError) as caught:
            self.download(response)
        self.assertEqual(caught.exception.code, "REMOTE_FILE_INCOMPLETE")

    def test_empty_response_is_rejected(self):
        with self.assertRaises(WorkflowError) as caught:
            self.download(httpx.Response(200, content=b""))
        self.assertEqual(caught.exception.code, "REMOTE_FILE_INCOMPLETE")

    def test_exact_response_keeps_all_original_bytes(self):
        raw = b"original STL bytes" * 100
        self.assertEqual(self.download(httpx.Response(200, content=raw)), raw)

    def test_http_compression_is_not_mistaken_for_truncation(self):
        raw = b"original STL bytes" * 100
        encoded = gzip.compress(raw)
        response = httpx.Response(200, content=encoded, headers={"Content-Encoding": "gzip", "Content-Length": str(len(encoded))})
        self.assertEqual(self.download(response), raw)


class ProviderErrorTests(unittest.TestCase):
    def test_openai_credit_exhaustion_is_not_reported_as_rate_limit(self):
        response = httpx.Response(429, json={
            "error": {
                "message": "You have no credits remaining. Add credits to continue using the API.",
                "type": "insufficient_quota",
                "code": "billing_hard_limit_reached",
            },
        })

        error = _provider_error("OpenAI", response)

        self.assertEqual(error.code, "OPENAI_CREDITS_EXHAUSTED")
        self.assertEqual(error.status, 402)
        self.assertIn("OpenAI API 余额已用完", error.message)
        self.assertNotIn("请求过于频繁", error.message)

    def test_real_openai_rate_limit_keeps_a_retryable_error(self):
        response = httpx.Response(429, json={
            "error": {
                "message": "Rate limit reached for requests",
                "type": "requests",
                "code": "rate_limit_exceeded",
            },
        })

        error = _provider_error("OpenAI", response)

        self.assertEqual(error.code, "OPENAI_RATE_LIMITED")
        self.assertEqual(error.status, 429)
        self.assertIn("请求过于频繁", error.message)

    def test_tencent_nested_error_keeps_detail_and_request_id(self):
        response = httpx.Response(400, json={
            "Response": {
                "Error": {
                    "Code": "InvalidParameter",
                    "Message": "inputs invalid",
                },
                "RequestId": "req-test",
            },
        })

        error = _provider_error("Tripo", response)

        self.assertEqual(error.code, "TRIPO_ERROR")
        self.assertIn("inputs invalid", error.message)
        self.assertIn("InvalidParameter", error.message)
        self.assertIn("req-test", error.message)

    def test_tokenhub_detail_and_top_level_code_are_visible(self):
        response = httpx.Response(400, json={
            "code": "InvalidParameter",
            "detail": "inputs must be an array",
            "request_id": "req-detail",
        })

        error = _provider_error("Tripo", response)

        self.assertIn("inputs must be an array", error.message)
        self.assertIn("InvalidParameter", error.message)
        self.assertIn("req-detail", error.message)


if __name__ == "__main__":
    unittest.main()
