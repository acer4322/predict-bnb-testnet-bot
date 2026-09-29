from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from collections import deque
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
D=ROOT/'data/research/r3_v0'
SK=joblib.load(D/'r3_stable_expansion_teacher_v1_full.joblib'); FEATURES=list(SK['features']); BUD=joblib.load(D/'r3_floor_budget_hgb_v2.joblib'); POST=joblib.load(D/'r3_post_add_our_state_student_v2_stream.joblib'); PF=list(POST['features'])

def fee(sh,px,role): return sh*px*0.02 if role=='TAKER' else 0.0

def shadow(mid:int):
    r=run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=D/'r3_post_add_our_state_student_v2_stream.joblib',post_add_lifecycle_control=True)
    events=[]
    for x in r.get('makerFillEvents',[]): events.append((int(x['atMs']),'MAKER',str(x['side']),float(x['price']),float(x['deltaShares'])))
    for x in r.get('takerEvents',[]): events.append((int(x['atMs']),'TAKER',str(x['side']),float(x['price']),float(x['shares'])))
    events.sort(key=lambda z:z[0])
    attempts=sorted(r.get('takerAttempts',[]),key=lambda z:int(z['atMs']))
    first=int(r.get('feed',{}).get('firstReceivedMs') or (events[0][0] if events else attempts[0]['atMs']))
    last=int(r.get('feed',{}).get('lastReceivedMs') or (events[-1][0] if events else attempts[-1]['atMs']))
    scored=[]
    for a in attempts:
        if str(a.get('structuralEffect'))!='ADD_EFFECT': continue
        t=int(a['atMs']); hist=[e for e in events if e[0]<t]
        if not hist: continue
        up=down=cost=fees=0.0; snaps=[]
        for et,role,side,px,sh in hist:
            if side=='UP': up+=sh
            else: down+=sh
            cost+=px*sh; fees+=fee(sh,px,role)
            pu,pd=up-cost-fees,down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down)
            snaps.append((et,role,side,px,sh,ss,fl,ups))
        et,role,side,px,sh,ss,fl,ups=snaps[-1]; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'; base=min(up,down); gross=up+down
        q15=[z for z in snaps if t-z[0]<=15000]; q5=[z for z in snaps if t-z[0]<=5000]; old5=q5[0] if q5 else q15[0] if q15 else snaps[-1]
        vals={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':float(role=='TAKER'),'age_since_last_ms':float(t-et),'events_5s':float(len(q5)),'events_15s':float(len(q15)),'maker_events_15s':float(sum(z[1]=='MAKER' for z in q15)),'taker_events_15s':float(sum(z[1]=='TAKER' for z in q15)),'same_side_events_15s':float(sum(surplus!='FLAT' and z[2]==surplus for z in q15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and z[2]!=surplus for z in q15)),'same_side_shares_15s':float(sum(z[4] for z in q15 if surplus!='FLAT' and z[2]==surplus)),'opp_side_shares_15s':float(sum(z[4] for z in q15 if surplus!='FLAT' and z[2]!=surplus)),'surplus_change_5s':float(ss-old5[5]),'floor_change_5s':float(fl-old5[6]),'upside_change_5s':float(ups-old5[7]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':max(0.,min(1.,(t-first)/max(1,last-first)))}
        x=np.asarray([[float(vals.get(f,0.0)) for f in FEATURES]],float)
        p=float(SK['model'].predict_proba(x)[0,1]); u=float(SK['utilityModel'].predict(x)[0]); budget=max(0.0,float(BUD['model'].predict(x)[0])); budgetQty=budget/max(1e-9,float(a.get('observedAsk') or 0.0)*1.02)
        # Counterfactual immediate post-ADD state using requested shares at observed ask; recent-flow features remain strict-past by construction.
        aq=float(a.get('requestedShares') or 0.0); apx=float(a.get('observedAsk') or 0.0); hup=up+(aq if a.get('side')=='UP' else 0.0); hdn=down+(aq if a.get('side')=='DOWN' else 0.0); hcost=cost+aq*apx; hfees=fees+aq*apx*0.02; hpu,hpd=hup-hcost-hfees,hdn-hcost-hfees; hfl=min(hpu,hpd); hups=max(hpu,hpd); hnet=hup-hdn; hg=hup+hdn; hab=abs(hnet); spent=max(0.0,fl-hfl)
        mk=[z for z in hist if z[1]=='MAKER']; tk=[z for z in hist if z[1]=='TAKER']; q10=[z for z in hist if t-z[0]<=10000]
        old10=q10[0] if q10 else hist[0]; oldab=abs(sum(z[4] if z[2]=='UP' else -z[4] for z in hist if z[0]<=old10[0]))
        pv={'combined_gross':hg,'combined_net':hnet,'combined_abs_net':hab,'combined_imbalance_ratio':hab/hg if hg else 0.0,'combined_paired_coverage':2*min(hup,hdn)/hg if hg else 0.0,'worst_case_floor':hfl,'best_case_pnl':hups,'abs_payoff_gap':hab,'last_maker_age_ms':t-mk[-1][0] if mk else 1e6,'last_taker_age_ms':t-tk[-1][0] if tk else 1e6,'maker_fills_5s':sum(z[1]=='MAKER' and t-z[0]<=5000 for z in hist),'maker_fills_10s':sum(z[1]=='MAKER' and t-z[0]<=10000 for z in hist),'taker_fills_5s':sum(z[1]=='TAKER' and t-z[0]<=5000 for z in hist),'taker_fills_10s':sum(z[1]=='TAKER' and t-z[0]<=10000 for z in hist),'maker_shares_5s':sum(z[4] for z in hist if z[1]=='MAKER' and t-z[0]<=5000),'maker_shares_10s':sum(z[4] for z in hist if z[1]=='MAKER' and t-z[0]<=10000),'taker_shares_5s':sum(z[4] for z in hist if z[1]=='TAKER' and t-z[0]<=5000),'taker_shares_10s':sum(z[4] for z in hist if z[1]=='TAKER' and t-z[0]<=10000),'combined_absnet_change_10s':hab-oldab,'event_index_norm':max(0.,min(1.,(t-first)/max(1,last-first))),'post_add_floor':hfl,'post_add_upside':hups,'post_add_abs_net':hab,'floor_spent':spent,'spent_vs_floor_scale':spent/max(abs(fl)+5.0,5.0),'elapsed_since_add_ms':0.0,'events_since_add':0.0,'maker_events_since_add':0.0,'repair_events_since_add':0.0,'floor_recovered_fraction':0.0}
        xp=np.asarray([[float(pv.get(f,0.0)) for f in PF]],float); pm=float(np.clip(POST['makerRecoveryModel'].predict(xp)[0],0,1)); pr=float(np.clip(POST['repairEmergencyModel'].predict(xp)[0],0,1)); pra=float(np.clip(POST['reAddModel'].predict(xp)[0],0,1))
        scored.append({'atMs':t,'side':a.get('side'),'requestedShares':a.get('requestedShares'),'preCombinedNet':a.get('preCombinedNet'),'pStableExpand':p,'predUtility5s':u,'predFloorBudget5s':budget,'budgetImpliedMaxShares':budgetQty,'rawRequestedShares':aq,'cfPostFloor':hfl,'cfFloorSpend':spent,'cfPMakerRecovery':pm,'cfPRepairEmergency':pr,'cfPReAdd':pra,'wouldPassP05':bool(p>=.5),'wouldPassUtilityPositive':bool(u>0),'result':a.get('result'),'adapterBoundary':'strict-past HFT fills; event_index_norm approximated by execution-tape time progress; recovery is immediate-post-fill counterfactual at observed ask'})
    s=r['studentRollout']; fp=s['finalPortfolio']
    out={'version':'R3_STABLE_EXPANSION_HFT_SHADOW_V1','marketId':mid,'researchOnly':True,'actionAuthority':False,'baseline':'R3_ACTIVE_RAWQ_POSTADD_V2','stableTeacher':SK['version'],'structuralAddAttempts':scored,'summary':{'nStructuralAdd':len(scored),'passP05':sum(x['wouldPassP05'] for x in scored),'passUtilityPositive':sum(x['wouldPassUtilityPositive'] for x in scored),'makerFilledShares':s['makerFilledShares'],'takerFilledShares':s['takerFilledShares'],'finalFloor':fp.get('worst_case_floor'),'finalUpside':fp.get('best_case_pnl'),'finalAbsNet':fp.get('combined_abs_net'),'gateVetoes':len(r.get('postAddGateEvents',[]))}}
    p=D/f'r3_stable_expansion_hft_shadow_market{mid}_v1.json'; p.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'artifact':str(p),'marketId':mid,'nStructuralAdd':len(scored),'passP05':out['summary']['passP05']}))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);a=ap.parse_args();shadow(a.market_id)
