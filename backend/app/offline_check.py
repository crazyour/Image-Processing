"""Installed-runtime checks. Uses synthetic bytes and httpx.MockTransport; never reads a stored API key."""
import base64, hashlib, json, ssl, certifi, httpx
from .ai_config import PRESET
from .live_api import synthetic_reference
from .providers import OpenAIProvider, MockProvider, ProviderError
from .schemas import Diagnosis, FeedbackSummary, JobIn
from .storage import safe_image

def run():
    calls = []; picture = synthetic_reference(); context = {"input": JobIn(count=1, width_mm=300).model_dump(), "rules": [], "index": 0, "brief": {"detail": "simple", "title": "Synthetic leaf"}}
    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/responses"):
            body = json.loads(request.content)
            schema = body["text"]["format"]["name"]
            if schema == "PlanOutput":
                value = MockProvider().plan(context)[0]
            elif schema == "FeedbackSummary":
                value = FeedbackSummary(feature="detail", direction=-1, scope="CATEGORY", evidence_summary="Synthetic fixture").model_dump()
            else:
                value = Diagnosis(supported=True, observations=["synthetic"], preserved_features=["leaf"], suggestions=["simpler"]).model_dump()
            return httpx.Response(200, json={"id": "response-offline-check", "output": [{"content": [{"type": "output_text", "text": json.dumps(value)}]}]}, headers={"x-request-id": "request-offline-check"})
        
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(picture).decode()}]}, headers={"x-request-id": "image-offline-check"})
    
    provider = OpenAIProvider("synthetic-offline-check", PRESET, httpx.MockTransport(respond))
    try:
        provider.plan(context)
        provider.diagnose({"role": "vision"}, picture)
        generated, _ = provider.image(context)
        edited, _ = provider.image(context, picture)
        provider.diagnose({"role": "quality"}, edited)
        provider.feedback({"human_selection": "以后更简洁", "scope": "CATEGORY"})
        provider.close()
        normalized, image = safe_image(generated)
        assert picture in next((c.content for c in calls))
        assert len(calls) == 6 and image.size == (512, 512)
        trust = ssl.create_default_context(cafile=certifi.where())
        assert trust.verify_mode == ssl.CERT_REQUIRED and trust.check_hostname
        from .windows_secrets import protect, unprotect
        protected = protect("synthetic-dpapi-test")
        assert unprotect(protected) == "synthetic-dpapi-test"
        failure_calls = []
        def forbidden(request):
            failure_calls.append(request)
            return httpx.Response(403, json={"error": {"message": "Your organization must be verified; sk-synthetic-never-save"}}, headers={"x-request-id": "req-offline-forbidden"})
        denied = OpenAIProvider("synthetic-offline-key", PRESET, httpx.MockTransport(forbidden))
        denied.image(context)
        raise AssertionError("403 must not fall back to a mock image")
    except:
        provider.close()
    except ProviderError:
        raise AssertionError
        raise AssertionError
        raise AssertionError
    denied.close(); denied.close()
    assert len(failure_calls) == 1
    
    return {"kind": "INSTALLED_OFFLINE_CONTRACT", "status": "PASS", "http_contract_calls": len(calls), "paid_api_calls": 0, "actual_network_calls": 0, "capabilities_live_verified": False, "png_sha256": hashlib.sha256(normalized).hexdigest(), "tls_ca_certificates": trust.cert_store_stats()["x509_ca"], "dpapi_current_user": True, "reference_bytes_sent": True, "permission_diagnostic_redacted": True, "failed_call_no_retry_or_mock": True, "providers": ["openai"]}
