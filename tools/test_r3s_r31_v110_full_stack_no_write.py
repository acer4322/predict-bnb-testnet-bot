from __future__ import annotations
import json, math, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'src') not in sys.path: sys.path.insert(0,str(ROOT/'src'))
from predict_bot.unified_controller_r3s_r31_echtgeld_v1 import UnifiedControllerR3SR31EchtgeldV1, EngineDeterministicReject, VERSION, DISPLAY_VERSION

OUT=Path('data/research/r3_v0/r3s_r31_v110_full_stack_no_write_regression.json')

def main():
    c=UnifiedControllerR3SR31EchtgeldV1(); cases=[]
    try:
        c.current_market_id=999001
        c._deployment_live_ready=lambda: True
        snap={'marketId':999001,'bucketStartSec':1,'windowEndMs':9999999999999,'secondsLeft':180.0,'sampledAtMs':100000,'timestampNs':100000000}
        # 1. TAKER_PRICE_CAP is a proved pre-venue rejection: no fake active-fault WAIT.
        captured=[]
        def reject(path,payload):
            captured.append(dict(payload)); raise EngineDeterministicReject({'ok':False,'order':{'state':'REJECTED','error_kind':'TAKER_PRICE_CAP'},'error':'test cap'},400)
        c._engine_post=reject
        c.r3s_forced_taker_qty=250.123456
        ok=c._record_taker('UP',0.5,100000,'TEST:PRICECAP',snap,{},0.1,0.2,0.3,'ADD_EFFECT')
        c.r3s_forced_taker_qty=None
        cases.append({'name':'PRICE_CAP_NO_FALSE_WAIT','pass':(not ok and not c.r21_active_fault_wait and not c._has_unresolved_taker()),'faultWait':c.r21_active_fault_wait})
        cases.append({'name':'TAKER_QTY_EXACT_NO_10_18_200_CLIP','pass':bool(captured and abs(float(captured[-1]['shares'])-250.123456)<1e-9),'payloadShares':captured[-1]['shares'] if captured else None})
        # 2. Structural repair must never call/rely on the re-ADD veto head.
        c.inventory.reset(); c.inventory.apply({'event_ms':101000,'role':'MAKER','side':'UP','price':0.5,'shares':20.0})
        c.r21_active_fault_wait=False; c.taker_pending={}
        old_gate=c.r3s_active_stack.readd_gate
        def forbidden(*a,**k): raise AssertionError('readd gate called on structural repair')
        c.r3s_active_stack.readd_gate=forbidden
        c.r3s_forced_taker_qty=12.0
        ok2=c._record_taker('DOWN',0.45,102000,'TEST:REPAIR',snap,{},0.1,0.2,0.3,'REPAIR_EFFECT')
        c.r3s_forced_taker_qty=None; c.r3s_active_stack.readd_gate=old_gate
        cases.append({'name':'STRUCTURAL_REPAIR_NEVER_HARD_VETOED_BY_POST_ADD_HEAD','pass':(not ok2 and not c.r21_active_fault_wait)})
        # 3. Triple-confirm containment uses 75% current residual and opposite side.
        c.inventory.reset(); c.inventory.apply({'event_ms':110000,'role':'MAKER','side':'UP','price':0.4,'shares':40.0})
        c.book.book={'bids':{0.50:100.0},'asks':{0.51:100.0}}
        c.r3s_post_add_episode={'atMs':90000,'intentId':'ADD-X','containmentSubmitted':False,'earlyEvaluatedAtMs':None,'evidence':{'pStableAdd':0.1,'pStableExpand':0.2,'predUtility5s':-1.0}}
        old_tc=c.r3s_active_stack.triple_confirm; old_rec=c._record_taker
        c.r3s_active_stack.triple_confirm=lambda *a,**k:(True,{'pStableAdd':0.1,'pStableExpand':0.2,'predUtility5s':-1.0,'pEarlyRecovery15s':0.1,'tripleConfirm':True})
        got={}
        def fake_record(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect):
            got.update(side=side,price=price,qty=c.r3s_forced_taker_qty,predEffect=pred_effect); c.live_metrics['takerSubmitAccepted']+=1; return False
        c._record_taker=fake_record
        action=c._r3s_maybe_containment(snap,110000,None)
        c._record_taker=old_rec; c.r3s_active_stack.triple_confirm=old_tc
        cases.append({'name':'TRIPLE_CONFIRM_75PCT_RESIDUAL','pass':bool(action and action.get('action')=='R3S_TRIPLE_CONFIRM_CONTAINMENT' and got.get('side')=='DOWN' and abs(float(got.get('qty') or 0)-30.0)<1e-9 and got.get('predEffect')=='REPAIR_EFFECT'),'action':action,'captured':got})
        # 4. Bundle exposes the exact full stack and R3.1 remains information only.
        st=c.snapshot(); comp=st.get('r3sR31Bundle',{}).get('components',{})
        expected={'activeQuantity','stableAdd','stableExpansion','postAddLifecycle','earlyRecovery','tripleConfirmContainment'}
        cases.append({'name':'FULL_VERSION_AND_COMPONENTS_EXPOSED','pass':(st.get('version')==VERSION and st.get('r3sR31Bundle',{}).get('displayVersion')==DISPLAY_VERSION and expected.issubset(set(comp)) and st.get('r3sR31Bundle',{}).get('r31ActionAuthority') is False),'version':st.get('version'),'displayVersion':st.get('r3sR31Bundle',{}).get('displayVersion'),'components':comp})
    finally:
        c.stop()
    rep={'version':'R3S_R31_V110_FULL_STACK_NO_WRITE_REGRESSION','allPass':all(x['pass'] for x in cases),'passed':sum(x['pass'] for x in cases),'total':len(cases),'cases':cases}
    OUT.write_text(json.dumps(rep,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(OUT),'passed':rep['passed'],'total':rep['total'],'allPass':rep['allPass']}))
if __name__=='__main__': main()
