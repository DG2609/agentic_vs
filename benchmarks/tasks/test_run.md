---
name: test_run
expected_tools:
  - file_write
  - run_tests
max_time_s: 120
difficulty: medium
---

# Test Run Benchmark

Write a calculator module and pytest tests, then run the tests.

## Instructions

1. Create `calculator.py` in the workspace with the following functions:
   - `add(a, b)` — returns the sum of a and b.
   - `subtract(a, b)` — returns the difference of a and b.
   - `multiply(a, b)` — returns the product of a and b.
   - `divide(a, b)` — returns the quotient of a and b. Raises `ValueError` if b is zero.

2. Create `test_calculator.py` with pytest tests that cover:
   - `test_add` — verifies `add(2, 3) == 5`.
   - `test_subtract` — verifies `subtract(10, 4) == 6`.
   - `test_multiply` — verifies `multiply(3, 7) == 21`.
   - `test_divide` — verifies `divide(10, 2) == 5.0`.
   - `test_divide_by_zero` — verifies `divide(1, 0)` raises `ValueError`.

3. Run the tests using the test runner and confirm all tests pass.

## Acceptance criteria

- Both `calculator.py` and `test_calculator.py` exist in the workspace.
- The test runner was invoked (the `run_tests` tool was called).
- All tests pass (5 passed, 0 failed).
