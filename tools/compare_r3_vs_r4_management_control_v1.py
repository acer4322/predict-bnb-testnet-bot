from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from src.predict_bot import r3_dual_paper_shadow_v2 as r3
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'); TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
M1=STACK['M1_model']; CLASSES=list(STACK['classes']); FULL=list(STACK['features']['full']); TFEATS=list(TRANS['features']); TM=TRANS['model']; OUT=ROOT/'data/research/r4_v0/hourly/r3_vs_r4_management_control_latest6_v1.json'
def mgmt_features(c,a,now,f):
    snaps=[]
    for key,o in list(c.orders.items()):
        try:s=a.snap(key)
        except Exception:continue
        s['_order']=o;snaps.append(s)
    up=float(c.inventory.maker_up+c.inventory.taker_up);dn=float(c.inventory.maker_down+c.inventory.taker_down);weak='DOWN' if up>dn+1e-9 else 'UP' if dn>up+1e-9 else None;dom='UP' if weak=='DOWN' else 'DOWN' if weak=='UP' else None
    def rows(side):return [s for s in snaps if side and str(s['_order'].side)==side and str(s.get('status')) in {'NEW','PARTIALLY_FILLED'}]
    wm,dm=rows(weak),rows(dom); unresolved=lambda rr:float(sum(float(s.get('leavesQty') or 0.) for s in rr)); oldest=lambda rr:max([max(0.,now-int(s['_order'].placed_at_ms))/1000. for s in rr],default=0.)
    def prog(rr):
        q=sum(float(s.get('qty') or 0.) for s in rr);z=sum(float(s.get('cumExecQty') or 0.) for s in rr);return z/q if q>1e-9 else 0.
    ev=list(c.inventory.events);ev5=[e for e in ev if now-int(e['event_ms'])<=5000];ev15=[e for e in ev if now-int(e['event_ms'])<=15000];start=min([int(e['event_ms']) for e in ev],default=now);sl=max(0.,300.-(now-start)/1000.);gross=up+dn;floor=float(f['floor']);absnet=abs(up-dn)
    return {'seconds_left':sl,'abs_gap':absnet,'risk_deficit':max(0.,-floor),'coverage':min(up,dn)/max(up,dn,1e-9),'absnet_ratio':absnet/gross if gross else 0.,'floor_per_gross':floor/gross if gross else 0.,'weak_active_owners':len(wm),'dominant_active_owners':len(dm),'weak_oldest_age_s':oldest(wm),'dominant_oldest_age_s':oldest(dm),'current_mode_age_s':(now-start)/1000.,'events_5s':len(ev5),'events_15s':len(ev15),'transitions_15s':0.,'weak_unresolved_shares':unresolved(wm),'dominant_unresolved_shares':unresolved(dm),'weak_progress_ratio':prog(wm),'dominant_progress_ratio':prog(dm),'weak_fill_shares_5s':sum(float(e['shares']) for e in ev5 if weak and e['side']==weak and e['role']=='MAKER'),'dominant_fill_shares_5s':sum(float(e['shares']) for e in ev5 if dom and e['side']==dom and e['role']=='MAKER')},weak
