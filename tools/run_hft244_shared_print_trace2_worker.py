import importlib.util
import json
import os
from pathlib import Path
import sys

spec=importlib.util.spec_from_file_location('native_build_v1',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
assert 'BTC5M_LAN_RESULT_DIR' in os.environ and d.digest(d.OLD)==d.OLD_SHA
bundle=Path(__file__).resolve().parent
manifest=json.loads((bundle/'MANIFEST.json').read_text(encoding='utf-8-sig'))
for item in manifest['files']: assert d.digest(bundle/item['name'])==item['sha256']
d.bounded('shared-print-trace2',[sys.executable,str(bundle/'probe_hft244_shared_print_trace2.py')],30)
d.save(d.RESULT/'COMPACT.json',dict(verdict='TRACE_COMPLETED_NOT_MODEL_PROMOTION',marketBE=0,originalUnchanged=d.digest(d.OLD)==d.OLD_SHA))
