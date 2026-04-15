"""
PromptIntelTeam — Perpetual agent that mines the Claude Code leaked source
and continuously improves ShadowDev's prompts.

Emits Socket.IO events for the Ink CLI frontend:
  intel:status       { running, round, overall_score, total_improvements }
  intel:round_start  { round, timestamp }
  intel:reading      { domain, file }
  intel:finding      { domain, file, patterns_found, snippets }
  intel:gap          { domain, gap, cc_technique, impact }
  intel:improvement  { domain, target_file, description }
  intel:scores       { round, scores, overall }
  intel:round_done   { round, improvements_this_round, scores }
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Source file maps
# ---------------------------------------------------------------------------

_CC_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "claude-code-leak_ref",
    "src",
)

CC_TARGETS: dict[str, list[str]] = {
    "system_prompt": [
        "constants/prompts.ts",
        "constants/systemPromptSections.ts",
        "constants/system.ts",
        "constants/common.ts",
    ],
    "tool_descriptions": [
        "tools/BashTool/prompt.ts",
        "tools/GlobTool/prompt.ts",
        "tools/GrepTool/prompt.ts",
        "tools/AgentTool/prompt.ts",
        "tools/TodoWriteTool/prompt.ts",
        "tools/TaskCreateTool/prompt.ts",
        "tools/WebFetchTool/prompt.ts",
    ],
    "coordinator": [
        "coordinator/coordinatorMode.ts",
        "utils/swarm/teammatePromptAddendum.ts",
    ],
    "memory": [
        "services/compact/prompt.ts",
        "services/autoDream/consolidationPrompt.ts",
        "services/SessionMemory/prompts.ts",
        "services/extractMemories/prompts.ts",
        "memdir/teamMemPrompts.ts",
    ],
    "agents": [
        "tools/AgentTool/built-in/generalPurposeAgent.ts",
        "tools/AgentTool/built-in/exploreAgent.ts",
        "tools/AgentTool/built-in/planAgent.ts",
        "tools/AgentTool/built-in/verificationAgent.ts",
    ],
}

_SD_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHADOWDEV_COMPARE: dict[str, str] = {
    "system_prompt": "agent/nodes.py",
    "tool_descriptions": "agent/graph.py",
    "coordinator": "agent/team/coordinator.py",
    "memory": "agent/nodes.py",
    "agents": "agent/team/worker_pool.py",
}

# ---------------------------------------------------------------------------
# Pattern extraction helpers
# ---------------------------------------------------------------------------

# Keywords that signal an important prompt instruction
_INSTRUCTION_KEYWORDS = re.compile(
    r"\b(you are|your job|your role|never |always |do not|important:|guidelines:|"
    r"critical:|note:|warning:|must |should |prohibited|required|forbidden|"
    r"you must|you should|you will|do not|don't)\b",
    re.IGNORECASE,
)

# Template literal: content between backticks that spans multiple lines or is long
_TEMPLATE_LITERAL_RE = re.compile(r"`([^`]{100,}?)`", re.DOTALL)

# Named string constant that could be a prompt section
_NAMED_CONST_RE = re.compile(
    r"(?:const|export const|let|export let)\s+([A-Z_]{4,})\s*=\s*[`'\"](.{30,})[`'\"]",
    re.DOTALL,
)


def _extract_patterns(content: str, domain: str) -> list[dict]:
    """
    Extract meaningful prompt patterns from TypeScript source content.

    Returns a list of dicts with keys: type, text, line_number, domain.
    """
    patterns: list[dict] = []
    lines = content.splitlines()

    # 1. Template literals (backtick strings > 100 chars)
    for match in _TEMPLATE_LITERAL_RE.finditer(content):
        text = match.group(1).strip()
        if len(text) < 100:
            continue
        # Find approximate line number
        line_no = content[: match.start()].count("\n") + 1
        patterns.append(
            {
                "type": "template_literal",
                "text": text[:500],
                "line_number": line_no,
                "domain": domain,
            }
        )

    # 2. Lines containing instruction keywords
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if len(stripped) < 30:
            continue
        if _INSTRUCTION_KEYWORDS.search(stripped):
            # Skip pure import/require lines
            if stripped.startswith(("import ", "require(", "//", "*", "}")):
                continue
            # Strip TS syntax noise
            cleaned = re.sub(r"[`'\",;\\]", "", stripped).strip()
            if len(cleaned) > 20:
                patterns.append(
                    {
                        "type": "instruction_line",
                        "text": cleaned[:300],
                        "line_number": i,
                        "domain": domain,
                    }
                )

    # 3. Named constants that look like prompt sections
    for match in _NAMED_CONST_RE.finditer(content):
        name = match.group(1)
        text = match.group(2).strip()
        line_no = content[: match.start()].count("\n") + 1
        if any(kw in name for kw in ("PROMPT", "SYSTEM", "INSTRUCTION", "GUIDELINE", "ADDENDUM")):
            patterns.append(
                {
                    "type": "named_constant",
                    "text": f"{name}: {text[:300]}",
                    "line_number": line_no,
                    "domain": domain,
                }
            )

    # Deduplicate by text prefix
    seen: set[str] = set()
    unique: list[dict] = []
    for p in patterns:
        key = p["text"][:80]
        if key not in seen:
            seen.add(key)
            unique.append(p)

    return unique


# ---------------------------------------------------------------------------
# Scoring helper
# ---------------------------------------------------------------------------

def _score_domain(cc_findings: list[dict], shadowdev_content: str) -> int:
    """
    Score 0-100: percentage of CC patterns whose key phrases appear in ShadowDev.
    """
    if not cc_findings:
        return 100  # nothing to compare against

    shadowdev_lower = shadowdev_content.lower()
    hits = 0
    for p in cc_findings:
        # Extract key phrase: first 6+ word run of actual words
        words = re.findall(r"[a-z]{3,}", p["text"].lower())
        if len(words) < 3:
            hits += 1  # too short to score meaningfully, count as present
            continue
        phrase = " ".join(words[:4])
        if phrase in shadowdev_lower:
            hits += 1

    return int(100 * hits / len(cc_findings))


# ---------------------------------------------------------------------------
# Impact classifier
# ---------------------------------------------------------------------------

_HIGH_IMPACT_SIGNALS = re.compile(
    r"\b(critical|prohibited|never|always|must|required|forbidden|do not|important:)\b",
    re.IGNORECASE,
)
_MEDIUM_IMPACT_SIGNALS = re.compile(
    r"\b(should|guidelines|note:|warning:|verify|check|ensure)\b",
    re.IGNORECASE,
)


def _classify_impact(text: str) -> str:
    if _HIGH_IMPACT_SIGNALS.search(text):
        return "high"
    if _MEDIUM_IMPACT_SIGNALS.search(text):
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class PromptIntelTeam:
    """
    Perpetual agent team that mines CC leaked source and improves ShadowDev prompts.
    """

    def __init__(self, sio=None) -> None:
        self.sio = sio
        self.running: bool = False
        self.round: int = 0
        self.total_improvements: int = 0
        self.scores: dict[str, int] = {domain: 0 for domain in CC_TARGETS}
        self.findings: dict[str, list[dict]] = {domain: [] for domain in CC_TARGETS}
        self.improvements_log: list[dict] = []
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_status(self) -> dict:
        overall = self._overall_score()
        return {
            "running": self.running,
            "round": self.round,
            "overall_score": overall,
            "total_improvements": self.total_improvements,
            "scores": dict(self.scores),
            "improvements_log": self.improvements_log[-20:],  # last 20
        }

    async def stop(self) -> None:
        self.running = False
        self._stop_event.set()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        self.running = True
        self._stop_event.clear()
        logger.info("PromptIntelTeam: starting perpetual loop")

        try:
            while not self._stop_event.is_set():
                self.round += 1
                await self._emit(
                    "intel:round_start",
                    {
                        "round": self.round,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                await self._emit(
                    "intel:status",
                    {
                        "running": True,
                        "round": self.round,
                        "overall_score": self._overall_score(),
                        "total_improvements": self.total_improvements,
                    },
                )

                # Run all domain workers in parallel
                domain_results = await asyncio.gather(
                    *[self._domain_worker(domain) for domain in CC_TARGETS],
                    return_exceptions=True,
                )

                # Merge findings (accumulate across rounds)
                all_findings: dict[str, list[dict]] = {}
                for result in domain_results:
                    if isinstance(result, Exception):
                        logger.error("Domain worker error: %s", result)
                        continue
                    if isinstance(result, dict) and "domain" in result:
                        domain = result["domain"]
                        new_findings = result.get("findings", [])
                        # Accumulate: add patterns not already in findings
                        existing_texts = {p["text"][:80] for p in self.findings[domain]}
                        for p in new_findings:
                            if p["text"][:80] not in existing_texts:
                                self.findings[domain].append(p)
                                existing_texts.add(p["text"][:80])
                        all_findings[domain] = self.findings[domain]

                # Synthesize and apply improvements
                improvements_this_round = await self._synthesize_and_improve(all_findings)

                # Update scores
                await self._update_scores(all_findings)

                overall = self._overall_score()
                await self._emit(
                    "intel:scores",
                    {
                        "round": self.round,
                        "scores": dict(self.scores),
                        "overall": overall,
                    },
                )
                await self._emit(
                    "intel:round_done",
                    {
                        "round": self.round,
                        "improvements_this_round": improvements_this_round,
                        "scores": dict(self.scores),
                    },
                )
                await self._emit(
                    "intel:status",
                    {
                        "running": True,
                        "round": self.round,
                        "overall_score": overall,
                        "total_improvements": self.total_improvements,
                    },
                )

                logger.info(
                    "PromptIntelTeam: round %d done — %d improvements, score %d",
                    self.round,
                    improvements_this_round,
                    overall,
                )

                # Sleep between rounds (60s); check stop every second
                for _ in range(60):
                    if self._stop_event.is_set():
                        break
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            logger.info("PromptIntelTeam: cancelled")
        finally:
            self.running = False
            await self._emit(
                "intel:status",
                {
                    "running": False,
                    "round": self.round,
                    "overall_score": self._overall_score(),
                    "total_improvements": self.total_improvements,
                },
            )

    # ------------------------------------------------------------------
    # Domain worker
    # ------------------------------------------------------------------

    async def _domain_worker(self, domain: str) -> dict:
        """
        Read each CC file for the domain, extract patterns, emit events.
        Returns {domain, findings}.
        """
        domain_findings: list[dict] = []
        cc_files = CC_TARGETS.get(domain, [])

        for rel_path in cc_files:
            full_path = os.path.join(_CC_ROOT, rel_path.replace("/", os.sep))

            await self._emit("intel:reading", {"domain": domain, "file": rel_path})

            if not os.path.isfile(full_path):
                logger.debug("CC file not found: %s", full_path)
                continue

            try:
                content = _read_file_safe(full_path)
            except OSError as exc:
                logger.warning("Cannot read %s: %s", full_path, exc)
                continue

            # Yield control so we don't block the event loop on large files
            await asyncio.sleep(0)

            patterns = _extract_patterns(content, domain)
            domain_findings.extend(patterns)

            snippets = [p["text"][:120] for p in patterns[:5]]
            await self._emit(
                "intel:finding",
                {
                    "domain": domain,
                    "file": rel_path,
                    "patterns_found": len(patterns),
                    "snippets": snippets,
                },
            )

        return {"domain": domain, "findings": domain_findings}

    # ------------------------------------------------------------------
    # Synthesize & improve
    # ------------------------------------------------------------------

    async def _synthesize_and_improve(self, all_findings: dict[str, list[dict]]) -> int:
        """
        Compare CC findings against ShadowDev files.
        Emit intel:gap for each gap found.
        Apply top-priority improvements to ShadowDev files.
        Returns number of improvements applied this round.
        """
        improvements_applied = 0

        for domain, findings in all_findings.items():
            if not findings:
                continue

            sd_rel = SHADOWDEV_COMPARE.get(domain, "")
            if not sd_rel:
                continue

            sd_path = os.path.join(_SD_ROOT, sd_rel.replace("/", os.sep))
            try:
                sd_content = _read_file_safe(sd_path)
            except OSError as exc:
                logger.warning("Cannot read ShadowDev file %s: %s", sd_path, exc)
                continue

            sd_lower = sd_content.lower()
            gaps: list[dict] = []

            for pattern in findings:
                text = pattern["text"]
                # Check if the key instruction already exists in ShadowDev
                words = re.findall(r"[a-z]{3,}", text.lower())
                if len(words) < 3:
                    continue
                # Use a 4-word sliding window — if any window matches, it's covered
                covered = False
                for i in range(max(1, len(words) - 3)):
                    phrase = " ".join(words[i : i + 4])
                    if phrase in sd_lower:
                        covered = True
                        break

                if not covered:
                    impact = _classify_impact(text)
                    # Build a short human-readable description of the CC technique
                    first_sentence = re.split(r"[.!?\n]", text.strip())[0][:120]
                    cc_technique = first_sentence.strip()
                    gap_desc = f"Missing CC pattern ({pattern['type']}): {first_sentence[:80]}"

                    gaps.append(
                        {
                            "domain": domain,
                            "gap": gap_desc,
                            "cc_technique": cc_technique,
                            "impact": impact,
                            "pattern": pattern,
                            "sd_path": sd_path,
                            "sd_rel": sd_rel,
                        }
                    )
                    await self._emit(
                        "intel:gap",
                        {
                            "domain": domain,
                            "gap": gap_desc,
                            "cc_technique": cc_technique,
                            "impact": impact,
                        },
                    )

            # Apply top high-impact gaps (max 2 per domain per round)
            high_impact = [g for g in gaps if g["impact"] == "high"]
            to_apply = high_impact[:2]

            # If no high-impact, apply 1 medium-impact gap
            if not to_apply:
                medium_impact = [g for g in gaps if g["impact"] == "medium"]
                to_apply = medium_impact[:1]

            for gap in to_apply:
                applied = await self._apply_improvement(gap, domain)
                if applied:
                    improvements_applied += 1
                    self.total_improvements += 1

        return improvements_applied

    async def _apply_improvement(self, gap: dict, domain: str) -> bool:
        """
        Write a minimal improvement to the relevant ShadowDev file.
        Returns True if the file was modified.
        """
        sd_path: str = gap["sd_path"]
        sd_rel: str = gap["sd_rel"]
        pattern: dict = gap["pattern"]
        cc_technique: str = gap["cc_technique"]

        # Build the comment block to append
        tag = "# [PromptIntel]"
        improvement_text = _build_improvement_block(domain, cc_technique, pattern)

        # Read current content — don't re-append if already present
        try:
            current = _read_file_safe(sd_path)
        except OSError:
            return False

        # Idempotency: skip if this exact cc_technique snippet was already applied
        snippet_key = cc_technique[:60].strip()
        if snippet_key and snippet_key.lower() in current.lower():
            return False

        # Append improvement to the file
        try:
            with open(sd_path, "a", encoding="utf-8") as fh:
                fh.write(f"\n\n{improvement_text}\n")
        except OSError as exc:
            logger.error("Failed to write improvement to %s: %s", sd_path, exc)
            return False

        description = f"Added CC-sourced {domain} pattern: {cc_technique[:80]}"
        self.improvements_log.append(
            {
                "domain": domain,
                "target_file": sd_rel,
                "description": description,
                "round": self.round,
            }
        )
        await self._emit(
            "intel:improvement",
            {
                "domain": domain,
                "target_file": sd_rel,
                "description": description,
            },
        )
        logger.info("PromptIntel improvement applied to %s: %s", sd_rel, description)
        return True

    # ------------------------------------------------------------------
    # Score update
    # ------------------------------------------------------------------

    async def _update_scores(self, all_findings: dict[str, list[dict]]) -> None:
        for domain, findings in all_findings.items():
            sd_rel = SHADOWDEV_COMPARE.get(domain, "")
            if not sd_rel:
                continue
            sd_path = os.path.join(_SD_ROOT, sd_rel.replace("/", os.sep))
            try:
                sd_content = _read_file_safe(sd_path)
            except OSError:
                continue
            self.scores[domain] = _score_domain(findings, sd_content)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _overall_score(self) -> int:
        if not self.scores:
            return 0
        return int(sum(self.scores.values()) / len(self.scores))

    async def _emit(self, event: str, data: dict) -> None:
        """Emit a Socket.IO event if sio is attached."""
        if self.sio is not None:
            try:
                await self.sio.emit(event, data)
            except Exception as exc:  # pragma: no cover
                logger.debug("PromptIntel emit error (%s): %s", event, exc)


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _read_file_safe(path: str, max_bytes: int = 512_000) -> str:
    """Read a file, capping at max_bytes to avoid loading huge files."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read(max_bytes)


def _build_improvement_block(domain: str, cc_technique: str, pattern: dict) -> str:
    """
    Build a clearly marked comment block to append to a ShadowDev file.
    Keeps changes minimal and auditable.
    """
    line_no = pattern.get("line_number", "?")
    ptype = pattern.get("type", "unknown")
    # Sanitize: strip any triple-quote sequences that could break the string
    safe_technique = cc_technique.replace('"""', "''").replace("'''", "''")[:400]

    lines = [
        "# [PromptIntel] -------------------------------------------------------",
        f"# Domain   : {domain}",
        f"# CC source : {ptype} (line ~{line_no})",
        "# Technique :",
    ]
    # Wrap the technique text at 78 chars
    for chunk in _wrap_text(safe_technique, 74):
        lines.append(f"#   {chunk}")
    lines.append("# [/PromptIntel] ------------------------------------------------------")

    return "\n".join(lines)


def _wrap_text(text: str, width: int) -> list[str]:
    """Simple word-wrap that respects width."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > width and current:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + 1
    if current:
        lines.append(" ".join(current))
    return lines or [""]
