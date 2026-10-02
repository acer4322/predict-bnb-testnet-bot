from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPEND = (ROOT / 'data/research/mcp_large_run_transport_handoff_append_v1.md').read_text(encoding='utf-8').strip()
MARKER = '## 2026-08-25 — Large research run / MCP transport operating rule'
for rel in ['BTC5M_CODEX_RESEARCH_HANDOFF.md', 'BTC5M_PROJECT_HANDOFF.md']:
    p = ROOT / rel
    text = p.read_text(encoding='utf-8')
    if MARKER not in text:
        with p.open('a', encoding='utf-8', newline='') as f:
            f.write('\n\n' + APPEND + '\n')
        print(f'updated {rel}')
    else:
        print(f'already_present {rel}')
