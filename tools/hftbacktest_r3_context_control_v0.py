from __future__ import annotations
import json, math, sys
from pathlib import Path
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from src.predict_bot import r3_dual_paper_shadow_v2 as r3


def current_features(c, now:int):
    ev=[(int(e['event_ms']),str(e['role']),str(e['side']),float(e['price']),float(e['shares'])) for e in c.inventory.events]
    rows=r3.features(ev)
    return rows[-1][1] if rows else None

def packet_from_active(c,a,now,f):
    snaps=[]
    for key,o in list(c.orders.items()):
        s=a.snap(key); s['_order']=o; snaps.append(s)
    if not snaps:
        return {'contextType':'NORMAL_FULL_FILL','ownershipState':0,'terminalCertainty':1,'remainingObligationFraction':0.,'passiveProgressProbability':1.,'reconcileNeeded':0,'cancelPending':0,'unknownState':0,'eventValidity':1,'observationAgeMs':0.,'elapsedSincePriorMs':float(f.get('age_since_last_ms',0.)),'hasPriorObservation':1.}
    partial=[s for s in snaps if str(s.get('status'))=='PARTIALLY_FILLED']
    live=partial or [s for s in snaps if str(s.get('status'))=='NEW']
    if live:
        rem=sum(float(s.get('leavesQty') or 0.) for s in live); qty=sum(float(s.get('qty') or 0.) for s in live); rr=max(0.,min(1.,rem/qty if qty>1e-9 else 1.)); cum=sum(float(s.get('cumExecQty') or 0.) for s in live); prog=max(0.,min(1.,cum/qty if qty>1e-9 else 0.)); typ='LIVE_PARTIAL_CHILD_STILL_OWNS_REMAINDER' if partial else 'LIVE_NO_FILL_DELAY_NOT_TERMINAL'; age=max(0.,now-min(int(s['_order'].placed_at_ms) for s in live))
        return {'contextType':typ,'ownershipState':1,'terminalCertainty':0,'remainingObligationFraction':rr,'passiveProgressProbability':prog,'reconcileNeeded':0,'cancelPending':0,'unknownState':0,'eventValidity':1,'observationAgeMs':float(age),'elapsedSincePriorMs':float(f.get('age_since_last_ms',0.)),'hasPriorObservation':1.}
    return {'contextType':'ORDER_STATE_UNKNOWN_OWNERSHIP_UNRELEASED','ownershipState':2,'terminalCertainty':0,'remainingObligationFraction':1.,'passiveProgressProbability':0.,'reconcileNeeded':0,'cancelPending':0,'unknownState':1,'eventValidity':1,'observationAgeMs':0.,'elapsedSincePriorMs':float(f.get('age_since_last_ms',0.)),'hasPriorObservation':1.}

