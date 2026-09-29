from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'MCP_SETUP.md'
base=subprocess.check_output(['git','show','HEAD:MCP_SETUP.md'],cwd=ROOT).decode('utf-8')
section='''\n\n## 2026-08-25 — Large research transport rule\n\nFor large BTC 5M Lab research runs, use **execute -> write artifact/checkpoint/log -> small MCP response**. MCP execution responses should normally contain only `ok`, exit code/status, artifact/checkpoint path and a short summary. Keep research `max_output_bytes` around **32768-65536**; write verbose stdout to `.log`.\n\nFor large reports, call `file_metadata` first and then `read_file_chunk` in approximately **128-256 KiB** chunks. Do not ingest large files with one `read_project_file` response. Long 30/50/100-market HFT jobs must be resumable/checkpointed and separate execution from result transfer. Avoid concurrent heavy MCP calls when CPU is high. On 502, do not immediately resend the same large request: check `lab_status` when available, inspect durable artifacts/checkpoints, then resume with smaller output/chunks/shards without changing cohort/split/labels/model settings.\n\nCanonical project contract: `data/research/mcp_large_run_transport_contract_v1.md`.\n'''
if '## 2026-08-25 — Large research transport rule' not in base:
    base=base.rstrip()+section
p.write_text(base,encoding='utf-8')
print('restored HEAD MCP_SETUP.md and appended transport rule')
