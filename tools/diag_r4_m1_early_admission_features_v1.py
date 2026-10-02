from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_r4_complexity_pruning_guard_ablation_v2 as g
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

class ForcedOpenM1:
    def __init__(self, real):
        self.real=real; self.records=[]
    def predict_proba(self,X):
        realp=self.real.predict_proba(X)
        for row,p in zip(np.asarray(X),realp):
            cm={str(c):float(p[i]) for i,c in enumerate(g.v5.CLASSES)}
            rec={k:float(row[i]) for i,k in enumerate(g.v5.FULL)}
            rec['realM1Risk']=1.0-cm.get('CONTINUE_WEAK',0.0)
            rec['unownedWeakDeficit']=max(0.0,rec.get('abs_gap',0.0)-rec.get('weak_unresolved_shares',0.0))
            rec['deterministicEligible']=bool(130.642<=rec.get('seconds_left',0.0)<180.0 and rec['unownedWeakDeficit']>36.0+1e-9)
            self.records.append(rec)
        forced=np.zeros_like(realp,dtype=float)
        cont=[i for i,c in enumerate(g.v5.CLASSES) if str(c)=='CONTINUE_WEAK']
        other=[i for i,c in enumerate(g.v5.CLASSES) if str(c)!='CONTINUE_WEAK']
        if other: forced[:,other[0]]=1.0
        elif cont: forced[:,cont[0]]=0.0
        return forced

def bind(data):
    g.v5.base.STRATEGY_DB=data/'strategy_target_compare_v1.db'
    g.v5.base.mod.BOOK_DB=data/'wallet_maker_book_inference.db'
    g.v5.base.ex.BOOK_DB=data/'wallet_maker_book_inference.db'
    g.v5.base.tape_v1.ARCHIVE_DIR=data/'execution_tape_v1/markets'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
    data=Path(args.data_root).resolve();bind(data);ids=[int(x) for x in json.loads(Path(args.ids_json).read_text())];settle=data/'target_wallet_official_v1.db';outrows=[]
    for mid in ids:
        events=[]; real=g.v5.M1; wrapper=ForcedOpenM1(real);g.v5.M1=wrapper
        rec={'marketId':mid}
        try:
            rep=g.run_overlay(mid,'LITE_NO_POSITIVE_FLOOR',events)
            con=sqlite3.connect(settle);rr=con.execute("select winner,resolved_at_ms from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]);rec['score']=score(rep,winner)
            elig=[x for x in wrapper.records if x['deterministicEligible']]
            rec['eligibleCount']=len(elig);rec['firstEligible']=elig[0] if elig else None; rec['firstRiskGE05']=next((x for x in elig if x['realM1Risk']>=0.5),None)
            rec['eligibleRows']=elig
            rec['forcedStats']=rep.get('mgmtStats') or {};rec['events']=events
        except Exception as ex: rec['error']=f'{type(ex).__name__}:{ex}'
        finally:g.v5.M1=real
        outrows.append(rec);print(json.dumps({'marketId':mid,'eligibleCount':rec.get('eligibleCount'),'firstRisk':(rec.get('firstEligible') or {}).get('realM1Risk'),'crossRisk':(rec.get('firstRiskGE05') or {}).get('realM1Risk'),'error':rec.get('error')},ensure_ascii=False),flush=True)
    Path(args.output).write_text(json.dumps({'version':'R4_M1_EARLY_ADMISSION_FEATURE_DIAGNOSTIC_V1','researchOnly':True,'rows':outrows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
if __name__=='__main__':main()
