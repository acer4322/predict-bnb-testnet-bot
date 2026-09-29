# Codex AGENTS loading verification — 2026-09-20

Installed binary: `codex-cli 0.155.0-alpha.9.2`. Used `codex debug prompt-input` at each working directory; no model invocation, task creation, worker job or service restart. Parsed the actual assembled messages, matched the complete normalized file bodies and checked ancestor-before-descendant order. Raw prompts were not saved.

## Measured project instruction reduction

- Root AGENTS: **7,327 → 2,057 bytes**, **131 → 23 lines**; **5,270 bytes / 71.93% removed**.
- Rough English-text proxy: about 1,832 → 514 tokens (characters ÷ 4). This is not an Astra tokenizer count, billing measurement or guarantee of whole-session savings.
- The table measures instruction-block characters, including the cwd wrapper. Conditional workflow bodies were absent from all automatic prompt inputs; mentioning their links did not inject their contents.

| Session working directory | Exact file bodies loaded, in order | Before chars | After chars | Result |
| --- | --- | ---: | ---: | --- |
| `.` | root AGENTS | 7507 | 2237 | PASS |
| `src` | root AGENTS | 7511 | 2241 | PASS |
| `tools` | root AGENTS | 7513 | 2243 | PASS |
| `dashboard` | root AGENTS | 7517 | 2247 | PASS |
| `dashboard-v2` | root AGENTS | 7520 | 2250 | PASS |
| `tests` | root AGENTS | 7513 | 2243 | PASS |
| `data/research` | root AGENTS | 7521 | 2251 | PASS |
| `data/research/multi_chat_coordination_v1` | root AGENTS + coordination AGENTS | 8179 | 2909 | PASS |
| `data/research/multi_chat_coordination_v1/bundle` | root AGENTS + coordination AGENTS | 8186 | 2916 | PASS |
| `data/research/v49_pending_distribution_depth_micro_training_20260920_r68` | root AGENTS | 7580 | 2310 | PASS |

## Conditional cost is not hidden

| On-demand document | Bytes |
| --- | ---: |
| `docs/agents/research.md` | 3,688 |
| `docs/agents/RESEARCH_CURRENT.md` | 1,940 |
| `docs/agents/worker.md` | 3,207 |
| `docs/agents/runtime.md` | 2,027 |
| `docs/agents/development.md` | 1,539 |
| `docs/agents/README.md` | 2,243 |

These are ordinary linked documents. A research task intentionally pays for its research route, current pointer and selected evidence; a dispatch also pays for the worker route. Do not add every conditional file to startup. The old SOUL/USER/IDENTITY/TOOLS/HEARTBEAT templates were not automatically loaded by this CLI before the edit, so un-routing them is not claimed as additional fixed savings.

## Discovery and scope

- Exactly two project AGENTS files were found outside vendor/generated/backup exclusions; no project AGENTS.override.md or SKILL.md was found. No new nested AGENTS was added. Global AGENTS is empty (0 bytes). Custom document-limit/fallback/root-marker settings were absent from the inspected user configuration.
- The configured local model is gpt-6-astra; this records configuration only, not proof of the model serving this desktop conversation. Model and reasoning settings were not changed.
- Starting at root includes root AGENTS only. Starting under coordination includes root then coordination in the same assembled instruction block; matching a header alone would have missed that distinction.
- Editing a child file from a root-started task does not cause this startup chain to be rebuilt. The root routing table therefore explicitly requires reading the coordination AGENTS when operating in its subtree.
- Each diagnostic invocation built a fresh prompt. The current desktop conversation still contains earlier instruction messages; no restart was forced. Use a new task/session for the clean startup savings.
- The observed local behavior agrees with [official instruction discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md). The documented default project instruction budget is 32 KiB; actual inclusion above was verified directly rather than inferred from that default.

## Preservation and link checks

- **1,043 tracked application/config files and selected frozen R68 artifacts** hashed before and after: unchanged. This is a bounded preservation audit, not a claim to hash every large/untracked dataset.
- Of 45 existing instruction/reference sources, only root AGENTS changed. Existing nested AGENTS, historical prompts/handoffs, generic templates and both external memory skills retained their hashes.
- The archived root text matches its original hash. Every new local Markdown link was checked; final verification also checks the report link after writing this file.
- No application code, research logic, data cohorts, worker settings, live rules, schedules, global instructions or memory files were edited. No trading tests or browser checks were needed for this instruction-only change.

Machine-readable evidence: [BEFORE.json](BEFORE.json), [AFTER.json](AFTER.json), [CLASSIFICATION.json](CLASSIFICATION.json). These contain only relevant metadata/hashes and classifications, not full host prompt dumps.