def r4_run_market(mid):
    orig_new=base.new_controller;events=[]
    def custom_new(a):
        c=orig_new(a);orig_add=c._add_order;st={'name':'ALLOW_ASYMMETRY','last':-10**18,'t':None,'intent':None}
        def evaluate(now):
            if st['t']==now:return st['intent']
            f=r3ctl.current_features(c,now)
            if f is None:it={'state':'ALLOW_ASYMMETRY','weakSide':None,'override':False};st['t']=now;st['intent']=it;return it
            X=np.asarray([[float(f[k]) for k in r3.FEATS]]);pb=float(r3.ARB.predict_proba(X)[0,1]);pc=float(r3.CROSS.predict_proba(X)[0,1]);p=r3ctl.packet_from_active(c,a,now,f);mode=r3.cooperation(st['name'],pb,pc,f,p);unc=r3.uncertainty_from_packet(p,mode);bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc
            if mode=='PRESERVE_CURRENT_REGIME' and pc>=.35:con=.35
            can=now-st['last']>=1000;ns=st['name']
            if can and pc>=con:ns='CROSSING_PROTECTION'
            elif ns=='CROSSING_PROTECTION' and can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
            elif ns=='BUILD_WEAK_SIDE' and can and pb<boff:ns='ALLOW_ASYMMETRY'
            elif ns=='ALLOW_ASYMMETRY' and can and pb>=bon:ns='BUILD_WEAK_SIDE'
            mf,weak=mgmt_features(c,a,now,f);risk=trans=0.;override=False
            try:
                pm=M1.predict_proba(np.asarray([[mf[k] for k in FULL]]))[0];cm={str(x):float(pm[i]) for i,x in enumerate(CLASSES)};risk=1-cm.get('CONTINUE_WEAK',0.);pt=TM.predict_proba(np.asarray([[mf[k] for k in TFEATS]]))[0];trans=float(pt[1] if len(pt)>1 else pt[0]);override=60<=mf['seconds_left']<180 and risk>=.5 and trans>=.5 and weak is not None
            except Exception:pass
            if override:ns='CROSSING_PROTECTION'
            if ns!=st['name']:st['name']=ns;st['last']=now
            it={'state':ns,**r3.control_intent(ns,f),'risk':risk,'transition':trans,'override':override};st['t']=now;st['intent']=it;return it
        def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            it=evaluate(now);weak=it.get('weakSide');strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
            if it['state']=='CROSSING_PROTECTION' and strong and side==strong:
                made=orig_add(weak,now,snapshot_ns,decision_id,'R4_MGMT_WEAK_SIDE_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True) if weak else False;events.append({'override':it['override'],'action':'VETO','made':bool(made)});return bool(made)
            made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard);events.append({'override':it['override'],'action':'PASS','made':bool(made)});return made
        c._add_order=add;return c
    base.new_controller=custom_new
    try:r=base.run_market(mid,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200)
    finally:base.new_controller=orig_new
    r['r4Overrides']=sum(e['override'] for e in events);return r
def summary(r):
    x=r['studentRollout'];p=x['finalPortfolio'];return {'makerFillEvents':x['makerFillEvents'],'makerFilledShares':x['makerFilledShares'],'takerFills':x['takerFills'],'finalAbsNet':p.get('combined_abs_net'),'worstCaseFloor':p.get('worst_case_floor'),'makerNet':p.get('maker_net')}
def main():
    import sqlite3
    db=sqlite3.connect(ROOT/'data/hft_forward_paper_v1.db');mids=[r[0] for r in db.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by completed_at_ms desc limit 6")];db.close();rows=[]
    for mid in mids:
        a=r3ctl.run_market(int(mid),True);b=r4_run_market(int(mid));A,B=summary(a),summary(b);row={'marketId':int(mid),'R3':A,'R4_MANAGEMENT':B,'deltaFloor':B['worstCaseFloor']-A['worstCaseFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],'r4Overrides':b['r4Overrides']};rows.append(row);print(json.dumps(row),flush=True)
    agg={'markets':len(rows),'r4FloorWins':sum(x['deltaFloor']>1e-9 for x in rows),'r3FloorWins':sum(x['deltaFloor']<-1e-9 for x in rows),'ties':sum(abs(x['deltaFloor'])<=1e-9 for x in rows),'meanFloorDelta':float(np.mean([x['deltaFloor'] for x in rows])),'meanAbsNetDelta':float(np.mean([x['deltaAbsNet'] for x in rows])),'totalOverrides':sum(x['r4Overrides'] for x in rows)};out={'version':'R3_VS_R4_MANAGEMENT_CONTROL_LATEST6_V1','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','markets':rows,'aggregate':agg};OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
if __name__=='__main__':main()
