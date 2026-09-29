# Preserve live runtime during authorized maintenance

Use only when a requested code/configuration change affects a running service or requires restart. Reading docs or running offline research does not trigger this workflow.

- Inspect the affected launchers, process/service identity, configuration and current state before changing them; merge intended changes, preserve unrelated values. Research does not authorize touching Echtgeld / 8781 / 8782, live rules, stakes, schedules or source-of-truth databases. The documented 8788-to-8778 BOOK_DB separation must remain intact.
- Before an authorized restart, capture the actual service's `/api/live-rules` and `/api/realtime`: runtime enabled/disabled, armed state, strategies, exact stakes, quote access and health. Verify which service owns the endpoints; do not assume every port runs the same API.
- Run focused checks for the changed path. Use the affected service's existing launcher/restart path. For the `start-local.ps1` stack, use `start-local.ps1 -NoBrowser`; use `stop-local.ps1` only when required by its current state. Do not terminate unrelated processes.
- After readiness, recheck `/health`, affected endpoints and the same compact state snapshot. If restart unexpectedly paused a previously authorized enabled runtime, restore only its captured state through the established control path and verify it. Unknown state is not permission to enable trading; never invent strategies/stakes.
- Do not send artificial trades to satisfy reliability/first-order gates. Preserve `UNVERIFIED_UNTIL_FIRST_ORDER`, pending quote-access or other unknown evidence and report its actual meaning. Stop verification once changed-path checks, health and state parity are established.

For relevant API details use the existing service code/docs. `/api/live-details` may contain ledger details omitted by `/api/state`; do not repeatedly fetch broad state when a narrow endpoint suffices. This file changes no launcher behavior, service configuration or trading authorization.
