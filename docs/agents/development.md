# Scoped development and validation

Use for actual code changes. Inspect related files and preserve existing work; keep research-only tools/artifacts separate from runtime deployment.

- Before introducing a new subsystem or dependency, briefly check existing repository utilities and suitable maintained libraries. External preflight is useful for a new architecture, not mandatory for every small edit or experiment. No unapproved paid-service spend.
- Run focused Python tests from the repository root using its existing environment. For `dashboard`, `npm.cmd test` includes a build and UI-related tests; run it when that scope warrants it. `dashboard-v2` has a separate package and `npm.cmd run build`, not the same test script. Inspect the affected package before choosing checks.
- Browser checks are for changed UI behavior/layout, not backend-only or instruction-only edits. Reserve broad regression for substantial or pre-live changes. Separate an observed baseline failure from a new regression using actual comparison.
- For instruction-only changes, verify local links, instruction discovery and preservation of application/config/research files. Do not launch training, restart services or run the full trading test suite for a documentation refactor.
- Before any runtime mutation/restart, use [runtime.md](runtime.md). Before any heavy training/HFT run, use [worker.md](worker.md).

These are project-specific validation routes, not a request to add tests that simply mirror implementation or to introduce a new framework.
