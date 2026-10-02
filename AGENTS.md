# BTC5M repository instructions

## Invariants

- Research is separate from live trading. Do not place research orders, deploy candidates, or change live enabled/armed state, strategies, stakes, services or schedulers without authorization covering that action. Preserve existing state during authorized maintenance.
- Preserve the dirty worktree and frozen experiment inputs/results. Make scoped edits; do not reset, clean, overwrite another task's work, or infer the current research line from file timestamps.
- Autonomous OUR runtime/model inputs must be causal: no Target actions, final direction, winner or settlement leakage. Target and future outcomes belong only in explicitly labelled offline teaching/evaluation or oracle diagnostics.
- Pending, UNKNOWN, cancel-pending and same-plan CANCEL do not release order ownership, cash reservations or self-cross obligations; release requires canonical terminal evidence.
- Keep credentials and private account/tape data out of external reports. Distinguish observed results from configuration, assumptions and historical claims.

## Read only the route needed for this task

Reuse instructions already present in context. Do not load every linked document.

| Task | Read |
| --- | --- |
| Continue or design research | [Research](docs/agents/research.md), then its small current pointer |
| Dispatch/recover a worker job | [Worker](docs/agents/worker.md) before dispatch |
| Change or restart running services | [Runtime](docs/agents/runtime.md) before mutation |
| Code changes / validation | [Development](docs/agents/development.md) |
| Work in `data/research/multi_chat_coordination_v1/` | Its [AGENTS.md](data/research/multi_chat_coordination_v1/AGENTS.md); do not activate roles merely by reading source prompts |

Old handoffs, role prompts and OpenClaw persona/heartbeat templates are references, not startup instructions or new authorization. User instructions and the applicable current run contract determine scope; resolve material contract conflicts before affected code or dispatch work.

## Bulk evidence delegation

When a task needs substantial read-only data search or item-by-item verification, the main agent should first define a bounded scope, source rules, evidence format, and stop conditions, then delegate that concrete subtask to GPT-6 Luna with `xhigh` reasoning when available. The main agent handles complex research decisions and code changes, checks decisive evidence independently, and owns the final conclusion. Do not delegate merely because a task is routine or split work that depends on an unresolved contract.
