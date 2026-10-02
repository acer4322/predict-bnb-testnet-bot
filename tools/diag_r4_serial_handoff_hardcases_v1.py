from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.test_r4_repair_serial_handoff_v1 import run_handoff
from tools.analyze_r4_economic_overrepair_replay_v1 import audit_rep

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets';ids=[int(x) for x in json.loads(Path(a.ids_json).read_text())];rows=[]
    for mid in ids:
        rep=run_handoff(mid);aud,_=audit_rep(mid,'HANDOFF',rep);hard=[x for x in aud if x.get('hardOverRepair')];rows.append({'marketId':mid,'hardEvents':hard,'handoffEvents':rep.get('repairSerialHandoffV1') or [],'makerFills':rep.get('makerFillEvents') or [],'takerEvents':rep.get('takerEvents') or []});print(json.dumps({'marketId':mid,'hard':len(hard),'handoffs':len(rows[-1]['handoffEvents'])},ensure_ascii=False),flush=True)
    Path(a.output).write_text(json.dumps({'version':'R4_SERIAL_HANDOFF_HARDCASE_DIAG_V1','rows':rows},indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
if __name__=='__main__':main()
