import pytest

from agent.plugins.hub_scout import HubScout


@pytest.mark.asyncio
async def test_fetch_index(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    results = await scout.search("")
    names = {r.name for r in results}
    assert names == {"demo", "deploy-fly"}


@pytest.mark.asyncio
async def test_search_by_name_substring(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    results = await scout.search("deploy")
    assert [r.name for r in results] == ["deploy-fly"]


@pytest.mark.asyncio
async def test_search_by_category(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    results = await scout.search("", category="devops")
    assert [r.name for r in results] == ["deploy-fly"]


@pytest.mark.asyncio
async def test_inspect_exact_match(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    m = await scout.inspect("demo")
    assert m.version == "1.0.0"
    assert m.permissions == ["fs.read"]


@pytest.mark.asyncio
async def test_inspect_missing_returns_none(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    assert await scout.inspect("does-not-exist") is None


@pytest.mark.asyncio
async def test_disk_cache_used_when_offline(tmp_path, fake_hub):
    scout = HubScout(index_url=fake_hub["url"], cache_dir=tmp_path)
    await scout.search("")
    scout._index_url = "http://127.0.0.1:1/index.json"
    scout._mem_cache = None
    scout._mem_cache_at = 0.0
    results = await scout.search("")
    assert {r.name for r in results} == {"demo", "deploy-fly"}
