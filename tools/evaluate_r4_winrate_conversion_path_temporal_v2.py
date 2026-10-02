from __future__ import annotations
import argparse,json,sqlite3,sys,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

WINDOWS=(3000,10000,15000)
PORT_KEYS=('maker_abs_net','maker_paired_coverage','worst_case_floor','best_case_pnl','combined_abs_net','combined_paired_coverage')
MODEL_KEYS=('pResidualWake','pMakerUp','pMakerDown','pTaker1s','pTaker3s')
PUBLIC_KEYS=('directionScore','spotQueueImbalance','spotTakerImbalance1s','futuresQueueImbalance','futuresTakerImbalance1s')

def fv(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None

def latest_before(rows,t):
    cand=[r for r in rows if int(r.get('decisionMs') or -1)<=int(t)]
    return cand[-1] if cand else None

def temporal(b,cand_ms):
    ds=sorted(b.get('decisionRows') or [],key=lambda r:int(r.get('decisionMs') or 0))
    maker=b.get('makerFillEvents') or []
    taker=b.get('takerEvents') or []
    cur=latest_before(ds,cand_ms) or {}
    out={}
    for w in WINDOWS:
        tag=f'{w//1000}s'; prev=latest_before(ds,cand_ms-w) or {}
        cp=cur.get('portfolio') or {}; pp=prev.get('portfolio') or {}
        cm=cur.get('models') or {}; pm=prev.get('models') or {}
        cu=cur.get('public') or {}; pu=prev.get('public') or {}
        # some decision rows may carry public values at top level
        for k in PORT_KEYS:
            a=fv(cp.get(k)); z=fv(pp.get(k)); out[f'delta_{k}_{tag}']=(a-z) if a is not None and z is not None else None
        for k in MODEL_KEYS:
            a=fv(cm.get(k)); z=fv(pm.get(k)); out[f'delta_{k}_{tag}']=(a-z) if a is not None and z is not None else None
        for k in PUBLIC_KEYS:
            a=fv(cu.get(k,cur.get(k))); z=fv(pu.get(k,prev.get(k))); out[f'delta_{k}_{tag}']=(a-z) if a is not None and z is not None else None
        m=[x for x in maker if cand_ms-w < int(x.get('atMs') or -1) <= cand_ms]
        tk=[x for x in taker if cand_ms-w < int(x.get('atMs') or -1) <= cand_ms]
        out[f'makerFillEvents_{tag}']=len(m); out[f'makerFilledShares_{tag}']=sum(float(x.get('deltaShares') or 0) for x in m)
        out[f'takerFillEvents_{tag}']=len(tk); out[f'takerFilledShares_{tag}']=sum(float(x.get('shares') or 0) for x in tk)
        recent=[r for r in ds if cand_ms-w < int(r.get('decisionMs') or -1) <= cand_ms]
        choices=Counter(str(r.get('executionChoice') or 'NONE') for r in recent)
        out[f'waitDecisions_{tag}']=choices.get('WAIT',0); out[f'makerDecisions_{tag}']=choices.get('MAKER',0); out[f'takerDecisions_{tag}']=choices.get('TAKER',0)
        # handoff proxy: transitions among execution choices in strict-past decision sequence
        seq=[str(r.get('executionChoice') or 'NONE') for r in recent]
        out[f'executionTransitions_{tag}']=sum(a!=b for a,b in zip(seq,seq[1:]))
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--ids',required=True); ap.add_argument('--data-root',required=True); ap.add_argument('--out',required=True); a=ap.parse_args()
    root=Path(a.data_root).resolve(); ids=[int(x) for x in a.ids.split(',') if x.strip()]
    cf.base.STRATEGY_DB=root/'strategy_target_compare_v1.db'; cf.base.mod.BOOK_DB=root/'wallet_maker_book_inference.db'; cf.base.ex.BOOK_DB=root/'wallet_maker_book_inference.db'; cf.base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets'
    settle=root/'target_wallet_official_v1.db'; rows=[]
    for mid in ids:
        con=sqlite3.connect(settle); rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone(); con.close(); winner=str(rr[0]) if rr else None
        rec={'marketId':mid,'winner':winner}
        try:
            b=cf.r3ctl.run_market(mid,True); cand=cf.first_candidate(b.get('decisionRows') or []); bs=score(b,winner)
            rec['candidate']=cand; rec['baselineScore']=bs; rec['baselineCompact']=cf.compact(b)
            if cand:
                rec['temporalStrictPast']=temporal(b,int(cand['decisionMs']))
                c=cf.run_exact(mid,cand['decisionMs']); cs=score(c,winner); cc=cf.compact(c)
                rec['counterfactualScore']=cs; rec['counterfactualCompact']=cc; rec['branchClass']=cf.classify(rec['baselineCompact'],cc); rec['exactBranchApplied']=bool(cc.get('forced') and int(cc['forced'][0]['atMs'])==int(cand['decisionMs']))
                rec['deltaPnl']=cs['pnlUsdt']-bs['pnlUsdt']; rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if cs['pnlUsdt']>0 else 'LOSS')
            else:
                rec['conversion']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->NO_CANDIDATE'; rec['branchClass']='NO_CANDIDATE'; rec['exactBranchApplied']=False
        except Exception as ex: rec['error']=f'{type(ex).__name__}:{ex}'
        rows.append(rec); print(json.dumps({'marketId':mid,'conversion':rec.get('conversion'),'exact':rec.get('exactBranchApplied'),'error':rec.get('error')}),flush=True)
    good=[r for r in rows if 'error' not in r]; cnt=Counter(r.get('conversion') for r in good)
    rep={'version':'R4_WINRATE_CONVERSION_PATH_TEMPORAL_V2','researchOnly':True,'actionAuthority':False,'strictPastTemporalWindowsMs':list(WINDOWS),'marketCount':len(good),'conversionCounts':dict(cnt),'rows':rows}
    p=Path(a.out); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'marketCount':len(good),'conversionCounts':dict(cnt)},indent=2))
if __name__=='__main__': main()
