from __future__ import annotations
import json,sys,sqlite3,statistics
from pathlib import Path
import numpy as np,joblib,pandas as pd
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_context_control_v0 as r3ctl
from src.predict_bot import r3_dual_paper_shadow_v2 as r3
from tools.compare_r3_vs_r4_management_control_v1 import mgmt_features,summary
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
DUR=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_durable_retained_surplus_manager_v1.joblib')
M1=STACK['M1_model'];CLASSES=list(STACK['classes']);FULL=list(STACK['features']['full']);TM=TRANS['model'];TFEATS=list(TRANS['features'])
DFEATS=list(DUR['features']);DM=DUR['models']['EBM_JOINT'];RM=DUR['models']['EBM_RETENTION']
OUT=ROOT/'data/research/r4_v0/hourly/r3_vs_r4_durable_retained_manager_latest12_v3.json';ROWS=ROOT/'data/research/r4_v0/hourly/r3_vs_r4_durable_retained_manager_latest12_v3_events.csv'

def fee(sh,px): return sh*px*(1-px)*.02*4

def econ_and_teacher_features(c,now,f):
    ev=sorted(list(c.inventory.events),key=lambda x:int(x['event_ms']))
    up=float(c.inventory.maker_up+c.inventory.taker_up);dn=float(c.inventory.maker_down+c.inventory.taker_down)
    # Rebuild strict-past cost from confirmed fills because teacher economics use fill price/cost.
    cost=fees=0.0
    for e in ev:
        sh=float(e.get('shares') or 0.);px=float(e.get('price') or 0.);cost+=sh*px
        if str(e.get('role'))=='TAKER':fees+=fee(sh,px)
    pu=up-cost-fees;pdn=dn-cost-fees;floor=min(pu,pdn);upside=max(pu,pdn);surplus=abs(up-dn);baseq=min(up,dn);gross=up+dn
    ss='UP' if up>dn+1e-9 else 'DOWN' if dn>up+1e-9 else 'FLAT';weak='DOWN' if ss=='UP' else 'UP' if ss=='DOWN' else 'FLAT'
    def win(ms): return [e for e in ev if now-int(e['event_ms'])<=ms]
    w5,w15=win(5000),win(15000)
    def maker_shares(w,side): return sum(float(e.get('shares') or 0) for e in w if side!='FLAT' and e.get('side')==side and e.get('role')=='MAKER')
    def cadence(w,side):
        z=[e for e in w if side!='FLAT' and e.get('side')==side and e.get('role')=='MAKER'];shares=[float(e.get('shares') or 0) for e in z];px=[float(e.get('price') or 0) for e in z];ts=[int(e['event_ms']) for e in z];g=[(b-a)/1000 for a,b in zip(ts,ts[1:])]
        return {'legs':len(z),'shares':sum(shares),'medSize':statistics.median(shares) if shares else 0.,'priceRange':max(px)-min(px) if px else 0.,'medGap':statistics.median(g) if g else 0.}
    weak5=cadence(w5,weak);weak15=cadence(w15,weak);ss5=cadence(w5,ss);ss15=cadence(w15,ss)
    last=ev[-1] if ev else {};start=int(ev[0]['event_ms']) if ev else now
    feat={'pre_floor':floor,'pre_upside':upside,'pre_surplus':surplus,'pre_base':baseq,'pre_surplus_ratio':surplus/max(gross,1e-9),'pre_floor_per_base':floor/max(baseq,1e-9),'last_price':float(last.get('price') or 0.),'last_shares':float(last.get('shares') or 0.),'last_role_taker':int(str(last.get('role'))=='TAKER'),'weak_parent_shares_5s':maker_shares(w5,weak),'weak_parent_shares_15s':maker_shares(w15,weak),'surplus_parent_shares_15s':maker_shares(w15,ss),'parent_weak_dominance_15s':(maker_shares(w15,weak)+1)/(maker_shares(w15,ss)+1),'events_parent_5s':len(w5),'events_parent_15s':len(w15),'seconds_from_first_event':(now-start)/1000.,'obs_weak_legs_5s':weak5['legs'],'obs_weak_shares_5s':weak5['shares'],'obs_weak_legs_15s':weak15['legs'],'obs_weak_shares_15s':weak15['shares'],'obs_weak_med_size_15s':weak15['medSize'],'obs_weak_price_range_15s':weak15['priceRange'],'obs_weak_med_gap_15s':weak15['medGap'],'obs_surplus_legs_5s':ss5['legs'],'obs_surplus_shares_5s':ss5['shares'],'obs_surplus_legs_15s':ss15['legs'],'obs_surplus_shares_15s':ss15['shares'],'obs_fill_dominance_5s':(weak5['shares']+1)/(ss5['shares']+1),'obs_fill_dominance_15s':(weak15['shares']+1)/(ss15['shares']+1),'obs_leg_dominance_15s':(weak15['legs']+1)/(ss15['legs']+1)}
    return feat,weak,ss

