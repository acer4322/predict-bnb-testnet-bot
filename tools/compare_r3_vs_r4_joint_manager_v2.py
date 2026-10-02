from __future__ import annotations
import json, sys, sqlite3
from pathlib import Path
import numpy as np, joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from src.predict_bot import r3_dual_paper_shadow_v2 as r3
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features, summary
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
JOINT=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_joint_base_upside_manager_v1.joblib')
M1=STACK['M1_model']; CLASSES=list(STACK['classes']); FULL=list(STACK['features']['full'])
TM=TRANS['model']; TFEATS=list(TRANS['features']); JM=JOINT['EBM']; JFEATS=list(JOINT['features'])
OUT=ROOT/'data/research/r4_v0/hourly/r3_vs_r4_joint_manager_latest12_v2.json'

def joint_features(c,now,f):
    ev=list(c.inventory.events); up=float(c.inventory.maker_up+c.inventory.taker_up); dn=float(c.inventory.maker_down+c.inventory.taker_down)
    cost=float(getattr(c.inventory,'maker_cost',0)+getattr(c.inventory,'taker_cost',0)); fees=float(getattr(c.inventory,'taker_fees',0))
    pu=up-cost-fees; pdn=dn-cost-fees; floor=min(pu,pdn); upside=max(pu,pdn); surplus=abs(up-dn); baseq=min(up,dn); gross=up+dn
    weak='DOWN' if up>dn+1e-9 else 'UP' if dn>up+1e-9 else None; strong='UP' if weak=='DOWN' else 'DOWN' if weak=='UP' else None
    def win(ms): return [e for e in ev if now-int(e['event_ms'])<=ms]
    w5,w15,w30=win(5000),win(15000),win(30000)
    def shs(w,side,role=None): return sum(float(e['shares']) for e in w if side and e['side']==side and (role is None or e['role']==role))
    prev5=w5[-1] if w5 else None
    # runtime-compatible approximations: current economic geometry + strictly-past realized flow. Delta fields use R3 current features where available.
    return {'seconds_left':max(0.,300.-(now-min([int(e['event_ms']) for e in ev],default=now))/1000.),'floor':floor,'upside':upside,'surplus':surplus,'base':baseq,'surplus_ratio':surplus/max(gross,1e-9),'floor_per_base':floor/max(baseq,1e-9),'upside_per_surplus':upside/max(surplus,1e-9),'last_price':float(ev[-1].get('price',0) if ev else 0),'last_shares':float(ev[-1].get('shares',0) if ev else 0),'last_role_taker':int(bool(ev and ev[-1].get('role')=='TAKER')),'age_since_prev_ms':int(ev[-1]['event_ms']-ev[-2]['event_ms']) if len(ev)>1 else 0,'events_5s':len(w5),'events_15s':len(w15),'events_30s':len(w30),'maker_events_15s':sum(e['role']=='MAKER' for e in w15),'taker_events_15s':sum(e['role']=='TAKER' for e in w15),'weak_maker_shares_5s':shs(w5,weak,'MAKER'),'weak_maker_shares_15s':shs(w15,weak,'MAKER'),'weak_maker_shares_30s':shs(w30,weak,'MAKER'),'surplus_maker_shares_15s':shs(w15,strong,'MAKER'),'surplus_maker_shares_30s':shs(w30,strong,'MAKER'),'weak_taker_shares_15s':shs(w15,weak,'TAKER'),'surplus_taker_shares_15s':shs(w15,strong,'TAKER'),'floor_change_5s':float(f.get('floor_change_5s',0)),'upside_change_5s':float(f.get('upside_change_5s',0)),'surplus_change_5s':float(f.get('surplus_change_5s',0)),'weak_to_surplus_maker_ratio_15s':(shs(w15,weak,'MAKER')+1)/(shs(w15,strong,'MAKER')+1),'weak_to_surplus_maker_ratio_30s':(shs(w30,weak,'MAKER')+1)/(shs(w30,strong,'MAKER')+1),'seconds_from_first_event':(now-min([int(e['event_ms']) for e in ev],default=now))/1000.}

