# Agent instruction maintenance

Root [AGENTS.md](../../AGENTS.md) is the always-loaded project contract and task router. Workflow documents are ordinary Markdown, not auto-discovered skills. Open only the route needed; do not require this index during normal work. Existing external skills stay under their host's control and were not installed, copied into a skill registry, or modified.

The only project nested AGENTS remains [coordination](../../data/research/multi_chat_coordination_v1/AGENTS.md): directory-specific shared-file ownership warrants it. No redundant AGENTS was added to src/tools/dashboard/data/research.

Codex builds the instruction chain at session startup from project root to the working directory, using at most one instruction file per directory (`AGENTS.override.md` before `AGENTS.md`, then configured fallbacks). Starting at the repository root does not preload descendant AGENTS. When a root-started task operates in the coordination subtree, the root route explicitly tells it to read that nested file. A shell `cd` or editing AGENTS does not rewrite messages already in the current conversation.

Prefer adding a focused route over expanding the root. Keep job IDs, model settings, metrics and next experiments in [RESEARCH_CURRENT.md](RESEARCH_CURRENT.md) and versioned artifacts. Do not turn that pointer into a second research log. Archived prompts and generic SOUL/USER/IDENTITY/TOOLS/HEARTBEAT templates are not current Codex operating instructions; they remain verbatim for historical/other-harness compatibility and are not linked as startup reads.

Audit evidence: [classification](audit-20260920/CLASSIFICATION.md), [before](audit-20260920/BEFORE.json), [verification](audit-20260920/VERIFICATION.md), [original root text](audit-20260920/AGENTS.original.txt). The archive intentionally is not named AGENTS.md or SKILL.md.

Official references: [Astra prompt/skill guidance](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra), [Codex AGENTS discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md). The refactor removes generic persona/process scaffolding; it does not assume a stronger model can infer missing venue, evidence or production constraints.
