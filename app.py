#!/usr/bin/env python3
"""Cloudera AI (CAI) Application entry point.

CAI runs the Application's *Script* with the ML Runtime's Python interpreter —
so the entry point must be a **Python file** (a shell script won't execute, and
the file picker usually only lists `.py`). Select **`app.py`** as the Script.

What it does:
  1. Installs the app's dependencies (idempotent — CAI Applications do not
     auto-install `requirements.txt`). Set `AGENTICQA_SKIP_PIP=1` to skip this on
     restart once the project already has them (much faster restarts).
  2. Serves the FastAPI app on `CDSW_APP_PORT`, bound to 127.0.0.1, **single
     worker** — the event bus, run engine and store are in-process singletons,
     so multiple workers would break SSE fan-out and duplicate runs.

Config comes from the Application's environment variables (AGENTICQA_*).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# CAI executes the Application Script inside an IPython-style engine where
# ``__file__`` is undefined; fall back to the working directory (CAI runs the
# app from the project root).
try:
    ROOT = Path(__file__).resolve().parent
except NameError:
    ROOT = Path(os.getcwd())
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "src"))

# 1. Dependencies (idempotent). Editable install pulls deps via pyproject; if that
#    fails for any reason, fall back to requirements.txt. Skippable for fast restarts.
if os.environ.get("AGENTICQA_SKIP_PIP") != "1":
    if subprocess.run([sys.executable, "-m", "pip", "install", "-e", "."]).returncode != 0:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

# 2. Serve (imports after install so the packages are present).
import asyncio  # noqa: E402
import threading  # noqa: E402

import uvicorn  # noqa: E402

port = int(os.environ.get("CDSW_APP_PORT", "8100"))
config = uvicorn.Config("agenticqa.main:app", host="127.0.0.1", port=port, workers=1, log_level="info")
server = uvicorn.Server(config)

# CAI executes the Script inside a running asyncio loop (IPython engine), where
# uvicorn.run()'s asyncio.run() raises "cannot be called from a running event
# loop". Serve in a dedicated thread with its own event loop, then block the
# main thread so the app stays up. Works both ways (running loop or plain process).
def _serve() -> None:
    asyncio.run(server.serve())


_thread = threading.Thread(target=_serve, name="agenticqa-uvicorn")
_thread.start()
_thread.join()
