"""Tests for shared infrastructure: /health endpoint, root redirect, and the
security headers middleware that wraps every response.

These assertions used to live in test_security.py alongside coverage for the
legacy /api/* routes; they survive that flow's removal because the middleware
and probe endpoints are still load-bearing for Cloud Run.
"""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


class TestHealthEndpoint:
    def test_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_returns_ok_status(self, client):
        resp = client.get("/health")
        assert resp.json() == {"status": "ok"}

    def test_fast_response(self, client):
        """Health check must not hit any slow dependencies."""
        import time
        start = time.monotonic()
        client.get("/health")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, "Health check took too long"


class TestRootRedirect:
    def test_root_redirects_to_kids_teacher(self, client):
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/kids-teacher"


class TestSecurityHeaders:
    EXPECTED_HEADERS = {
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "strict-origin-when-cross-origin",
    }

    def test_headers_on_health(self, client):
        resp = client.get("/health")
        for header, value in self.EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, f"Missing/wrong header: {header}"

    def test_headers_on_root_redirect(self, client):
        resp = client.get("/", follow_redirects=False)
        for header, value in self.EXPECTED_HEADERS.items():
            assert resp.headers.get(header) == value, f"Missing/wrong header: {header}"

    def test_csp_present(self, client):
        resp = client.get("/health")
        csp = resp.headers.get("content-security-policy", "")
        assert "default-src" in csp
        assert "'self'" in csp

    def test_hsts_present(self, client):
        resp = client.get("/health")
        hsts = resp.headers.get("strict-transport-security", "")
        assert "max-age=" in hsts
