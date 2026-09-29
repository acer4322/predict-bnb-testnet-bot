from pathlib import Path
p=Path('tools/test_r4_p0_provenance_repeatability_v1.py')
s=p.read_text(encoding='utf-8')
s=s.replace("from pathlib import Path\nfrom typing import Any\n\nfrom tools.hftbacktest_r4_p0_provenance_journal_v1 import run_recovery\n\nROOT = Path(__file__).resolve().parents[1]\n", "from pathlib import Path\nfrom typing import Any\nimport sys\n\nROOT = Path(__file__).resolve().parents[1]\nif str(ROOT) not in sys.path:\n    sys.path.insert(0, str(ROOT))\nfrom tools.hftbacktest_r4_p0_provenance_journal_v1 import run_recovery\n\n")
p.write_text(s,encoding='utf-8')
print('patched')
