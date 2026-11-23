"""Resource limits and environment scrubbing for untrusted subprocesses."""

from __future__ import annotations

import os
import sys

# Env vars that must NEVER reach a test subprocess (they belong to the app).
_SECRET_PREFIXES = ("ANTHROPIC_", "OPENAI_", "GITHUB_", "GH_", "CLOUDERA_", "CDP_", "AWS_")
_SECRET_EXACT = {"APP_API_KEY", "GITHUB_WEBHOOK_SECRET", "GITHUB_TOKEN"}

# Env vars worth passing through if present (kept minimal on purpose).
_ALLOW_PASSTHROUGH = ("HOME", "LANG", "LC_ALL", "TMPDIR", "TERM")

# Defaults (seconds / bytes).
INSTALL_TIMEOUT = 420.0
DISCOVER_TIMEOUT = 120.0
LAYER_TIMEOUT = 300.0
MAX_OUTPUT_BYTES = 2_000_000

# Per-process rlimits (best-effort; not all are enforced on every platform).
# A CPU limit below the wall-clock layer timeout kills long suites with no timeout reported; keep it above it.
CPU_SECONDS = int(LAYER_TIMEOUT) + 60
FSIZE_BYTES = 256 * 1024 * 1024  # 256 MiB


def scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A minimal environment with secrets removed, plus optional overrides."""
    env: dict[str, str] = {}
    path = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin")
    env["PATH"] = path
    for key in _ALLOW_PASSTHROUGH:
        if key in os.environ:
            env[key] = os.environ[key]
    env.setdefault("HOME", os.path.expanduser("~"))
    # Make Python output unbuffered so streamed lines arrive promptly.
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Never do a "--user" install inside the per-run venv. CAI ML Runtimes set
    # PIP_USER=1 (and can ship a pip.conf with user=true), which makes pip fail
    # with "Can not perform a '--user' install" inside a virtualenv. An env var
    # overrides the config file, so this forces it off; also ignore user
    # site-packages for clean isolation.
    env["PIP_USER"] = "0"
    env["PYTHONNOUSERSITE"] = "1"
    if extra:
        for key, value in extra.items():
            if _is_secret(key):
                continue
            env[key] = value
    return env


def _is_secret(key: str) -> bool:
    up = key.upper()
    return up in _SECRET_EXACT or any(up.startswith(p) for p in _SECRET_PREFIXES)


def preexec_limits():
    """Return a preexec_fn that applies rlimits, or None where unsupported.

    Skips RLIMIT_AS (address space) because it destabilizes the CPython
    interpreter on macOS; wall-clock timeouts remain the primary guard.
    """
    if sys.platform == "win32":
        return None

    def _apply() -> None:  # pragma: no cover - runs in the child process
        import resource

        for res, soft in ((resource.RLIMIT_CPU, CPU_SECONDS), (resource.RLIMIT_FSIZE, FSIZE_BYTES)):
            try:
                _, hard = resource.getrlimit(res)
                limit = soft if hard == resource.RLIM_INFINITY else min(soft, hard)
                resource.setrlimit(res, (limit, hard))
            except (ValueError, OSError):
                pass
        # Detach from the parent's process group so a timeout can kill the tree.
        try:
            os.setsid()
        except OSError:
            pass

    return _apply
