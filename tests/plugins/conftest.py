"""Shared fixtures: a local aiohttp fake hub serving a controlled index."""
import json
import pytest
import pytest_asyncio
from aiohttp import web


_INDEX = {
    "version": 1,
    "plugins": [
        {
            "name": "demo",
            "version": "1.0.0",
            "url": "http://{host}/artefacts/demo-1.0.0.tar.gz",
            "sha256": "a" * 64,
            "author": "tester",
            "description": "a demo",
            "category": "utility",
            "tags": ["demo", "test"],
            "permissions": ["fs.read"],
            "tool_count": 1,
            "size_bytes": 1024,
        },
        {
            "name": "deploy-fly",
            "version": "0.2.0",
            "url": "http://{host}/artefacts/deploy-fly-0.2.0.tar.gz",
            "sha256": "b" * 64,
            "author": "ops",
            "description": "deploy to fly.io",
            "category": "devops",
            "tags": ["deploy", "flyio"],
            "permissions": ["net.http", "subprocess"],
            "tool_count": 3,
            "size_bytes": 5432,
        },
    ],
}


@pytest_asyncio.fixture
async def fake_hub(aiohttp_server):
    """Start a local hub and return a dict with url, artefacts mapping, server."""
    artefacts: dict[str, bytes] = {}

    async def index_handler(request):
        host = request.host
        cloned = json.loads(json.dumps(_INDEX))
        for p in cloned["plugins"]:
            p["url"] = p["url"].format(host=host)
        return web.json_response(cloned)

    async def artefact_handler(request):
        name = request.match_info["name"]
        if name not in artefacts:
            return web.Response(status=404)
        return web.Response(body=artefacts[name], content_type="application/gzip")

    app = web.Application()
    app.router.add_get("/index.json", index_handler)
    app.router.add_get("/artefacts/{name}", artefact_handler)
    server = await aiohttp_server(app)
    url = f"http://{server.host}:{server.port}/index.json"
    return {"url": url, "artefacts": artefacts, "server": server}
