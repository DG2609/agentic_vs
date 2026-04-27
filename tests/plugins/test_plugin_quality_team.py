"""Tests for PluginQualityTeam — plugin subsystem audit team.

Runs each domain scanner against the live source tree on `main` and
asserts:

  * sandbox_safety: zero high-severity findings (we ship the gates).
  * supply_chain:   zero high-severity findings (we ship sha256 + caps).
  * rpc_reliability: zero high-severity findings (timeouts + drain).
  * test_coverage:  every required test file present.

These are *meta-tests*: they enforce that the team's own definition of
"good" stays in sync with what we actually ship. If a future commit
deletes the SHA256 check or the stderr drain, the corresponding
domain test breaks here loudly — exactly the regression-floor we
want.
"""
import pytest

from agent.team.plugin_quality import (
    DOMAINS,
    PluginQualityTeam,
    _scan_sandbox_safety,
    _scan_supply_chain,
    _scan_rpc_reliability,
    _scan_test_coverage,
    _SCANNERS,
)


def _highs(issues: list[dict]) -> list[dict]:
    return [i for i in issues if i["severity"] == "high"]


def test_domains_match_scanners():
    assert set(DOMAINS) == set(_SCANNERS.keys())


def test_sandbox_safety_no_high_findings():
    issues = _scan_sandbox_safety()
    assert _highs(issues) == [], f"sandbox_safety regressions: {_highs(issues)}"


def test_supply_chain_no_high_findings():
    issues = _scan_supply_chain()
    assert _highs(issues) == [], f"supply_chain regressions: {_highs(issues)}"


def test_rpc_reliability_no_high_findings():
    issues = _scan_rpc_reliability()
    assert _highs(issues) == [], f"rpc_reliability regressions: {_highs(issues)}"


def test_test_coverage_all_required_present():
    issues = _scan_test_coverage()
    assert issues == [], f"missing required tests: {[i['rule_id'] for i in issues]}"


def test_team_status_initial_state():
    team = PluginQualityTeam()
    import asyncio
    status = asyncio.run(team.get_status())
    assert status["running"] is False
    assert status["converged"] is False
    assert status["round"] == 0
    assert set(status["scores"].keys()) == set(DOMAINS)


@pytest.mark.asyncio
async def test_team_runs_one_round_and_converges_quickly(monkeypatch):
    """End-to-end smoke: one round, then stop. Verifies emit + scoring path."""
    import agent.team.plugin_quality as pq
    monkeypatch.setattr(pq, "_ROUND_SLEEP_S", 0)
    monkeypatch.setattr(pq, "_CONVERGENCE_STABLE_ROUNDS", 1)

    captured: list[tuple[str, dict]] = []

    class _FakeSio:
        async def emit(self, event, data):
            captured.append((event, data))

    team = pq.PluginQualityTeam(sio=_FakeSio())
    import asyncio
    task = asyncio.create_task(team.run_until_converged())
    # Give it up to 5s to converge (it should — main is currently clean).
    for _ in range(50):
        if team.converged or not team.running:
            break
        await asyncio.sleep(0.1)
    if not team.converged:
        await team.stop()
    await asyncio.wait_for(task, timeout=2.0)

    events = {e for e, _ in captured}
    assert "plugin_quality:round_start" in events
    assert "plugin_quality:scores" in events
    assert "plugin_quality:round_done" in events
    # On a clean tree we expect convergence after _CONVERGENCE_STABLE_ROUNDS=1 round.
    assert team.converged or team._stable_rounds >= 1
