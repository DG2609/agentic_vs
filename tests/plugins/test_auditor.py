from pathlib import Path
import pytest

from agent.plugins.auditor import QualityAuditor

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_good_plugin_passes():
    rep = await QualityAuditor().audit(FIX / "good_plugin")
    assert rep.blocked is False
    assert rep.score >= 60


@pytest.mark.asyncio
async def test_eval_plugin_blocked():
    rep = await QualityAuditor().audit(FIX / "eval_plugin")
    assert rep.blocked is True
    rules = {b.rule for b in rep.blockers}
    assert "top-level-side-effect" in rules


@pytest.mark.asyncio
async def test_missing_manifest_blocked(tmp_path):
    (tmp_path / "plugin").mkdir()
    (tmp_path / "plugin" / "x.py").write_text("__skill_tools__ = []\n")
    rep = await QualityAuditor().audit(tmp_path / "plugin")
    assert rep.blocked is True
    assert any(b.rule == "missing-manifest" for b in rep.blockers)


@pytest.mark.asyncio
async def test_unknown_permission_blocked(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "plugin.json").write_text(
        '{"name":"x","version":"1","tools":[],"permissions":["root.access"],"entry":"x"}'
    )
    (tmp_path / "p" / "x.py").write_text("__skill_tools__ = []\n")
    rep = await QualityAuditor().audit(tmp_path / "p")
    assert rep.blocked is True
    assert any(b.rule == "unknown-permission" for b in rep.blockers)
