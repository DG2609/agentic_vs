from agent.plugins.types import PluginMeta, QualityReport, InstalledPlugin, QualityIssue


def test_plugin_meta_defaults():
    m = PluginMeta(name="demo", version="1.0.0", url="https://x/demo.tar.gz", sha256="a" * 64)
    assert m.author == ""
    assert m.permissions == []
    assert m.signature is None


def test_quality_report_blocked():
    r = QualityReport(score=45, issues=[], blockers=[QualityIssue(rule="eval", message="eval at top level", severity="high")])
    assert r.blocked is True


def test_quality_report_pass():
    r = QualityReport(score=80, issues=[], blockers=[])
    assert r.blocked is False


def test_installed_plugin_repr():
    p = InstalledPlugin(
        name="demo", version="1.0.0", status="installed",
        score=90, permissions=["net.http"], install_path="/x", installed_at="2026-04-21T00:00:00Z",
        last_audited_at="2026-04-21T00:00:00Z", last_error=None,
    )
    assert "demo" in repr(p)
