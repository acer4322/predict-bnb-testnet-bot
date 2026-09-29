from __future__ import annotations
import argparse,json,sqlite3,sys,copy,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score

# This file carries a job-local copy of base.run_market source semantics indirectly by
# temporarily wrapping HftBookAdapter.submit_taker. The wrapper changes only the first
# marketable Taker submitted after the exact forced-repair seam, leaving all prior
# execution and all later native R3 actions unchanged.

def run_scaled(mid:int, force_at:int, multiplier:float):
    orig_new=base.new_controller; applied=[]; arm={'ready':False,'used':False}
    orig_submit=base.HftBookAdapter.submit_taker
    def submit_scaled(self,side,max_price,shares):
        if arm['ready'] and not arm['used']:
            arm['used']=True
            shares=float(shares)*float(multiplier)
        return orig_submit(self,side,max_price,shares)
    def injected_new(a):
        c=orig_new(a); inv=c.inventory; orig_features=inv.features
        def features(now):
            if not applied and int(now)==int(force_at) and c.episode is None:
                net=float(inv.maker_up-inv.maker_down); gross=float(inv.maker_up+inv.maker_down); pc=2*min(inv.maker_up,inv.maker_down)/gross if gross>1e-9 else 0.0; ab=abs(net)
                if ab>1.0:
                    side='UP' if net>0 else 'DOWN'
                    c.episode={'kind':'RESIDUAL','side':side,'risk_start_ms':int(now),'risk_pre_abs':ab,'start_ms':int(now),'pre_abs':ab,'start_abs':ab,'expansion':0.0,'pre_pc':pc,'unresolved':False}
                    c.readiness=True; arm['ready']=True
                    applied.append({'atMs':int(now),'side':side,'multiplier':float(multiplier),'seam':'POST_HFT_FILL_PRE_DECISION_INVENTORY_FEATURES'})
            return orig_features(now)
        inv.features=features; return c
    base.new_controller=injected_new; base.HftBookAdapter.submit_taker=submit_scaled
    try: rep=r3ctl.run_market(int(mid),True)
    finally:
        base.new_controller=orig_new; base.HftBookAdapter.submit_taker=orig_submit
    rep['forcedRepairQuantityV1']=applied; rep['forcedRepairScaledTakerUsed']=bool(arm['used']); return rep

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x.strip()]
    base.STRATEGY_DB=root/'strategy_target_compare_v1.db';base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets';settle=root/'target_wallet_official_v1.db'
    rows=[]
    for mid in ids:
        con=sqlite3.connect(settle); rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
        try:
            b=r3ctl.run_market(mid,True); cand=cf.first_candidate(b.get('decisionRows') or []); bs=score(b,winner); rec={'marketId':mid,'winner':winner,'candidate':cand,'baseline':bs}
            if cand:
                for mult in (1.0,2.0,3.0):
                    r=run_scaled(mid,int(cand['decisionMs']),mult); sc=score(r,winner); rec[f'x{int(mult)}']={'score':sc,'pnlDelta':sc['pnlUsdt']-bs['pnlUsdt'],'used':r.get('forcedRepairScaledTakerUsed')}
                rec['conversion18']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if rec['x1']['score']['pnlUsdt']>0 else 'LOSS')
                rec['conversion36']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if rec['x2']['score']['pnlUsdt']>0 else 'LOSS')
                rec['conversion54']=('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if rec['x3']['score']['pnlUsdt']>0 else 'LOSS')
            rows.append(rec);print(json.dumps({'marketId':mid,'c18':rec.get('conversion18'),'c36':rec.get('conversion36'),'c54':rec.get('conversion54')}),flush=True)
        except Exception as e: rows.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps({'marketId':mid,'error':str(e)}),flush=True)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps({'version':'R4_EXACT_REPAIR_QUANTITY_CONTINUATION_V1','researchOnly':True,'actionAuthority':False,'rows':rows},indent=2),encoding='utf-8')
if __name__=='__main__':main()
