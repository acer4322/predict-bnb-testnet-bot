from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);ap.add_argument('--market-id',type=int,default=1774858);a=ap.parse_args();dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'
 rep=r3ctl.run_market(a.market_id,True);fills=rep.get('makerFillEvents') or [];target=min(fills,key=lambda x:abs(int(x.get('atMs') or 0)-1787955929500));oid=str(target.get('orderId'))
 meta=(rep.get('orderMeta') or {}).get(oid) or {};placed=int(meta.get('placedAtMs') or 0);ft=int(target.get('atMs') or 0)
 states=[x for x in rep.get('orderStateRows') or [] if str(x.get('orderId'))==oid]
 actions=[]
 for x in rep.get('makerFillEvents') or []:actions.append({'kind':'MAKER_FILL',**{k:x.get(k) for k in ['atMs','observedAtMs','orderId','side','price','deltaShares','cumShares','status']}})
 for x in rep.get('takerEvents') or []:actions.append({'kind':'TAKER_FILL',**{k:x.get(k) for k in ['atMs','decisionMs','side','price','shares','feeUsdt','decisionId']}})
 actions=sorted([x for x in actions if placed-15000<=int(x.get('atMs') or 0)<=ft+15000],key=lambda x:int(x.get('atMs') or 0))
 decisions=[d for d in rep.get('decisionRows') or [] if placed-10000<=int(d.get('decisionMs') or 0)<=ft+5000]
 controls=[e for e in (rep.get('r3Control') or {}).get('events') or [] if placed-10000<=int(e.get('atMs') or 0)<=ft+5000]
 out={'version':'R4_OVERREPAIR_1774858_DIAG_V1','marketId':a.market_id,'targetFill':target,'orderId':oid,'orderMeta':meta,'placedAtMs':placed,'fillAtMs':ft,'restingAgeMs':ft-placed,'orderStates':states,'nearbyActions':actions,'nearbyDecisions':decisions,'nearbyR3Control':controls,'studentRollout':rep.get('studentRollout')}
 Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'orderId':oid,'meta':meta,'targetFill':target,'restingAgeMs':ft-placed,'nearbyActions':actions,'statesN':len(states)},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
