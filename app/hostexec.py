"""Run local and host commands (host = nsenter into PID 1) with timeouts; never raises."""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from typing import Any

NSENTER = ["nsenter", "--target", "1", "--mount", "--uts", "--ipc", "--net", "--pid", "--"]


def run(cmd: list[str], timeout: float = 60, input_text: str | None = None,
        env: dict[str, str] | None = None) -> dict[str, Any]:
    start = time.monotonic()
    full_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout,
                              env=full_env, errors="replace")
        rc, out, err = proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        rc, out, err = 127, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", f"timed out after {timeout}s"
    except OSError as exc:
        rc, out, err = 126, "", str(exc)
    return {"ok": rc == 0, "rc": rc, "stdout": out, "stderr": err.strip(),
            "duration_ms": int((time.monotonic() - start) * 1000), "cmd": " ".join(shlex.quote(c) for c in cmd)}


def host_mode() -> str:
    """'nsenter' (default, inside the container) or 'direct' (development / tests on the host itself)."""
    return os.environ.get("DM_HOST_MODE", "nsenter")


def host(cmd: list[str], timeout: float = 60, input_text: str | None = None) -> dict[str, Any]:
    if host_mode() == "direct":
        return run(cmd, timeout, input_text)
    return run(NSENTER + cmd, timeout, input_text)


def host_sh(script: str, timeout: float = 60, input_text: str | None = None) -> dict[str, Any]:
    return host(["sh", "-c", script], timeout, input_text)
