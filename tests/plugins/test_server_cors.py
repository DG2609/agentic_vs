"""Verify that plugin routes pick up the server's CORS config.

Spins up the real aiohttp app via create_app() and checks the Access-Control
response headers on OPTIONS preflight for every /api/plugins/* endpoint.
"""
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from server.main import create_app


@pytest_asyncio.fixture
async def client():
    app = await create_app()
    async with TestClient(TestServer(app)) as c:
        yield c


_CORS_HEADERS = {
    "Origin": "http://example.invalid",
    "Access-Control-Request-Method": "GET",
    "Access-Control-Request-Headers": "Content-Type",
}


@pytest.mark.parametrize("path,method", [
    ("/api/plugins", "GET"),
    ("/api/plugins/search", "GET"),
    ("/api/plugins/inspect", "GET"),
    ("/api/plugins/audit", "POST"),
    ("/api/plugins/install", "POST"),
    ("/api/plugins/uninstall", "POST"),
    ("/api/plugins/reload", "POST"),
    ("/api/plugins/report", "GET"),
])
@pytest.mark.asyncio
async def test_plugin_routes_cors_preflight(client, path, method):
    headers = dict(_CORS_HEADERS)
    headers["Access-Control-Request-Method"] = method
    resp = await client.options(path, headers=headers)
    assert resp.status in (200, 204), (
        f"{method} {path} preflight returned {resp.status}"
    )
    allow = resp.headers.get("Access-Control-Allow-Origin", "")
    assert allow in ("*", "http://example.invalid"), (
        f"{method} {path} missing Access-Control-Allow-Origin (got {allow!r})"
    )