def run_r4(mid):
    orig_new=base.new_controller; stats={'riskCandidates':0,'jointPreserves':0,'crossOverrides':0}
    def custom_new(a):
        c=orig_new(a); orig_add=c._add_order; st={'name':'ALLOW_ASYMMETRY','last':-10**18,'t':None,'intent':None}
        def evaluate(now):
            if st['t']==now:return st['intent']
            f=r3ctl.current_features(c,now)
            if f is None: it={'state':'ALLOW_ASYMMETRY','weakSide':None,'override':False};st['t']=now;st['intent']=it;return it
            X=np.asarray([[float(f[k]) for k in r3.FEATS]]);pb=float(r3.ARB.predict_proba(X)[0,1]);pc=float(r3.CROSS.predict_proba(X)[0,1]);p=r3ctl.packet_from_active(c,a,now,f);mode=r3.cooperation(st['name'],pb,pc,f,p);unc=r3.uncertainty_from_packet(p,mode);bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc
            can=now-st['last']>=1000;ns=st['name']
            if can and pc>=con:ns='CROSSING_PROTECTION'
            elif ns=='CROSSING_PROTECTION' and can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
            elif ns=='BUILD_WEAK_SIDE' and can and pb<boff:ns='ALLOW_ASYMMETRY'
            elif ns=='ALLOW_ASYMMETRY' and can and pb>=bon:ns='BUILD_WEAK_SIDE'
            mf,weak=mgmt_features(c,a,now,f); risk=trans=joint=0.; override=False; preserve=False
            try:
                pm=M1.predict_proba(np.asarray([[mf[k] for k in FULL]]))[0]; cm={str(x):float(pm[i]) for i,x in enumerate(CLASSES)};risk=1-cm.get('CONTINUE_WEAK',0.)
                pt=TM.predict_proba(np.asarray([[mf[k] for k in TFEATS]]))[0];trans=float(pt[1] if len(pt)>1 else pt[0])
                jf=joint_features(c,now,f); joint=float(JM.predict_proba(np.asarray([[jf[k] for k in JFEATS]]))[0,1])
                candidate=60<=mf['seconds_left']<180 and risk>=.5 and trans>=.5 and weak is not None
                if candidate: stats['riskCandidates']+=1
                # Natural 0.5 boundary only. High joint belief vetoes forced crossing: preserve concurrent base+upside formation.
                preserve=bool(candidate and joint>=.5)
                override=bool(candidate and not preserve)
                if preserve: stats['jointPreserves']+=1
            except Exception: pass
            if override: ns='CROSSING_PROTECTION'; stats['crossOverrides']+=1
            if ns!=st['name']:st['name']=ns;st['last']=now
            it={'state':ns,**r3.control_intent(ns,f),'risk':risk,'transition':trans,'joint':joint,'override':override,'preserve':preserve};st['t']=now;st['intent']=it;return it
        def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            it=evaluate(now);weak=it.get('weakSide');strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
            if it['state']=='CROSSING_PROTECTION' and strong and side==strong:
                return bool(orig_add(weak,now,snapshot_ns,decision_id,'R4_JOINT_MGMT_WEAK_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)) if weak else False
            return orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        c._add_order=add;return c
    base.new_controller=custom_new
    try:r=base.run_market(mid,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200)
    finally:base.new_controller=orig_new
    r['stats']=dict(stats);return r

def main():
    db=sqlite3.connect(ROOT/'data/hft_forward_paper_v1.db');mids=[int(r[0]) for r in db.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by completed_at_ms desc limit 12")];db.close(); rows=[]
    for mid in mids:
        a=r3ctl.run_market(mid,True);b=run_r4(mid);A,B=summary(a),summary(b);row={'marketId':mid,'R3':A,'R4_JOINT_MANAGEMENT':B,'deltaFloor':B['worstCaseFloor']-A['worstCaseFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],**b['stats']};rows.append(row);print(json.dumps(row),flush=True)
    out={'version':'R3_VS_R4_JOINT_MANAGER_LATEST12_V2','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','rule':'Existing risk+transition candidate; joint-base-upside belief >=0.5 vetoes forced crossing. No threshold sweep.','markets':rows,'aggregate':{'markets':len(rows),'r4FloorWins':sum(x['deltaFloor']>1e-9 for x in rows),'r3FloorWins':sum(x['deltaFloor']<-1e-9 for x in rows),'ties':sum(abs(x['deltaFloor'])<=1e-9 for x in rows),'meanFloorDelta':float(np.mean([x['deltaFloor'] for x in rows])),'medianFloorDelta':float(np.median([x['deltaFloor'] for x in rows])),'meanAbsNetDelta':float(np.mean([x['deltaAbsNet'] for x in rows])),'riskCandidates':sum(x['riskCandidates'] for x in rows),'jointPreserves':sum(x['jointPreserves'] for x in rows),'crossOverrides':sum(x['crossOverrides'] for x in rows)}}
    OUT.write_text(json.dumps(out,indent=2));print(json.dumps(out['aggregate'],indent=2))
if __name__=='__main__':main()

