"""
ImprovementOrchestrator — autonomous multi-round improvement engine.

Phases per round:
  1. Analysis  — parallel explorer workers scan the codebase
  2. Planning  — analyst worker synthesizes findings into a task list
  3. Implement — coder workers run sequentially (safe for file writes)
  4. Review    — reviewer worker verifies each implementation
  5. Loop      — repeat up to max_rounds or until all tasks pass

Never stops on errors — all exceptions are caught and reported.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

import config
from agent.team.worker_pool import WorkerPool
from agent.team.review_loop import ReviewResult
from agent.team.scratchpad import Scratchpad

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────

@dataclass
class ImprovementTask:
    """A single improvement task for a coder worker."""
    goal: str
    files: list[str] = field(default_factory=list)
    priority: int = 3           # 1=highest
    worker_id: str = ""         # Set on spawn
    result: str = ""            # Set on completion
    review: Optional[ReviewResult] = None


@dataclass
class ImprovementRound:
    """Results from one full analyze → implement → review cycle."""
    round_num: int
    tasks: list[ImprovementTask] = field(default_factory=list)
    analysis_summary: str = ""
    passed: int = 0
    failed: int = 0
    skipped: int = 0


# ─────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────

class ImprovementOrchestrator:
    """
    Autonomous improvement engine for a ShadowDev workspace.

    Usage::

        from agent.team.tools import _POOL
        orch = ImprovementOrchestrator(_POOL, workspace="/path/to/project")
        summary = await orch.run("Improve code quality and test coverage")
    """

    def __init__(
        self,
        pool: WorkerPool,
        workspace: str,
        max_rounds: int = 5,
        max_retries: int | None = None,
    ):
        self.pool = pool
        self.workspace = workspace
        self.max_rounds = max_rounds
        self.max_retries = max_retries if max_retries is not None else config.TEAM_MAX_RETRIES
        self.scratchpad = Scratchpad(
            root=f"{workspace}/{config.TEAM_SCRATCHPAD_DIR}"
        )
        self.rounds: list[ImprovementRound] = []

    # ── Public entry point ─────────────────────────────────

    async def run(self, goal: str) -> str:
        """
        Run the full improvement loop up to max_rounds.
        Returns a multi-line summary of what was accomplished.
        """
        logger.info(f"[orchestrator] goal: {goal[:120]}")
        self.scratchpad.write("goal.md", f"# Improvement Goal\n\n{goal}\n")

        lines: list[str] = []

        for rnum in range(1, self.max_rounds + 1):
            logger.info(f"[orchestrator] ── ROUND {rnum}/{self.max_rounds} ──")
            try:
                rnd = await self._run_round(rnum, goal)
            except Exception as exc:
                logger.error(f"[orchestrator] round {rnum} exception: {exc}", exc_info=True)
                lines.append(f"Round {rnum}: ERROR — {exc}")
                continue

            self.rounds.append(rnd)
            summary = (
                f"Round {rnum}: {len(rnd.tasks)} tasks — "
                f"{rnd.passed} PASSED, {rnd.failed} FAILED, {rnd.skipped} SKIPPED"
            )
            lines.append(summary)
            logger.info(f"[orchestrator] {summary}")

            if rnd.failed == 0 and rnd.passed > 0:
                logger.info("[orchestrator] all tasks passed — done early")
                break

        return "\n".join(lines) if lines else "No improvement rounds completed."

    # ── Round pipeline ─────────────────────────────────────

    async def _run_round(self, round_num: int, goal: str) -> ImprovementRound:
        rnd = ImprovementRound(round_num=round_num)

        # Phase 1: parallel analysis
        analysis = await self._analyze(goal, round_num)
        rnd.analysis_summary = analysis
        self.scratchpad.write(f"analysis-round{round_num}.md", analysis)

        # Phase 2: synthesize plan
        tasks = await self._plan_improvements(analysis, goal, round_num)
        if not tasks:
            logger.info(f"[orchestrator] round {round_num}: no tasks planned")
            return rnd
        rnd.tasks = tasks

        # Phase 3+4: implement then review (sequential — avoids write conflicts)
        for task in rnd.tasks:
            await self._implement_and_review(task, rnd)

        return rnd

    # ── Phase 1: Analysis ──────────────────────────────────

    async def _analyze(self, goal: str, round_num: int) -> str:
        """
        Spawn three parallel explorer workers to analyse different dimensions
        of the codebase. Synthesise results into a combined summary.
        """
        prior = self.scratchpad.read(f"analysis-round{round_num - 1}.md")
        prior_ctx = f"\n\nPrior findings:\n{prior[:800]}" if prior else ""

        analysis_prompts = [
            (
                "architecture-explorer",
                (
                    f"Analyse the project at {self.workspace}. "
                    "Map main modules, entry points, data flows. "
                    "Identify: files with most complexity, modules with no tests, "
                    "dead/duplicate code. "
                    f"Goal context: {goal}{prior_ctx}\n"
                    "Write findings to scratchpad file 'architecture.md'. "
                    "Do NOT modify source files."
                ),
            ),
            (
                "quality-explorer",
                (
                    f"Run code quality checks on {self.workspace}. "
                    "Use code_quality and lsp_diagnostics. "
                    "Find: files with D/F quality grade, type errors, "
                    "functions >50 lines, missing docstrings. "
                    f"Goal context: {goal}{prior_ctx}\n"
                    "Write findings to scratchpad file 'quality.md'. "
                    "Do NOT modify source files."
                ),
            ),
            (
                "test-explorer",
                (
                    f"Analyse test coverage for {self.workspace}. "
                    "Use run_tests and grep_search. "
                    "Find: untested public functions, failing tests, "
                    "test files importing non-existent modules. "
                    f"Goal context: {goal}{prior_ctx}\n"
                    "Write findings to scratchpad file 'tests.md'. "
                    "Do NOT modify source files."
                ),
            ),
        ]

        # Spawn all in parallel
        worker_ids = list(await asyncio.gather(*[
            self.pool.spawn(
                prompt=prompt,
                role="explorer",
                tools=self._explorer_tools(),
                description=name,
                max_steps=20,
            )
            for name, prompt in analysis_prompts
        ]))

        results = await self._collect_all(worker_ids, timeout=240)

        sections = []
        for i, wid in enumerate(worker_ids):
            name = analysis_prompts[i][0]
            sections.append(f"## {name}\n{results.get(wid, '(no result)')}")
        return "\n\n".join(sections)

    # ── Phase 2: Planning ──────────────────────────────────

    async def _plan_improvements(
        self,
        analysis: str,
        goal: str,
        round_num: int,
    ) -> list[ImprovementTask]:
        """
        Spawn an analyst worker that reads the scratchpad and produces a
        structured task list. Parse the output into ImprovementTask objects.
        """
        arch = self.scratchpad.read("architecture.md")
        quality = self.scratchpad.read("quality.md")
        tests = self.scratchpad.read("tests.md")

        prompt = (
            f"Goal: {goal}\n\n"
            f"Analysis findings (round {round_num}):\n\n"
            f"## Architecture\n{arch[:1500]}\n\n"
            f"## Quality\n{quality[:1500]}\n\n"
            f"## Tests\n{tests[:1500]}\n\n"
            "Produce an improvement plan with 3-5 specific, actionable tasks. "
            "For each task output EXACTLY this format (use --- as separator):\n"
            "TASK: <brief goal>\n"
            "FILES: <comma-separated file paths, or 'unknown'>\n"
            "PRIORITY: <integer 1-5, 1=highest>\n"
            "---\n\n"
            "Rules:\n"
            "- Only plan tasks supported by findings above — do not invent problems\n"
            "- Prefer concrete file:line issues over vague improvements\n"
            "- Do NOT modify any files"
        )

        analyst_id = await self.pool.spawn(
            prompt=prompt,
            role="general",
            tools=self._explorer_tools(),
            description="Plan improvements",
            max_steps=5,
        )
        results = await self._collect_all([analyst_id], timeout=120)
        plan_text = results.get(analyst_id, "")

        tasks = _parse_task_list(plan_text)
        logger.info(f"[orchestrator] planned {len(tasks)} tasks")
        return tasks

    # ── Phase 3+4: Implement → Review ──────────────────────

    async def _implement_and_review(
        self,
        task: ImprovementTask,
        rnd: ImprovementRound,
    ) -> None:
        """
        Implement a task, then review it. On FAILED review, spawn a fresh
        coder with the issues baked into the prompt (up to max_retries).
        """
        issues_ctx = ""

        for attempt in range(self.max_retries + 1):
            # Build coder prompt
            files_ctx = f"\nAffected files: {', '.join(task.files)}" if task.files else ""
            scratchpad_note = (
                f"\nScratchpad at {self.workspace}/{config.TEAM_SCRATCHPAD_DIR}: "
                f"read architecture.md, quality.md, tests.md for context."
            )
            retry_note = (
                f"\n\nPrevious attempt FAILED with these issues — fix them:\n{issues_ctx}"
                if issues_ctx else ""
            )
            prompt = (
                f"Task: {task.goal}{files_ctx}{scratchpad_note}{retry_note}\n\n"
                "Steps:\n"
                "1. Read the relevant files\n"
                "2. Make targeted, minimal changes (fix root cause, not symptoms)\n"
                "3. Run tests for the changed module — fix any failures\n"
                "4. Commit the changes\n"
                "5. Report: what changed, test results, commit hash\n\n"
                "Do not break existing tests. Do not refactor beyond task scope."
            )

            try:
                wid = await self.pool.spawn(
                    prompt=prompt,
                    role="coder",
                    tools=self._coder_tools(),
                    description=f"{task.goal[:45]} (att {attempt + 1})",
                    max_steps=config.TEAM_WORKER_MAX_STEPS,
                )
                task.worker_id = wid
            except Exception as exc:
                logger.error(f"[orchestrator] spawn coder failed: {exc}")
                rnd.skipped += 1
                return

            # Wait for coder to finish
            impl_results = await self._collect_all([wid], timeout=300)
            task.result = impl_results.get(wid, "(no result)")

            # Review
            review = await self._run_reviewer(task.files, task.result)
            task.review = review

            if review.passed:
                rnd.passed += 1
                logger.info(f"[orchestrator] PASSED: {task.goal[:50]}")
                return

            logger.info(
                f"[orchestrator] FAILED (attempt {attempt + 1}): "
                f"{task.goal[:40]} — {review.issues[:80]}"
            )
            issues_ctx = review.issues

            if attempt >= self.max_retries:
                rnd.failed += 1
                return

        rnd.failed += 1

    async def _run_reviewer(
        self,
        files: list[str],
        impl_result: str,
    ) -> ReviewResult:
        """Spawn a reviewer worker and return its verdict."""
        files_list = ", ".join(files) if files else "changed files"
        prompt = (
            f"Review changes to: {files_list}\n\n"
            f"Implementation report:\n{impl_result[:1200]}\n\n"
            "Verification steps:\n"
            "1. Read each changed file\n"
            "2. Run lsp_diagnostics — investigate every error (do not dismiss as unrelated)\n"
            "3. Run run_tests — report all failures\n"
            "4. Check edge cases and error paths the implementation may have missed\n\n"
            "First line MUST be 'PASSED ✅' or 'FAILED ❌'. "
            "Then list specific issues with file:line references.\n"
            "Prove the code works — do not rubber-stamp."
        )
        reviewer_id = await self.pool.spawn(
            prompt=prompt,
            role="reviewer",
            tools=self._reviewer_tools(),
            description=f"Review {files_list[:40]}",
            max_steps=20,
        )
        results = await self._collect_all([reviewer_id], timeout=180)
        result_text = results.get(reviewer_id, "FAILED ❌ (reviewer timed out)")
        passed = "PASSED" in result_text
        return ReviewResult(
            passed=passed,
            exhausted=False,
            issues="" if passed else result_text,
        )

    # ── Queue helper ───────────────────────────────────────

    async def _collect_all(
        self,
        worker_ids: list[str],
        timeout: float = 180,
    ) -> dict[str, str]:
        """
        Poll notification_queue until all worker_ids report or timeout expires.
        Notifications not matching any requested ID are put back in the queue.
        Returns {worker_id: result_text}.
        """
        results: dict[str, str] = {}
        pending = set(worker_ids)
        held: list[str] = []
        deadline = asyncio.get_running_loop().time() + timeout

        while pending and asyncio.get_running_loop().time() < deadline:
            try:
                notif = await asyncio.wait_for(
                    self.pool.notification_queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            matched = next((wid for wid in pending if wid in notif), None)
            if matched:
                pending.discard(matched)
                start = notif.find("<result>")
                end = notif.find("</result>")
                result = notif[start + 8:end].strip() if start != -1 and end != -1 else notif
                results[matched] = result
            else:
                held.append(notif)

        # Return unmatched notifications to queue (coordinator may need them)
        for h in held:
            await self.pool.notification_queue.put(h)

        # Mark timed-out workers
        for wid in pending:
            results[wid] = f"(timeout after {timeout}s)"
            logger.warning(f"[orchestrator] worker {wid[:8]} timed out")

        return results

    # ── Tool sets ──────────────────────────────────────────

    def _explorer_tools(self) -> list:
        try:
            from agent.tools.code_search import code_search, grep_search, batch_read
            from agent.tools.file_ops import file_read, glob_search
            from agent.tools.lsp import lsp_diagnostics, lsp_symbols
            from agent.tools.code_quality import code_quality
            from agent.tools.test_runner import run_tests
            return [
                file_read, glob_search, code_search, grep_search, batch_read,
                lsp_diagnostics, lsp_symbols, code_quality, run_tests,
            ]
        except Exception:
            return []

    def _coder_tools(self) -> list:
        try:
            from agent.subagents import _get_general_tools
            return _get_general_tools()
        except Exception:
            return []

    def _reviewer_tools(self) -> list:
        try:
            from agent.tools.code_search import code_search, grep_search, batch_read
            from agent.tools.file_ops import file_read, glob_search
            from agent.tools.lsp import lsp_diagnostics, lsp_symbols
            from agent.tools.code_quality import code_quality
            from agent.tools.test_runner import run_tests
            return [
                file_read, glob_search, code_search, grep_search, batch_read,
                lsp_diagnostics, lsp_symbols, code_quality, run_tests,
            ]
        except Exception:
            return []


# ─────────────────────────────────────────────────────────────
# Task list parser
# ─────────────────────────────────────────────────────────────

def _parse_task_list(text: str) -> list[ImprovementTask]:
    """
    Parse structured TASK/FILES/PRIORITY blocks from analyst output.

    Expected format (blocks separated by ---):
        TASK: Fix null check in validate.py
        FILES: src/auth/validate.py, tests/test_validate.py
        PRIORITY: 1
    """
    tasks: list[ImprovementTask] = []

    for block in text.split("---"):
        block = block.strip()
        if not block:
            continue
        goal = files_list = priority = None
        for line in block.splitlines():
            if line.startswith("TASK:"):
                goal = line[5:].strip()
            elif line.startswith("FILES:"):
                raw = line[6:].strip()
                files_list = [
                    f.strip() for f in raw.split(",")
                    if f.strip() and f.strip().lower() != "unknown"
                ]
            elif line.startswith("PRIORITY:"):
                try:
                    priority = max(1, min(5, int(line[9:].strip())))
                except ValueError:
                    priority = 3
        if goal:
            tasks.append(ImprovementTask(
                goal=goal,
                files=files_list or [],
                priority=priority or 3,
            ))

    tasks.sort(key=lambda t: t.priority)
    return tasks[:5]  # cap at 5 per round
