"""Tests for the error codes registry and TelemetrySafeError."""
import pytest


# ── Error registry ───────────────────────────────────────────────────────────

def test_errors_has_unknown():
    from agent.constants.errors import Errors
    assert Errors.UNKNOWN == 1


def test_errors_has_tool_timeout():
    from agent.constants.errors import Errors
    assert Errors.TOOL_TIMEOUT == 100


def test_errors_has_free_usage_limit():
    from agent.constants.errors import Errors
    assert Errors.FREE_USAGE_LIMIT == 403


def test_errors_has_file_path_traversal():
    from agent.constants.errors import Errors
    assert Errors.FILE_PATH_TRAVERSAL == 204


def test_name_of_returns_correct_name():
    from agent.constants.errors import Errors
    assert Errors.name_of(100) == "TOOL_TIMEOUT"


def test_name_of_returns_unknown_for_missing():
    from agent.constants.errors import Errors
    assert Errors.name_of(9999) == "UNKNOWN"


def test_errors_no_duplicate_ids():
    """All error codes must be unique."""
    from agent.constants.errors import Errors
    codes = [v for k, v in vars(Errors.__class__).items()
             if isinstance(v, int) and not k.startswith("_")]
    assert len(codes) == len(set(codes)), "Duplicate error codes detected"


def test_errors_all_positive():
    from agent.constants.errors import Errors
    codes = [v for k, v in vars(Errors.__class__).items()
             if isinstance(v, int) and not k.startswith("_")]
    assert all(c > 0 for c in codes)


# ── TelemetrySafeError ───────────────────────────────────────────────────────

def test_telemetry_safe_error_creates_message():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.TOOL_TIMEOUT, "timed out")
    assert "[E100]" in str(err)
    assert "TOOL_TIMEOUT" in str(err)
    assert "timed out" in str(err)


def test_telemetry_safe_error_code_attribute():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.FILE_NOT_FOUND, "missing file")
    assert err.code == Errors.FILE_NOT_FOUND


def test_telemetry_safe_error_details():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.UNKNOWN, "details here")
    assert err.details == "details here"


def test_telemetry_safe_error_is_retryable_for_timeout():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.TOOL_TIMEOUT, "timeout")
    assert err.is_retryable is True


def test_telemetry_safe_error_not_retryable_for_traversal():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.FILE_PATH_TRAVERSAL, "traversal")
    assert err.is_retryable is False


def test_telemetry_safe_error_is_fatal_for_free_limit():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.FREE_USAGE_LIMIT, "free limit")
    assert err.is_fatal is True


def test_telemetry_safe_error_not_fatal_for_timeout():
    from agent.constants.errors import Errors, TelemetrySafeError
    err = TelemetrySafeError(Errors.LLM_TIMEOUT, "slow")
    assert err.is_fatal is False


def test_telemetry_safe_error_with_cause():
    from agent.constants.errors import Errors, TelemetrySafeError
    cause = ValueError("original error")
    err = TelemetrySafeError(Errors.FILE_READ_ERROR, "read failed", cause=cause)
    assert err.cause is cause
    assert err.__cause__ is cause


def test_telemetry_safe_error_is_exception():
    from agent.constants.errors import Errors, TelemetrySafeError
    with pytest.raises(TelemetrySafeError):
        raise TelemetrySafeError(Errors.LLM_API_ERROR, "api failed")


def test_telemetry_safe_error_log_does_not_raise(caplog):
    from agent.constants.errors import Errors, TelemetrySafeError
    import logging
    err = TelemetrySafeError(Errors.TOOL_TIMEOUT, "timed out")
    err.log()  # should not raise
