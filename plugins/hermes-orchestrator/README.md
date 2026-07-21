# Hermes Orchestrator Plugin

Route tasks from one Hermes profile to specialist profiles while recording a
local run ledger.  The orchestrator dispatches work to dedicated profiles
(gemini for research, qoder for code, …), collects results, and surfaces
them back to the entry profile.

## Tools

| Tool | Purpose |
|------|---------|
| `agent_dispatch(task, route?, profile?, project?)` | Route one task to a specialist profile |
| `agent_dispatch_many(tasks)` | Route several independent tasks in parallel |
| `agent_run(run_id?)` | Inspect the latest or a specific run |
| `agent_runs(status?)` | List recent runs |
| `agent_runwatch(run_id?, seconds?)` | Wait for a run to finish |
| `agent_followup(run_id, task)` | Add an additive child task to an existing run |
| `agent_sink(run_id?)` | Archive a run's result packet as a journal entry |
| `agent_runapprove(run_id?)` | Approve a held run |
| `agent_cancel(run_id?)` | Cancel a running run |
| `agent_check_pending()` | Read completions not yet delivered as conversation messages |

## How it works

1. The entry profile calls `agent_dispatch(task=…)`.
2. The orchestrator creates a run record and spawns the target profile as a
   subprocess (`hermes -p <profile> chat -Q …`).
3. The subprocess runs independently (minutes to hours).
4. When it finishes, the runner captures the output and writes a
   `result-packet.json`.
5. A background watcher creates a pending notification.
6. The entry profile picks up the notification at the start of its next
   conversation turn and reports the result.

## Configuration

Storage defaults to `$HERMES_HOME/orchestrator/`.  Override with
`HERMES_ORCHESTRATOR_HOME`.

Default route→profile mapping:

| Route | Profile |
|-------|---------|
| `research` | gemini |
| `code` | qoder |
| `review` | codex |
| `fast` | codex-spark |
| `sink` | (configured sink profile) |

## Safety

- High-risk tasks (deploy, delete, push, …) are blocked until explicitly
  approved.
- The runner enforces a 6-hour timeout.
- Target subprocess output is truncated to 1800 chars for the summary.
- Private keys, tokens, and credentials are redacted from logs.