def run_market(market_id:int,soft:bool,disable_taker:bool=False,no_taker_fallback_mode:str="NONE",maker_first_repair_mode:str="NONE",maker_first_wait_ms:int=2200):
    orig_new=base.new_controller; control_events=[]; rng=np.random.default_rng(930000+int(market_id))
    def custom_new(a):
        c=orig_new(a); orig_add=c._add_order; state={'name':'ALLOW_ASYMMETRY','last':-10**18,'eval_t':None,'intent':None}
        def eval_state(now:int):
            if state['eval_t']==now and state['intent'] is not None:return state['intent']
            f=current_features(c,now)
            if f is None:
                intent={'state':'ALLOW_ASYMMETRY','repairMode':'NORMAL_FORMATION','weakSide':None,'weakSideMakerScale':1.,'strongSideMakerScale':1.,'pauseStrongSide':False,'pb':0.,'pc':0.,'unc':0.,'contextType':'NO_INVENTORY'};state['intent']=intent;state['eval_t']=now;return intent
            X=np.asarray([[float(f[k]) for k in r3.FEATS]],float); pb=float(r3.ARB.predict_proba(X)[0,1]);pc=float(r3.CROSS.predict_proba(X)[0,1]);p=packet_from_active(c,a,now,f)
            unc=0.;bon=.48;boff=.38;con=.35;mode='ALLOW_R3_REEVALUATION'
            if soft:
                mode=r3.cooperation(state['name'],pb,pc,f,p);unc=r3.uncertainty_from_packet(p,mode);bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc
                # R3.1 CF1: context may modulate confidence, but PRESERVE_CURRENT_REGIME must not block native crossing protection once raw R3 reaches its own crossing threshold.
                if mode=='PRESERVE_CURRENT_REGIME' and pc>=.35: con=.35
            can=(now-state['last'])>=1000;ns=state['name']
            if can and pc>=con:ns='CROSSING_PROTECTION'
            elif ns=='CROSSING_PROTECTION':
                if can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
            elif ns=='BUILD_WEAK_SIDE':
                if can and pb<boff:ns='ALLOW_ASYMMETRY'
            elif can and pb>=bon:ns='BUILD_WEAK_SIDE'
            if ns!=state['name']:state['name']=ns;state['last']=now
            ci=r3.control_intent(ns,f);intent={'state':ns,**ci,'pb':pb,'pc':pc,'unc':unc,'contextType':p['contextType'],'cooperationMode':mode,'buildOn':bon,'crossOn':con};state['intent']=intent;state['eval_t']=now;return intent
        def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            it=eval_state(now); weak=it.get('weakSide'); strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
            action='PASS'
            if it['state']=='CROSSING_PROTECTION' and strong and side==strong:
                action='VETO_STRONG_CROSS'; made=False
                # actively substitute one weak-side passive repair attempt
                if weak:
                    made=orig_add(weak,now,snapshot_ns,decision_id,'R3_CROSSING_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
                control_events.append({'atMs':now,'attemptSide':side,'action':action,'substituteWeak':weak,'made':bool(made),**it});return bool(made)
            if it['state']=='BUILD_WEAK_SIDE' and strong and side==strong and rng.random()>float(it['strongSideMakerScale']):
                action='VETO_STRONG_BUILD'; made=False
                if weak: made=orig_add(weak,now,snapshot_ns,decision_id,'R3_BUILD_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)
                control_events.append({'atMs':now,'attemptSide':side,'action':action,'substituteWeak':weak,'made':bool(made),**it});return bool(made)
            made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
            control_events.append({'atMs':now,'attemptSide':side,'action':action,'made':bool(made),**it});return made
        c._add_order=add;c.r3_control_events=control_events;return c
    base.new_controller=custom_new
    try: rep=base.run_market(int(market_id),entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200,disable_taker=bool(disable_taker),no_taker_fallback_mode=str(no_taker_fallback_mode),maker_first_repair_mode=str(maker_first_repair_mode),maker_first_wait_ms=int(maker_first_wait_ms))
    finally: base.new_controller=orig_new
    rep['r3Control']={'version':'R3_CONTEXT_CONTROL_V0' if soft else 'R3_BASE_CONTROL_V0','softContext':bool(soft),'events':control_events,'vetoStrong':sum(e['action'].startswith('VETO') for e in control_events),'weakSubstitutions':sum(bool(e.get('made')) and e['action'].startswith('VETO') for e in control_events),'contextualEvents':sum(e.get('unc',0)>0 for e in control_events)}
    return rep

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--market-id',type=int,required=True);args=p.parse_args()
    A=run_market(args.market_id,False);B=run_market(args.market_id,True)
    def s(r):
        x=r['studentRollout'];p=x['finalPortfolio'];return {'makerFillEvents':x['makerFillEvents'],'makerFilledShares':x['makerFilledShares'],'takerFills':x['takerFills'],'finalAbsNet':p.get('combined_abs_net'),'floor':p.get('worst_case_floor'),'makerNet':p.get('maker_net'),'control':{k:r['r3Control'][k] for k in ['softContext','vetoStrong','weakSubstitutions','contextualEvents']}}
    out={'marketId':args.market_id,'baseline':s(A),'contextV4':s(B),'executionChanged':s(A)!=s(B)}
    path=ROOT/'data/research/r3_v0'/f'r3_hft_context_control_smalltest_market{args.market_id}_v0.json';path.write_text(json.dumps({'summary':out,'A':A,'B':B},indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
