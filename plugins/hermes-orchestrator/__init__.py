"""
hermes-orchestrator — route tasks to specialist Hermes profiles.

Provides ``agent_dispatch`` / ``agent_run`` / ``agent_check_pending`` etc. as
Hermes tools.  The entry profile dispatches a task, the orchestrator spawns
the target profile as a subprocess, and the result is surfaced back on the
next conversation turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ── constants ───────────────────────────────────────────────────────────────

_PLUGIN_NAME = "profile-orchestrator"

# Storage: $HERMES_ORCHESTRATOR_HOME or $HERMES_HOME/orchestrator or ~/.hermes/orchestrator
_HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
_STORE_DIR = Path(
    os.environ.get("HERMES_ORCHESTRATOR_HOME")
    or _HERMES_HOME / "orchestrator"
)
_RUNS_DIR = _STORE_DIR / "runs"
_LOGS_DIR = _STORE_DIR / "logs"
_ARTIFACTS_DIR = _STORE_DIR / "artifacts"
_PENDING_DIR = _STORE_DIR / "pending_notifications"
_ENTRY_FEED = _STORE_DIR / "entry-feed.md"

# Default route→profile mapping
_DEFAULT_PROFILES = {
    "research": "gemini",
    "code": "qoder",
    "review": "codex",
    "fast": "codex-spark",
    "sink": "librarian",
}

_TOOLSET = "dispatch"

# ── helpers ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact(text: str) -> str:
    """Strip credentials and secrets from log/display text."""
    patterns = [
        re.compile(r"(?i)(api[_-]?key|token|secret|password|cookie)(\s*[:=]\s*)\S+"),
        re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{16,})"),
        re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b"),
    ]
    for pat in patterns:
        text = pat.sub(r"\1\2[REDACTED]", text)
    return text


def _new_run_id() -> str:
    return "run_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(3)


def _run_path(run_id: str) -> Path:
    return _RUNS_DIR / f"{run_id}.json"


def _load_run(run_id: str) -> Optional[Dict[str, Any]]:
    p = _run_path(run_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _save_run(run: Dict[str, Any]) -> None:
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _run_path(run["run_id"]).write_text(
        json.dumps(run, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _profile_exists(profile: str) -> bool:
    if profile == "default":
        return True
    return (_HERMES_HOME / "profiles" / profile / "config.yaml").exists()


def _resolve_profile(route: str, preferred: Optional[str] = None) -> str:
    if preferred and _profile_exists(preferred):
        return preferred
    default = _DEFAULT_PROFILES.get(route, "default")
    if _profile_exists(default):
        return default
    # fallback chain
    for fallback in ("gemini", "codex", "qoder", "codex-spark", "default"):
        if _profile_exists(fallback):
            return fallback
    return "default"


def _new_run(task: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    route = str(opts.get("route") or "research")
    profile = _resolve_profile(route, str(opts.get("profile") or ""))
    run_id = _new_run_id()
    now = _now()
    run = {
        "run_id": run_id,
        "status": "running",
        "route": route,
        "profile": profile,
        "project": str(opts.get("project") or ""),
        "skills": opts.get("skills"),
        "task": _redact(task),
        "timeout": opts.get("timeout", 21600),
        "created_at": now,
        "started_at": now,
        "ended_at": None,
        "exit_code": None,
        "summary_for_user": None,
        "log_path": str(_LOGS_DIR / f"{run_id}.log"),
        "result_packet_path": str(_ARTIFACTS_DIR / run_id / "result-packet.json"),
        # lineage
        "parent_run_id": opts.get("parent_run_id"),
        "root_run_id": opts.get("root_run_id") or run_id,
        "relation_type": "child" if opts.get("parent_run_id") else "root",
        "child_run_ids": [],
        "source_run_id": opts.get("source_run_id"),
        # safety
        "approval_required": False,
        "risk_reasons": [],
    }

    # Risk detection
    task_lower = task.lower()
    high_risk = ["deploy", "delete ", "git push", "force push", "rebase ",
                 "reset hard", "drop table", "rm -rf", "remove "]
    reasons = [w for w in high_risk if w in task_lower]
    if reasons:
        run["approval_required"] = True
        run["risk_reasons"] = reasons

    return run


def _dispatch_run(task: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    run = _new_run(task, opts)
    _save_run(run)
    if run["approval_required"]:
        return run
    return _spawn_runner(run)


def _spawn_runner(run: Dict[str, Any]) -> Dict[str, Any]:
    """Launch runner.py as a subprocess (non-blocking)."""
    plugin_dir = Path(__file__).parent.resolve()
    runner_script = plugin_dir / "runner.py"
    env = os.environ.copy()
    env["HERMES_ORCHESTRATOR_HOME"] = str(_STORE_DIR)
    env["ORCHESTRATOR_RUN_ID"] = run["run_id"]
    env["HERMES_PROFILE"] = run["profile"]
    skills = run.get("skills")
    if skills:
        env["ORCHESTRATOR_SKILLS"] = ",".join(skills)
    timeout = run.get("timeout", 21600)
    if timeout:
        env["ORCHESTRATOR_TIMEOUT"] = str(timeout)

    proc = subprocess.Popen(
        [sys.executable, str(runner_script)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    run["_runner_pid"] = proc.pid
    return run


# ── tool schemas ────────────────────────────────────────────────────────────

DISPATCH_SCHEMA = {
    "name": "profile_dispatch",
    "description": "Route a task to a specialist Hermes profile. The orchestrator spawns the target profile as a subprocess and surfaces the result on the next turn.",
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Task description for the target profile"},
            "project": {"type": "string", "description": "Optional project name for grouping"},
            "route": {"type": "string", "enum": ["research", "code", "review", "fast", "custom"], "description": "Task category"},
            "profile": {"type": "string", "description": "Explicit target profile name"},
            "skills": {"type": "array", "items": {"type": "string"}, "description": "Optional skill names to load into the child profile"},
            "timeout_seconds": {"type": "integer", "description": "Optional per-task timeout in seconds (default 21600)"},
            "approve": {"type": "boolean", "description": "Set true to pre-approve high-risk operations"},
        },
        "required": ["task"],
    },
}

RUN_SCHEMA = {
    "name": "profile_run",
    "description": "Inspect the latest or a specific orchestrator run.",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to inspect. Omit for latest."},
        },
    },
}

RUNS_SCHEMA = {
    "name": "profile_runs",
    "description": "List recent orchestrator runs, optionally filtered by status.",
    "parameters": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "Filter: running, completed, failed, cancelled, waiting_approval"},
        },
    },
}

RUNWATCH_SCHEMA = {
    "name": "profile_runwatch",
    "description": "Wait briefly for an orchestrator run to complete.",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to watch"},
            "seconds": {"type": "integer", "description": "Max seconds to wait (1-300)", "default": 30},
        },
    },
}

DISPATCH_MANY_SCHEMA = {
    "name": "profile_dispatch_many",
    "description": "Route multiple independent tasks to specialist profiles in parallel.",
    "parameters": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "project": {"type": "string"},
                        "route": {"type": "string"},
                        "profile": {"type": "string"},
                        "approve": {"type": "boolean"},
                    },
                    "required": ["task"],
                },
            },
        },
        "required": ["tasks"],
    },
}

FOLLOWUP_SCHEMA = {
    "name": "profile_followup",
    "description": "Create an additive child task linked to an existing run.",
    "parameters": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Additional instruction"},
            "run_id": {"type": "string", "description": "Parent run ID"},
            "project": {"type": "string"},
            "route": {"type": "string"},
            "profile": {"type": "string"},
            "skills": {"type": "array", "items": {"type": "string"}, "description": "Optional skill names to load into the child profile"},
            "approve": {"type": "boolean"},
        },
        "required": ["task"],
    },
}

APPROVE_SCHEMA = {
    "name": "profile_approve",
    "description": "Approve a held high-risk orchestrator run.",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to approve"},
        },
    },
}

CANCEL_SCHEMA = {
    "name": "profile_cancel",
    "description": "Cancel a running orchestrator run.",
    "parameters": {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "Run ID to cancel"},
        },
    },
}

CHECK_PENDING_SCHEMA = {
    "name": "profile_check_pending",
    "description": "Read any orchestrator run completions not yet delivered as conversation messages.",
    "parameters": {"type": "object", "properties": {}},
}


# ── tool handlers ───────────────────────────────────────────────────────────

def _all_runs() -> List[Dict[str, Any]]:
    if not _RUNS_DIR.exists():
        return []
    runs = []
    for p in sorted(_RUNS_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            runs.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return runs


def _format_run_brief(run: Dict[str, Any]) -> str:
    parts = [
        run.get("run_id", "?"),
        f"· {run.get('status', '?')}",
        f"· {run.get('route', '?')}→{run.get('profile', '?')}",
        f"· {run.get('project', '')}" if run.get("project") else "",
        f"· {_redact(str(run.get('task', '')))[:80]}",
    ]
    return " ".join(p for p in parts if p)


def _tool_dispatch(args: Dict[str, Any], **_: Any) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        return "agent_dispatch requires a task."
    opts: Dict[str, Any] = {}
    if args.get("project"):
        opts["project"] = str(args["project"])
    if args.get("route"):
        opts["route"] = str(args["route"])
    if args.get("profile"):
        opts["profile"] = str(args["profile"])
    if args.get("skills"):
        opts["skills"] = list(args["skills"])
    if args.get("timeout_seconds"):
        opts["timeout"] = int(args["timeout_seconds"])
    if args.get("approve"):
        opts["preapproved"] = True
    run = _dispatch_run(task, opts)
    status = run.get("status", "?")
    msg = f"Dispatched {run['run_id']} → {run['profile']} ({run['route']})."
    if status == "waiting_approval":
        msg = f"Created {run['run_id']} but held for approval: {', '.join(run.get('risk_reasons') or [])}"
    return _dispatch_payload([run], msg)


def _tool_dispatch_many(args: Dict[str, Any], **_: Any) -> str:
    raw = args.get("tasks") or []
    if not raw:
        return "agent_dispatch_many requires a non-empty tasks array."
    runs = []
    for item in raw:
        task = str(item.get("task") or "").strip()
        if not task:
            continue
        opts: Dict[str, Any] = {}
        if item.get("project"):
            opts["project"] = str(item["project"])
        if item.get("route"):
            opts["route"] = str(item["route"])
        if item.get("profile"):
            opts["profile"] = str(item["profile"])
        if item.get("approve"):
            opts["preapproved"] = True
        runs.append(_dispatch_run(task, opts))
    if not runs:
        return "agent_dispatch_many did not receive any valid task."
    return _dispatch_payload(runs, f"Dispatched {len(runs)} task(s).")


def _tool_run(args: Dict[str, Any], **_: Any) -> str:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        all_runs = _all_runs()
        if not all_runs:
            return "No runs yet."
        run = all_runs[0]
    else:
        run = _load_run(run_id)
        if not run:
            return f"Run {run_id} not found."
    return json.dumps(run, indent=2, ensure_ascii=False, default=str)


def _tool_runs(args: Dict[str, Any], **_: Any) -> str:
    status_filter = str(args.get("status") or "").strip().lower()
    all_runs = _all_runs()
    if status_filter:
        all_runs = [r for r in all_runs if r.get("status", "").lower() == status_filter]
    summary = [
        {
            "run_id": r["run_id"],
            "status": r.get("status"),
            "route": r.get("route"),
            "profile": r.get("profile"),
            "task": _redact(str(r.get("task", "")))[:120],
            "created_at": r.get("created_at"),
        }
        for r in all_runs[:20]
    ]
    return json.dumps(summary, indent=2, ensure_ascii=False)


def _tool_runwatch(args: Dict[str, Any], **_: Any) -> str:
    run_id = str(args.get("run_id") or "").strip()
    seconds = int(args.get("seconds") or 30)
    seconds = max(1, min(300, seconds))
    if not run_id:
        return "agent_runwatch requires a run_id."
    run = _load_run(run_id)
    if not run:
        return f"Run {run_id} not found."
    deadline = time.time() + seconds
    while time.time() < deadline:
        run = _load_run(run_id)
        if run and run.get("status") in ("completed", "failed", "cancelled"):
            return _format_run_brief(run)
        time.sleep(2)
    return f"Still running after {seconds}s.\n{_format_run_brief(run)}"


def _tool_followup(args: Dict[str, Any], **_: Any) -> str:
    task = str(args.get("task") or "").strip()
    if not task:
        return "agent_followup requires a task."
    parent_id = str(args.get("run_id") or "").strip()
    parent = _load_run(parent_id) if parent_id else None
    if not parent:
        return f"Parent run {parent_id} not found."
    opts: Dict[str, Any] = {
        "parent_run_id": parent["run_id"],
        "root_run_id": parent.get("root_run_id") or parent["run_id"],
        "source_run_id": parent["run_id"],
        "route": args.get("route") or parent.get("route", "research"),
        "profile": args.get("profile") or parent.get("profile", ""),
    }
    if args.get("project"):
        opts["project"] = str(args["project"])
    if args.get("approve"):
        opts["preapproved"] = True
    run = _dispatch_run(task, opts)
    parent["child_run_ids"] = parent.get("child_run_ids") or []
    parent["child_run_ids"].append(run["run_id"])
    _save_run(parent)
    return f"Follow-up {run['run_id']} linked to parent {parent['run_id']}."


def _tool_runapprove(args: Dict[str, Any], **_: Any) -> str:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        all_runs = _all_runs()
        held = [r for r in all_runs if r.get("status") == "waiting_approval"]
        if not held:
            return "No runs waiting for approval."
        run_id = held[0]["run_id"]
    run = _load_run(run_id)
    if not run:
        return f"Run {run_id} not found."
    run["status"] = "running"
    _save_run(run)
    _spawn_runner(run)
    return f"Approved and dispatched {run_id}."


def _tool_cancel(args: Dict[str, Any], **_: Any) -> str:
    run_id = str(args.get("run_id") or "").strip()
    if not run_id:
        all_runs = _all_runs()
        running = [r for r in all_runs if r.get("status") == "running"]
        if not running:
            return "No running runs to cancel."
        run_id = running[0]["run_id"]
    run = _load_run(run_id)
    if not run:
        return f"Run {run_id} not found."
    pid = run.get("_runner_pid")
    if pid:
        try:
            if sys.platform == "darwin":
                subprocess.run(["kill", "-9", str(pid)], timeout=5, capture_output=True)
            else:
                os.kill(pid, 9)
        except Exception:
            pass
    run["status"] = "cancelled"
    run["ended_at"] = _now()
    _save_run(run)
    return f"Cancelled {run_id}."


def _tool_check_pending(args: Dict[str, Any], **_: Any) -> str:
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    paths = sorted(_PENDING_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime)
    if not paths:
        return "[]"
    results = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(data)
        except Exception:
            pass
    # Consume-on-read
    for path in paths:
        try:
            path.unlink()
        except Exception:
            pass
    return json.dumps(results, ensure_ascii=False, indent=2)


def _dispatch_payload(runs: List[Dict[str, Any]], message: str) -> str:
    return json.dumps({
        "schema": "hermes.orchestrator.dispatch_result.v1",
        "message": message,
        "runs": [{
            "run_id": r.get("run_id"),
            "status": r.get("status"),
            "route": r.get("route"),
            "profile": r.get("profile"),
            "project": r.get("project"),
            "parent_run_id": r.get("parent_run_id"),
            "root_run_id": r.get("root_run_id"),
            "relation_type": r.get("relation_type"),
            "child_run_ids": r.get("child_run_ids") or [],
            "task": _redact(str(r.get("task", ""))),
            "log_path": r.get("log_path"),
            "result_packet_path": r.get("result_packet_path"),
            "approval_required": r.get("approval_required"),
            "risk_reasons": r.get("risk_reasons"),
        } for r in runs],
        "todos": [_run_todo_item(r) for r in runs],
        "todo_instruction": "Immediately call the built-in todo tool with this full todos list (merge=false).",
        "entry_feed": str(_ENTRY_FEED),
    }, indent=2, ensure_ascii=False)


def _run_todo_item(run: Dict[str, Any]) -> Dict[str, str]:
    task = _redact(str(run.get("task") or "")).replace("\n", " ")
    if len(task) > 96:
        task = task[:93] + "..."
    status = str(run.get("status") or "?")
    route = str(run.get("route") or "?")
    profile = str(run.get("profile") or "?")
    _VERBS = {"research": "researching", "code": "coding", "review": "reviewing",
              "fast": "checking", "sink": "recording", "notify": "notifying"}
    _DONE = {"research": "researched", "code": "coded", "review": "reviewed",
             "fast": "checked", "sink": "recorded", "notify": "notified"}
    verb = _VERBS.get(route, "working")
    done = _DONE.get(route, "completed")
    if status == "running":
        tag = f"subagent: {profile} is {verb}"
    elif status == "completed":
        tag = f"done: {profile} {done}"
    elif status == "failed":
        tag = f"failed: {profile}"
    elif status == "waiting_approval":
        tag = "pending: your approval"
    else:
        tag = f"{status}: {profile}"
    return {
        "id": run.get("run_id") or "?",
        "content": f"{tag} · {task}",
        "status": "in_progress" if status == "running" else "completed",
    }


# ── pending notification system ────────────────────────────────────────────

def _pending_path(run_id: str) -> Path:
    return _PENDING_DIR / f"{run_id}.json"


def _write_pending_notification(run: Dict[str, Any]) -> None:
    _PENDING_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "route": run.get("route"),
        "profile": run.get("profile"),
        "task": _redact(str(run.get("task") or "")),
        "summary_for_user": run.get("summary_for_user"),
        "result_packet_path": run.get("result_packet_path"),
        "log_path": run.get("log_path"),
        "created_at": run.get("created_at"),
        "ended_at": run.get("ended_at"),
    }
    _pending_path(run["run_id"]).write_text(
        json.dumps(data, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _clean_pending_notification(run_id: str) -> None:
    p = _pending_path(run_id)
    if p.exists():
        p.unlink()


def _completion_message(run: Dict[str, Any]) -> str:
    status = run.get("status", "completed")
    route = run.get("route", "?")
    profile = run.get("profile", "?")
    summary = run.get("summary_for_user") or ""
    return (
        f"[orchestrator] **{profile}** completed ({route}, {status}).\n"
        + (f"\n{summary}" if summary else "")
    )


# ── entry watcher ───────────────────────────────────────────────────────────

def _start_entry_watcher(ctx) -> None:
    """Background thread: poll for completed runs and try to inject results."""

    def _loop():
        while True:
            _PENDING_DIR.mkdir(parents=True, exist_ok=True)
            for path in sorted(_PENDING_DIR.glob("run_*.json"), key=lambda p: p.stat().st_mtime):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.warning("watcher: failed to read pending %s: %s", path.name, exc)
                    continue
                run_id = data.get("run_id")
                if not run_id:
                    continue
                run = _load_run(run_id)
                if not run or run.get("status") in ("running",):
                    continue
                if run.get("status") in ("completed", "failed", "cancelled"):
                    try:
                        ctx.inject_message(_completion_message(run), role="user")
                        _clean_pending_notification(run_id)
                    except Exception as exc:
                        log.warning("watcher: inject_message failed for %s: %s", run_id, exc)
            time.sleep(5)

    thread = threading.Thread(target=_loop, name="orchestrator-entry-watcher", daemon=True)
    thread.start()
    log.info("orchestrator entry watcher started (5s poll)")


# ── register ────────────────────────────────────────────────────────────────

def register(ctx) -> None:
    _start_entry_watcher(ctx)

    tools = [
        ("profile_dispatch", DISPATCH_SCHEMA, _tool_dispatch, "🧭"),
        ("profile_dispatch_many", DISPATCH_MANY_SCHEMA, _tool_dispatch_many, "🧭"),
        ("profile_runs", RUNS_SCHEMA, _tool_runs, "📋"),
        ("profile_run", RUN_SCHEMA, _tool_run, "🔎"),
        ("profile_runwatch", RUNWATCH_SCHEMA, _tool_runwatch, "⏳"),
        ("profile_followup", FOLLOWUP_SCHEMA, _tool_followup, "➕"),
        ("profile_approve", APPROVE_SCHEMA, _tool_runapprove, "✅"),
        ("profile_cancel", CANCEL_SCHEMA, _tool_cancel, "🛑"),
        ("profile_check_pending", CHECK_PENDING_SCHEMA, _tool_check_pending, "📬"),
    ]
    for name, schema, handler, emoji in tools:
        ctx.register_tool(
            name=name,
            toolset=_TOOLSET,
            schema=schema,
            handler=handler,
            description=schema.get("description", ""),
            emoji=emoji,
        )

