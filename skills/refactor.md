---
name: refactor
description: Guided refactoring — identify smells, propose safe incremental improvements
version: "1.0"
---

## Refactoring Workflow

Refactor the code described in $ARGUMENTS. Follow the safe, incremental process below.

**Principle:** Refactoring changes structure, not behaviour. Tests must pass before and after each step.

---

### Phase 1 — Understand Before Changing

Before touching any code:

1. Read all files involved end-to-end.
2. Run the existing tests to establish a green baseline:
   ```
   python -m pytest -x -q
   ```
3. Note which tests cover the code you're changing. If there are none, write minimal characterisation tests first.

### Phase 2 — Identify Smells

Evaluate the code against these patterns:

| Smell | Indicator |
|-------|-----------|
| Long method | > 30 lines, multiple levels of nesting |
| God class | > 200 lines, > 10 methods, multiple responsibilities |
| Duplicate code | Same logic in 2+ places |
| Deep nesting | > 3 levels of if/for/try |
| Feature envy | Method uses another class's data more than its own |
| Magic numbers | Unexplained numeric/string literals |
| Long parameter list | > 4 parameters — consider a data class |
| Shotgun surgery | One change requires edits in many files |
| Dead code | Unreachable branches, unused imports, unused functions |

List every smell you find before proposing any changes.

### Phase 3 — Plan Incremental Steps

Break the refactoring into the smallest safe units. Each step must:
- Change only structure, not observable behaviour
- Leave the tests green
- Be independently reviewable

Order of preference (lowest risk first):
1. Rename (variable, function, class) — pure search/replace
2. Extract constant / magic number
3. Extract helper function (no logic change, just move)
4. Simplify condition (de-Morgan's law, early return / guard clause)
5. Remove duplication (DRY by extracting shared logic)
6. Split class / decompose large function

### Phase 4 — Execute Step by Step

For each step:
1. Make the change
2. Run tests: `python -m pytest -x -q`
3. If tests fail — revert immediately and diagnose
4. Commit atomically with a `refactor:` prefix

### Phase 5 — Final Review

After all steps:
- [ ] All tests still pass
- [ ] No new smells introduced
- [ ] Public API unchanged (or documented migration path)
- [ ] No performance regression

!`git diff --stat HEAD`

Summarise what was changed and why each improvement matters.
