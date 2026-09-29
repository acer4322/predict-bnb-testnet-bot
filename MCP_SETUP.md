# MCP Setup / Operating Notes

## Large research transport rule (2026-08-25)

For large BTC 5M Lab research runs, use **execute -> write artifact/checkpoint/log -> small MCP response**. MCP execution responses should normally contain only `ok`, exit code/status, artifact/checkpoint path and a short summary. Keep research `max_output_bytes` around **32768-65536**; write verbose stdout to `.log`.

For large reports, call `file_metadata` first and then `read_file_chunk` in approximately **128-256 KiB** chunks. Do not ingest large files with one `read_project_file` response.

Long 30/50/100-market HFT jobs must be resumable/checkpointed and separate execution from result transfer. Avoid concurrent heavy MCP calls when CPU is high. On 502, do not immediately resend the same large request: check `lab_status` when available, inspect durable artifacts/checkpoints, then resume with smaller output/chunks/shards without changing the cohort, split, labels or model settings.

Canonical project contract: `data/research/mcp_large_run_transport_contract_v1.md`.

## Existing transport notes

- `run_project_process` executes a non-shell process; `run_project_shell` executes a shell command. Intentional mutation/execution should use the appropriate confirmation controls exposed by the MCP server.
- Large synchronous responses through the tunnel can trigger 502. Prefer artifact/log output plus bounded chunk reads.
