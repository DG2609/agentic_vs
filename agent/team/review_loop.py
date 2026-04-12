"""
Auto review-and-retry loop for Agent Teams.

After an implementation worker finishes, the coordinator calls
trigger_review(). This spawns a reviewer worker, waits for its
verdict, and retries up to max_retries times on FAILED results.
"""
import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_REVIEW_TIMEOUT = 300


@dataclass
class ReviewResult:
    passed: bool
    exhausted: bool = False
    issues: str = ""


class ReviewLoop:
    """Orchestrates review-and-retry for a given implementation worker."""

    def __init__(self, pool, max_retries: int = 3):
        self.pool = pool
        self.max_retries = max_retries

    async def trigger_review(
        self,
        impl_worker_id: str,
        changed_files: list[str],
    ) -> ReviewResult:
        """
        Spawn a reviewer for the changed files. On FAILED, send a retry
        message back to the implementation worker. Repeat up to max_retries.
        Returns ReviewResult with passed/exhausted/issues.
        """
        files_list = ", ".join(changed_files)

        for attempt in range(self.max_retries + 1):
            reviewer_prompt = (
                f"Review the following changed files for correctness:\n{files_list}\n\n"
                "Steps:\n"
                "1. Read each file\n"
                "2. Run lsp_diagnostics on each file — report errors/warnings\n"
                "3. Run run_tests — report failures\n"
                "4. Check for missing error handling or obvious bugs\n\n"
                "First line of your response MUST be 'PASSED ✅' or 'FAILED ❌'.\n"
                "Then list specific issues with file:line references."
            )

            reviewer_id = await self.pool.spawn(
                prompt=reviewer_prompt,
                role="reviewer",
                tools=self._get_reviewer_tools(),
                description=f"Review attempt {attempt + 1}/{self.max_retries + 1}",
            )

            verdict, issues = await self._wait_for_verdict(reviewer_id)

            if verdict == "passed":
                logger.info(f"[review_loop] PASSED on attempt {attempt + 1}")
                return ReviewResult(passed=True, issues=issues)

            logger.info(f"[review_loop] FAILED attempt {attempt + 1}: {issues[:100]}")

            if attempt >= self.max_retries:
                return ReviewResult(passed=False, exhausted=True, issues=issues)

            retry_prompt = (
                f"A reviewer found issues with the implementation (attempt {attempt + 1}/{self.max_retries}).\n\n"
                f"Changed files: {files_list}\n\n"
                f"Issues found:\n{issues}\n\n"
                f"Fix ALL of these issues. Read each affected file first, make the targeted changes, "
                f"run tests to verify, commit the fix, and report the commit hash."
            )
            impl_worker_id = await self.pool.spawn(
                prompt=retry_prompt,
                role="coder",
                tools=self._get_coder_tools(),
                description=f"Fix retry {attempt + 1}/{self.max_retries}",
            )

        return ReviewResult(passed=False, exhausted=True, issues="max retries exceeded")

    async def _wait_for_verdict(self, reviewer_id: str) -> tuple[str, str]:
        """
        Poll notification_queue until we find a notification for reviewer_id.
        Returns ('passed'|'failed', issues_text).
        """
        deadline = asyncio.get_running_loop().time() + _REVIEW_TIMEOUT
        held: list[str] = []

        while asyncio.get_running_loop().time() < deadline:
            try:
                notif = await asyncio.wait_for(
                    self.pool.notification_queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if reviewer_id in notif:
                for h in held:
                    await self.pool.notification_queue.put(h)
                result_text = self._extract_result(notif)
                if "PASSED" in result_text:
                    return "passed", result_text
                return "failed", result_text
            else:
                held.append(notif)

        for h in held:
            await self.pool.notification_queue.put(h)
        return "failed", f"Reviewer {reviewer_id[:8]} timed out after {_REVIEW_TIMEOUT}s"

    def _extract_result(self, notification: str) -> str:
        """Extract <result>...</result> from notification XML."""
        start = notification.find("<result>")
        end = notification.find("</result>")
        if start != -1 and end != -1:
            return notification[start + 8:end].strip()
        return notification

    def _get_reviewer_tools(self) -> list:
        """Reviewer tool set — read-only + diagnostics + tests."""
        try:
            from agent.tools.code_search import code_search, grep_search, batch_read
            from agent.tools.file_ops import file_read, glob_search
            from agent.tools.lsp import lsp_diagnostics, lsp_symbols
            from agent.tools.code_quality import code_quality
            from agent.tools.test_runner import run_tests
            return [
                file_read, glob_search,
                code_search, grep_search, batch_read,
                lsp_diagnostics, lsp_symbols,
                code_quality, run_tests,
            ]
        except Exception:
            return []

    def _get_coder_tools(self) -> list:
        """Coder tool set — read + write + tests + git."""
        try:
            from agent.tools.code_search import code_search, grep_search, batch_read
            from agent.tools.file_ops import file_read, file_write, file_edit, glob_search
            from agent.tools.test_runner import run_tests
            from agent.tools.git import git_add, git_commit, git_status, git_diff
            return [
                file_read, file_write, file_edit, glob_search,
                code_search, grep_search, batch_read,
                run_tests, git_add, git_commit, git_status, git_diff,
            ]
        except Exception:
            return []