def run_r4(mid,event_rows):
    orig_new=base.new_controller;stats={'riskCandidates':0,'positiveFloorPreserves':0,'teacherPreserves':0,'crossOverrides':0}
    def custom_new(a):
        c=orig_new(a);orig_add=c._add_order;st={'name':'ALLOW_ASYMMETRY','last':-10**18,'t':None,'intent':None}
        def evaluate(now):
            if st['t']==now:return st['intent']
            f=r3ctl.current_features(c,now)
            if f is None:it={'state':'ALLOW_ASYMMETRY','weakSide':None,'override':False};st['t']=now;st['intent']=it;return it
            X=np.asarray([[float(f[k]) for k in r3.FEATS]]);pb=float(r3.ARB.predict_proba(X)[0,1]);pc=float(r3.CROSS.predict_proba(X)[0,1]);p=r3ctl.packet_from_active(c,a,now,f);mode=r3.cooperation(st['name'],pb,pc,f,p);unc=r3.uncertainty_from_packet(p,mode);bon=.48+.03*unc;boff=.38-.015*unc;con=.35+.0225*unc
            can=now-st['last']>=1000;ns=st['name']
            if can and pc>=con:ns='CROSSING_PROTECTION'
            elif ns=='CROSSING_PROTECTION' and can and pc<con*.7:ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
            elif ns=='BUILD_WEAK_SIDE' and can and pb<boff:ns='ALLOW_ASYMMETRY'
            elif ns=='ALLOW_ASYMMETRY' and can and pb>=bon:ns='BUILD_WEAK_SIDE'
            mf,weak=mgmt_features(c,a,now,f);risk=trans=0.;candidate=False;preserve_reason='';pdur=pret=composite=0.;econ={}
            try:
                pm=M1.predict_proba(np.asarray([[mf[k] for k in FULL]]))[0];cm={str(x):float(pm[i]) for i,x in enumerate(CLASSES)};risk=1-cm.get('CONTINUE_WEAK',0.)
                pt=TM.predict_proba(np.asarray([[mf[k] for k in TFEATS]]))[0];trans=float(pt[1] if len(pt)>1 else pt[0])
                tf,weak2,ss=econ_and_teacher_features(c,now,f);econ=tf;weak=weak2 if weak2!='FLAT' else weak
                candidate=bool(60<=mf['seconds_left']<180 and risk>=.5 and trans>=.5 and weak is not None)
                if candidate:
                    stats['riskCandidates']+=1
                    # R4 contract hard guard: an extra management override may not flatten a portfolio solely because risk is high when current economic floor is already positive. Native R3 remains free to cross on its own.
                    if tf['pre_floor']>0:
                        preserve_reason='POSITIVE_FLOOR';stats['positiveFloorPreserves']+=1
                    else:
                        row=pd.DataFrame([[float(tf[k]) for k in DFEATS]],columns=DFEATS)
                        pdur=float(DM.predict_proba(row)[0,1]);pret=float(np.clip(RM.predict(row)[0],0,2));composite=pdur*pret
                        if pdur>=.5:
                            preserve_reason='DURABLE_RETAINED_TEACHER';stats['teacherPreserves']+=1
            except Exception as ex:
                preserve_reason=''
            override=bool(candidate and not preserve_reason)
            if override:ns='CROSSING_PROTECTION';stats['crossOverrides']+=1
            if candidate:
                event_rows.append({'marketId':mid,'t':now,'secondsLeft':mf.get('seconds_left'),'risk':risk,'transition':trans,'floor':econ.get('pre_floor'),'upside':econ.get('pre_upside'),'surplus':econ.get('pre_surplus'),'pDurableRetained':pdur,'predRetention':pret,'composite':composite,'preserveReason':preserve_reason,'forcedCross':override})
            if ns!=st['name']:st['name']=ns;st['last']=now
            it={'state':ns,**r3.control_intent(ns,f),'weakSide':weak,'override':override};st['t']=now;st['intent']=it;return it
        def add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
            it=evaluate(now);weak=it.get('weakSide');strong='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
            if it['state']=='CROSSING_PROTECTION' and strong and side==strong:
                return bool(orig_add(weak,now,snapshot_ns,decision_id,'R4_DURABLE_RETAINED_WEAK_PRIORITY',p,snapshot,allow_stack=False,bypass_guard=True)) if weak else False
            return orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        c._add_order=add;return c
    base.new_controller=custom_new
    try:r=base.run_market(mid,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk',trade_offset='mid',taker_confirm_ms=2200)
    finally:base.new_controller=orig_new
    r['stats']=dict(stats);return r

def main():
    db=sqlite3.connect(ROOT/'data/hft_forward_paper_v1.db');latest=[int(r[0]) for r in db.execute("select market_id from hft_forward_runs_v1 where strategy_key='R2' and status='COMPLETE' order by completed_at_ms desc limit 12")];db.close();mids=[]
    for m in latest+[1736560]:
        if m not in mids:mids.append(m)
    rows=[];event_rows=[]
    for mid in mids:
        a=r3ctl.run_market(mid,True);b=run_r4(mid,event_rows);A,B=summary(a),summary(b);s=b['stats'];row={'marketId':mid,'R3':A,'R4_DURABLE_RETAINED':B,'deltaFloor':B['worstCaseFloor']-A['worstCaseFloor'],'deltaAbsNet':B['finalAbsNet']-A['finalAbsNet'],**s};rows.append(row);print(json.dumps(row),flush=True)
    pd.DataFrame(event_rows).to_csv(ROWS,index=False)
    agg={'markets':len(rows),'r4FloorWins':sum(x['deltaFloor']>1e-9 for x in rows),'r3FloorWins':sum(x['deltaFloor']<-1e-9 for x in rows),'ties':sum(abs(x['deltaFloor'])<=1e-9 for x in rows),'meanFloorDelta':float(np.mean([x['deltaFloor'] for x in rows])),'medianFloorDelta':float(np.median([x['deltaFloor'] for x in rows])),'meanAbsNetDelta':float(np.mean([x['deltaAbsNet'] for x in rows])),'riskCandidates':sum(x['riskCandidates'] for x in rows),'positiveFloorPreserves':sum(x['positiveFloorPreserves'] for x in rows),'teacherPreserves':sum(x['teacherPreserves'] for x in rows),'crossOverrides':sum(x['crossOverrides'] for x in rows)}
    out={'version':'R3_VS_R4_DURABLE_RETAINED_MANAGER_LATEST12_V3','researchOnly':True,'actionAuthority':'HFT_SIM_ONLY','preregisteredRule':['Risk+transition candidate uses existing natural 0.5 boundaries in 60-180s.','If current reconstructed economic floor > 0, management cannot add a forced crossing; native R3 remains unchanged.','If floor <= 0, EBM durable-retained teacher at natural p>=0.5 vetoes management forced crossing.','Retention head is logged only, not thresholded.','No threshold or weight sweep.'],'markets':rows,'aggregate':agg,'events':str(ROWS.relative_to(ROOT))};OUT.write_text(json.dumps(out,indent=2));print(json.dumps(agg,indent=2))
if __name__=='__main__':main()
