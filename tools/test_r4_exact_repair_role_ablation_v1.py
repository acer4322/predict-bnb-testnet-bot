from __future__ import annotations
import argparse,json,sqlite3,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name=='tools' else Path(r'C:\BTC5M-worker')
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import validate_r4_r3_repair_counterfactual_teacher_v1 as cf
from tools.run_r4_threeway10_benchmark_chunk_v1 import score


def run_variant(mid:int,force_at:int,block_taker_ms:int):
    orig_new=base.new_controller; orig_submit=base.HftBookAdapter.submit_taker; applied=[]; blocked=[]
    def submit_wrap(self,side,max_price,shares):
        now=int(self.bt.current_timestamp//1_000_000)
        if applied and int(force_at) <= now <= int(force_at)+int(block_taker_ms):
            blocked.append({'atMs':now,'side':side,'shares':float(shares),'maxPrice':float(max_price)})
            return -1, 1
        return orig_submit(self,side,max_price,shares)
    def injected_new(a):
        c=orig_new(a);inv=c.inventory;orig_features=inv.features
        def features(now):
            if not applied and int(now)==int(force_at) and c.episode is None:
                net=float(inv.maker_up-inv.maker_down);gross=float(inv.maker_up+inv.maker_down);pc=2*min(inv.maker_up,inv.maker_down)/gross if gross>1e-9 else 0.0;ab=abs(net)
                if ab>1.0:
                    side='UP' if net>0 else 'DOWN';c.episode={'kind':'RESIDUAL','side':side,'risk_start_ms':int(now),'risk_pre_abs':ab,'start_ms':int(now),'pre_abs':ab,'start_abs':ab,'expansion':0.0,'pre_pc':pc,'unresolved':False};c.readiness=True;applied.append({'atMs':int(now),'side':side,'seam':'POST_HFT_FILL_PRE_DECISION_INVENTORY_FEATURES'})
            return orig_features(now)
        inv.features=features;return c
    base.new_controller=injected_new;base.HftBookAdapter.submit_taker=submit_wrap
    try: rep=r3ctl.run_market(int(mid),True)
    finally: base.new_controller=orig_new;base.HftBookAdapter.submit_taker=orig_submit
    rep['roleAblation']={'blockedTakerWindowMs':int(block_taker_ms),'blocked':blocked,'forced':applied};return rep

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--ids',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--out',required=True);a=ap.parse_args();root=Path(a.data_root).resolve();ids=[int(x) for x in a.ids.split(',') if x.strip()]
    base.STRATEGY_DB=root/'strategy_target_compare_v1.db';base.mod.BOOK_DB=root/'wallet_maker_book_inference.db';base.ex.BOOK_DB=root/'wallet_maker_book_inference.db';base.tape_v1.ARCHIVE_DIR=root/'execution_tape_v1/markets';settle=root/'target_wallet_official_v1.db'
    rows=[]
    for mid in ids:
        con=sqlite3.connect(settle);rr=con.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();con.close();winner=str(rr[0]) if rr else None
        try:
            b=r3ctl.run_market(mid,True);cand=cf.first_candidate(b.get('decisionRows') or []);bs=score(b,winner);rec={'marketId':mid,'winner':winner,'candidate':cand,'baseline':bs}
            if cand:
                d=cf.run_exact(mid,int(cand['decisionMs']));ds=score(d,winner);p=run_variant(mid,int(cand['decisionMs']),5000);ps=score(p,winner)
                rec['defaultRepair']={'score':ds,'conversion':('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if ds['pnlUsdt']>0 else 'LOSS')}
                rec['passiveOnly5s']={'score':ps,'conversion':('WIN' if bs['pnlUsdt']>0 else 'LOSS')+'->'+('WIN' if ps['pnlUsdt']>0 else 'LOSS'),'blocked':p['roleAblation']['blocked']}
            rows.append(rec);print(json.dumps({'marketId':mid,'default':rec.get('defaultRepair',{}).get('conversion'),'passiveOnly':rec.get('passiveOnly5s',{}).get('conversion'),'blockedN':len(rec.get('passiveOnly5s',{}).get('blocked',[]))}),flush=True)
        except Exception as e:rows.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps({'marketId':mid,'error':str(e)}),flush=True)
    Path(a.out).parent.mkdir(parents=True,exist_ok=True);Path(a.out).write_text(json.dumps({'version':'R4_EXACT_REPAIR_ROLE_ABLATION_V1','researchOnly':True,'actionAuthority':False,'rows':rows},indent=2),encoding='utf-8')
if __name__=='__main__':main()
