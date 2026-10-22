from __future__ import annotations

import unittest

import httpx

from vercel_app.workflow import _provider_error


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


if __name__ == "__main__":
    unittest.main()
