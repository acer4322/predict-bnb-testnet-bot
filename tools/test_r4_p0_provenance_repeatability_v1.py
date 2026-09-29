from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.hftbacktest_r4_p0_provenance_journal_v1 import run_recovery

OUT = ROOT / 'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_repeatability_v1.json'


def canonical_payload(x: dict[str, Any]) -> dict[str, Any]:
    return {
        'marketId': x.get('marketId'),
        'journal': x.get('provenanceJournal') or [],
        'state': x.get('provenanceResponsibilityState') or {},
        'fills': x.get('fillLog') or [],
        'stress': x.get('provenanceStressEvents') or [],
        'summary': x.get('provenanceSummary') or {},
    }


def digest(x: dict[str, Any]) -> str:
    b = json.dumps(canonical_payload(x), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--market-id', type=int, default=1738856)
    ap.add_argument('--repeats', type=int, default=10)
    a = ap.parse_args()
    rows=[]
    reference_hash=None
    exact=0
    for i in range(a.repeats):
        r=run_recovery(a.market_id, queue_reinsert=False, provenance_stress_cancel=True)
        h=digest(r)
        if reference_hash is None:
            reference_hash=h
        same=(h==reference_hash)
        exact += int(same)
        rows.append({'repeat':i+1,'hash':h,'exactToFirst':same,'events':len(r.get('provenanceJournal') or []),'responsibilities':len(r.get('provenanceResponsibilityState') or {}),'stressCancelEvents':len(r.get('provenanceStressEvents') or [])})
        print(json.dumps(rows[-1]),flush=True)
    status='REPEAT_PASS' if exact==a.repeats else 'REPEAT_FAIL'
    rep={
        'version':'R4_P0_PROVENANCE_REPEATABILITY_V1',
        'status':status,
        'marketId':a.market_id,
        'repeats':a.repeats,
        'exactRepeats':exact,
        'referenceHash':reference_hash,
        'rows':rows,
        'gate':'10/10 exact canonical journal+state+fill+stress hash',
        'guards':{'researchOnly':True,'no8781Change':True,'noLiveR3Change':True,'noEchtgeld':True,'special20260816Sealed':True}
    }
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'exact':f'{exact}/{a.repeats}','hash':reference_hash}),flush=True)


if __name__=='__main__':
    main()
