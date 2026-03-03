---
name: code-review
description: Thorough code review covering correctness, style, security, and performance
version: "1.0"
---

## Code Review

Review the code described in $ARGUMENTS (or the most recently discussed files).

Use the checklist below. For each issue found, include:
- **Location**: `file.py:line`
- **Category**: one of the tags below
- **Severity**: Critical / High / Medium / Low / Suggestion
- **Finding**: clear explanation
- **Recommendation**: concrete improvement

---

### Correctness `[correctness]`
- [ ] Edge cases: empty inputs, None/null, zero, negative numbers, empty collections
- [ ] Off-by-one errors in loops and slices
- [ ] Async/await correctness (missing awaits, race conditions)
- [ ] Error handling: are exceptions caught at the right level? Are errors swallowed silently?
- [ ] Return values always defined on all code paths

### Security `[security]`
- [ ] User input validated and sanitised before use
- [ ] No shell injection (`shell=True` with user data)
- [ ] No SQL injection (use parameterised queries)
- [ ] Secrets not hardcoded or logged
- [ ] File path traversal prevented

### Performance `[perf]`
- [ ] N+1 query patterns (DB calls in loops)
- [ ] Unnecessary repeated computation (should be cached/pre-computed)
- [ ] Unbounded collections / infinite loops
- [ ] Blocking I/O in async context

### Maintainability `[maintainability]`
- [ ] Functions ≤ 30 lines (single responsibility)
- [ ] Meaningful names (no single-letter vars except loop counters)
- [ ] Magic numbers extracted to named constants
- [ ] Duplication that should be extracted to a helper

### Style `[style]`
- [ ] Consistent indentation and formatting
- [ ] Type hints present on public functions
- [ ] Docstrings on public APIs
- [ ] Unnecessary comments (code should speak for itself)

### Tests `[tests]`
- [ ] New code has corresponding unit tests
- [ ] Happy path and error path covered
- [ ] Tests are deterministic (no time/random dependence without mocking)

---

### Output Format

Summarise findings as:

```
## Code Review

### Summary
Files reviewed: ...
Total findings: N (C critical, H high, M medium, L low, S suggestions)

### Findings

#### [HIGH] [correctness] Off-by-one in loop — utils.py:47
**Finding:** The slice `items[0:n]` excludes the last item when n equals len(items).
**Recommendation:** Change to `items[:n]` or add a bounds check.

...
```

After the findings, add a **Positive Notes** section for well-done aspects.
