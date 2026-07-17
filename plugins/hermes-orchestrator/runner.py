#!/usr/bin/env python3
"""
Runner subprocess for hermes-orchestrator.

Reads run_id from env, spawns the target Hermes profile as a subprocess,
captures output, and writes the result packet.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# ── paths ───────────────────────────────────────────────────────────────────

STORE_DIR = Path(os.environ.get("HERMES_ORCHESTRATOR_HOME") or Path.home() / ".hermes" / "orchestrator")
RUN_ID = os.environ.get("ORCHESTRATOR_RUN_ID", "")
PROFILE = os.environ.get("HERMES_PROFILE", "default")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact(text: str) -> str:
    """Strip credentials from text."""
    import re
    patterns = [
        re.compile(r"(?i)(api[_-]?key|token|secret|password|cookie)(\s*[:=]\s*)\S+"),
        re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{16,})"),
    ]
    for pat in patterns:
        text = pat.sub(r"\1\2[REDACTED]", text)
    return text


def _load_run(run_id: str) -> Optional[Dict[str, Any]]:
    p = STORE_DIR / "runs" / f"{run_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save_run(run: Dict[str, Any]) -> None:
    p = STORE_DIR / "runs" / f"{run['run_id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(run, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _write_packet(run: Dict[str, Any], output: str) -> None:
    artifacts_dir = STORE_DIR / "artifacts" / run["run_id"]
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    packet = {
        "run_id": run["run_id"],
        "status": run.get("status"),
        "exit_code": run.get("exit_code"),
        "profile": run.get("profile"),
        "route": run.get("route"),
        "task": _redact(str(run.get("task") or "")),
        "summary_for_user": run.get("summary_for_user", output[-800:800]),
        "output_truncated": len(output) > 1800,
        "completed_at": _now(),
    }
    (artifacts_dir / "result-packet.json").write_text(
        json.dumps(packet, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _write_brief(run: Dict[str, Any], output: str) -> None:
    artifacts_dir = STORE_DIR / "artifacts" / run["run_id"]
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    brief_path = artifacts_dir / "brief.md"
    summary = run.get("summary_for_user") or output[-1200:800] or "No output"
    brief_path.write_text(f"# {run['run_id']}\n\n{_redact(summary)}\n", encoding="utf-8")


def _write_pending_notification(run: Dict[str, Any]) -> None:
    pending_dir = STORE_DIR / "pending_notifications"
    pending_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "route": run.get("route"),
        "profile": run.get("profile"),
        "task": _redact(str(run.get("task") or "")),
        "summary_for_user": run.get("summary_for_user"),
        "result_packet_path": str(STORE_DIR / "artifacts" / run["run_id"] / "result-packet.json"),
        "log_path": run.get("log_path"),
        "created_at": run.get("created_at"),
        "ended_at": run.get("ended_at"),
    }
    (pending_dir / f"{run['run_id']}.json").write_text(
        json.dumps(data, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _run_hermes_task(task: str) -> str:
    """Spawn the target Hermes profile and return its output."""
    task_quoted = shlex.quote(task)
    cmd = [
        sys.executable, "-m", "hermes_cli.main",
        "--profile", PROFILE,
        "chat", "-q", task,
        "--source", "hermes-orchestrator",
        "-t", "web,browser,terminal,file,code_execution,todo,clarify",
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=60 * 60 * 6,  # 6-hour max
    )
    return proc.stdout or ""


def main() -> int:
    if not RUN_ID:
        log.error("ORCHESTRATOR_RUN_ID not set")
        return 1

    run = _load_run(RUN_ID)
    if not run:
        log.error("Run %s not found", RUN_ID)
        return 1

    task = run.get("task", "")
    log_path = run.get("log_path", "")
    if log_path:
        log_dir = Path(log_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)

    output = ""
    try:
        output = _run_hermes_task(task)
        run["exit_code"] = 0
        run["status"] = "completed"
        run["result_summary"] = output[-1800:] if output else None
    except subprocess.TimeoutExpired:
        run["exit_code"] = None
        run["status"] = "failed"
        run["error_summary"] = "timeout after 6h"
    except Exception as exc:
        run["exit_code"] = None
        run["status"] = "failed"
        run["error_summary"] = _redact(str(exc))
    finally:
        run["ended_at"] = _now()
        _write_packet(run, output)
        _write_brief(run, output)
        _save_run(run)
        # Signal completion to the entry watcher
        _write_pending_notification(run)

    return 0 if run.get("status") == "completed" else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    raise SystemExit(main())
