"""
Cross-platform desktop notification support.
Uses plyer if available (Windows/macOS/Linux), falls back to win10toast on Windows,
then falls back to silent no-op.
"""
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)


def notify(title: str, message: str, duration: int = 5) -> None:
    """Send a desktop notification. Silently fails if no notification system available."""
    # Truncate long messages
    if len(message) > 200:
        message = message[:197] + "..."

    try:
        import plyer
        plyer.notification.notify(title=title, message=message, timeout=duration)
        return
    except ImportError:
        pass
    except Exception as e:
        logger.debug("plyer notification failed: %s", e)

    if sys.platform == "win32":
        try:
            from win10toast import ToastNotifier
            ToastNotifier().show_toast(title, message, duration=duration, threaded=True)
            return
        except ImportError:
            pass
        except Exception as e:
            logger.debug("win10toast notification failed: %s", e)

    if sys.platform == "linux":
        try:
            subprocess.run(
                ["notify-send", "--expire-time", str(duration * 1000), title, message],
                capture_output=True, timeout=3
            )
            return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if sys.platform == "darwin":
        try:
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=3)
            return
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
