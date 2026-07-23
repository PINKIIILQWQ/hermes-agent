# Profile Orchestrator

Route tasks from one Hermes profile to specialist profiles while recording a
local run ledger.  The orchestrator dispatches work to dedicated profiles,
collects results, and surfaces them back to the entry profile.

## Tools

| Tool | Purpose |
|------|---------|
| `profile_dispatch(task, route?, profile?, project?, skills?, timeout_seconds?, approve?)` | Route one task to a specialist profile |
| `profile_dispatch_many(tasks)` | Route several independent tasks in parallel |
| `profile_run(run_id?)` | Inspect the latest or a specific run |
| `profile_runs(status?)` | List recent runs |
| `profile_runwatch(run_id?, seconds?)` | Wait for a run to finish |
| `profile_followup(run_id?, task, route?, profile?, approve?)` | Add an additive child task to an existing run |
| `profile_approve(run_id?)` | Approve a held, high-risk run |
| `profile_cancel(run_id?)` | Cancel a running run |
| `profile_check_pending()` | Read completions not yet delivered as conversation messages |

## How it works

1. The entry profile calls `profile_dispatch(task=…)`.
2. The orchestrator creates a run record and spawns the target profile as a
   subprocess (`hermes -p <profile> chat -q <task>`).
3. The subprocess runs independently (minutes to hours).
4. When it finishes, the runner captures the output, writes a
   `result-packet.json`, and emits a pending notification.
5. A background watcher detects the notification and injects the result into
   the entry conversation on the next turn.

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

## Safety

- High-risk tasks (deploy, delete, push, …) are blocked until explicitly
  approved.
- The runner enforces a configurable timeout (default 6 hours).
- Target subprocess output is truncated for the summary.
- Private keys, tokens, and credentials are redacted from logs.
- Failed or cancelled runs are marked with an error summary and left in the
  run ledger for inspection.

## Install

Place this directory under `~/.hermes/plugins/hermes-orchestrator/` or configure
`plugins.external_paths` in `config.yaml`.

```yaml
plugins:
  external_paths:
    - path: /path/to/hermes-orchestrator
      depth: 1
```
