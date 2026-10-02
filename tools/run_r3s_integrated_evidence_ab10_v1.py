from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_containment_smoke_v1 as h
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners,realized_pnl
D=ROOT/'data/research/r3_v0';OUT=D/'r3s_integrated_evidence_ab10_v1.json';STATUS=D/'r3s_integrated_evidence_ab10_status_v1.json';POST=D/'r3_post_add_our_state_student_v2_stream.joblib'
BASE=json.loads((D/'r3_targeted_add_graduation10_v1.json').read_text(encoding='utf-8'));COHORT=BASE['selected']
SA=joblib.load(D/'r3_stable_add_eligibility_pilot500_v2_normalized.joblib');SE=joblib.load(D/'r3_stable_expansion_teacher_v1_full.joblib');ER=joblib.load(D/'r3_early_recovery_continuation_pilot500_v2_hftscale.joblib')
def fee(sh,px,role):return sh*px*.02 if role=='TAKER' else 0.
def geom(ev,t):
 up=dn=cost=fees=0.
 for z in ev:
  if z['t']>t:break
  if z['side']=='UP':up+=z['sh']
  else:dn+=z['sh']
  cost+=z['px']*z['sh'];fees+=fee(z['sh'],z['px'],z['role'])
 pu,pd=up-cost-fees,down if False else dn-cost-fees;g=up+dn;ab=abs(up-dn)
 return {'up':up,'dn':dn,'cost':cost,'fees':fees,'floor':min(pu,pd),'upside':max(pu,pd),'gross':g,'abs':ab,'pc':2*min(up,dn)/g if g else 0.}
def evidence(r):
 ev=[]
 for z in r.get('makerFillEvents',[]):ev.append({'t':int(z['atMs']),'role':'MAKER','side':str(z['side']),'px':float(z['price']),'sh':float(z['deltaShares']),'effect':None})
 for z in r.get('takerEvents',[]):ev.append({'t':int(z['atMs']),'role':'TAKER','side':str(z['side']),'px':float(z['price']),'sh':float(z['shares']),'effect':None})
 ev.sort(key=lambda z:z['t']);up=dn=0.
 for z in ev:
  pre=up-dn
  if z['role']=='TAKER' and abs(pre)>1e-9:z['effect']='ADD' if ((pre>0 and z['side']=='UP') or (pre<0 and z['side']=='DOWN')) else 'REPAIR'
  if z['side']=='UP':up+=z['sh']
  else:dn+=z['sh']
 att=sorted(r.get('takerAttempts',[]),key=lambda x:int(x['atMs']));first=int(r.get('feed',{}).get('firstReceivedMs') or ev[0]['t']);last=int(r.get('feed',{}).get('lastReceivedMs') or ev[-1]['t']);rows=[]
 for a in att:
  if a.get('result')!='FILLED' or str(a.get('structuralEffect'))!='ADD_EFFECT':continue
  t=int(a['atMs']);hist=[z for z in ev if z['t']<t]
  if not hist:continue
  g=geom(hist,t-1);sur='UP' if g['up']>g['dn'] else 'DOWN' if g['dn']>g['up'] else 'FLAT';q15=[z for z in hist if t-z['t']<=15000];q5=[z for z in hist if t-z['t']<=5000];old=geom(hist,q5[0]['t'] if q5 else q15[0]['t'] if q15 else hist[-1]['t']);tot=sum(z['sh'] for z in q15) or 1.;nev=len(q15) or 1;lastz=hist[-1]
  v={'paired_coverage':g['pc'],'imbalance_ratio':g['abs']/g['gross'] if g['gross'] else 0.,'cost_per_gross':(g['cost']+g['fees'])/g['gross'] if g['gross'] else 0.,'floor_per_gross':g['floor']/g['gross'] if g['gross'] else 0.,'upside_per_gross':g['upside']/g['gross'] if g['gross'] else 0.,'floor_to_upside':g['floor']/g['upside'] if abs(g['upside'])>1e-9 else 0.,'last_price':lastz['px'],'last_role_taker':float(lastz['role']=='TAKER'),'age_since_last_s':(t-lastz['t'])/1000.,'events_5s':len(q5),'events_15s':len(q15),'maker_frac_15s':sum(z['role']=='MAKER' for z in q15)/nev,'taker_frac_15s':sum(z['role']=='TAKER' for z in q15)/nev,'same_side_event_frac_15s':sum(sur!='FLAT' and z['side']==sur for z in q15)/nev,'same_side_share_frac_15s':sum(z['sh'] for z in q15 if sur!='FLAT' and z['side']==sur)/tot,'opp_side_share_frac_15s':sum(z['sh'] for z in q15 if sur!='FLAT' and z['side']!=sur)/tot,'floor_change5_per_gross':(g['floor']-old['floor'])/g['gross'] if g['gross'] else 0.,'upside_change5_per_gross':(g['upside']-old['upside'])/g['gross'] if g['gross'] else 0.,'absnet_change5_per_gross':(g['abs']-old['abs'])/g['gross'] if g['gross'] else 0.,'event_index_norm':max(0.,min(1.,(t-first)/max(1,last-first)))}
  psa=float(SA['model'].predict_proba(np.asarray([[float(v.get(f,0.)) for f in SA['features']]],float))[0,1])
  ss=g['abs'];base=min(g['up'],g['dn']);old5=old;vals={'floor':g['floor'],'upside':g['upside'],'upside_gap':g['upside']-g['floor'],'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/g['gross'] if g['gross'] else 0.,'cost_per_gross_share':(g['cost']+g['fees'])/g['gross'] if g['gross'] else 0.,'last_price':lastz['px'],'last_shares':lastz['sh'],'last_role_taker':float(lastz['role']=='TAKER'),'age_since_last_ms':float(t-lastz['t']),'events_5s':float(len(q5)),'events_15s':float(len(q15)),'maker_events_15s':float(sum(z['role']=='MAKER' for z in q15)),'taker_events_15s':float(sum(z['role']=='TAKER' for z in q15)),'same_side_events_15s':float(sum(sur!='FLAT' and z['side']==sur for z in q15)),'opp_side_events_15s':float(sum(sur!='FLAT' and z['side']!=sur for z in q15)),'same_side_shares_15s':float(sum(z['sh'] for z in q15 if sur!='FLAT' and z['side']==sur)),'opp_side_shares_15s':float(sum(z['sh'] for z in q15 if sur!='FLAT' and z['side']!=sur)),'surplus_change_5s':float(g['abs']-old5['abs']),'floor_change_5s':float(g['floor']-old5['floor']),'upside_change_5s':float(g['upside']-old5['upside']),'floor_to_upside_ratio':g['floor']/g['upside'] if abs(g['upside'])>1e-9 else 0.,'floor_per_base_share':g['floor']/base if base>1e-9 else 0.,'upside_per_surplus_share':g['upside']/ss if ss>1e-9 else 0.,'event_index_norm':v['event_index_norm']}
  xs=np.asarray([[float(vals.get(f,0.)) for f in SE['features']]],float);pse=float(SE['model'].predict_proba(xs)[0,1]);util=float(SE['utilityModel'].predict(xs)[0])
  fc=[z for z in ev if z['role']=='TAKER' and z['t']>=t and z['t']<=t+3000 and z['side']==a.get('side')]
  per=1.
  if fc:
   ft=fc[0]['t'];g0=geom(ev,ft);gp=geom([z for z in ev if z['t']<ft],ft-1);gh=geom(ev,ft+15000);spent=max(0.,gp['floor']-g0['floor']);past=[z for z in ev if ft<z['t']<=ft+15000];rec=max(0.,gh['floor']-g0['floor'])/max(spent,1e-9) if spent>1e-9 else 0.;rv={'elapsed_s':15.,'floor_recovery_frac':rec,'absnet_change_frac':(gh['abs']-g0['abs'])/max(g0['abs'],1.),'paired_coverage':gh['pc'],'paired_change':gh['pc']-g0['pc'],'maker_events':sum(z['role']=='MAKER' for z in past),'repair_events':sum(z.get('effect')=='REPAIR' for z in past),'readd_events':sum(z.get('effect')=='ADD' for z in past),'gross_change_frac':(gh['gross']-g0['gross'])/max(g0['gross'],1.),'event_index_norm':max(0.,min(1.,1-float((r.get('decisionRows') or [{}])[-1].get('secondsLeft',0) or 0)/300.))};per=float(ER['model'].predict_proba(np.asarray([[float(rv[f]) for f in ER['features']]],float))[0,1])
  rows.append({'atMs':t,'pStableAdd':psa,'pStableExpand':pse,'utility':util,'pEarly15':per})
 return rows
def stat(a):
 s=sorted(a);n=len(a);return {'sum':sum(a),'mean':sum(a)/n,'min':min(a),'max':max(a),'positiveRate':sum(x>0 for x in a)/n}
def main():
 ww=winners(COHORT);rows=[];errs={};STATUS.write_text(json.dumps({'state':'RUNNING','completed':0}),encoding='utf-8')
 for mid in COHORT:
  try:
   base=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True);ee=evidence(base);trig=any(x['pStableAdd']<.5 and x['pStableExpand']<.5 and x['utility']<=0 and x['pEarly15']<.5 for x in ee);cand=base
   if trig:cand=h.run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=POST,post_add_lifecycle_control=True,containment_after_add_ms=12000,containment_repair_fraction=.75)
   bp=realized_pnl(base['studentRollout'],ww[mid]);cp=realized_pnl(cand['studentRollout'],ww[mid]);rows.append({'marketId':mid,'triggered':trig,'evidence':ee,'baselinePnl':bp,'candidatePnl':cp,'containmentEvents':cand.get('containmentEvents',[])})
  except Exception as e:errs[str(mid)]=repr(e)
  STATUS.write_text(json.dumps({'state':'RUNNING','completed':len(rows),'errors':len(errs),'artifact':str(OUT)},indent=2),encoding='utf-8')
 bp=[r['baselinePnl'] for r in rows];cp=[r['candidatePnl'] for r in rows];rep={'version':'R3S_INTEGRATED_EVIDENCE_AB10_V1','state':'COMPLETE','singleBaselineReplayPerMarket':True,'rows':rows,'errors':errs,'summary':{'baseline':stat(bp),'candidate':stat(cp),'candidateBetter':sum(c>b+1e-9 for b,c in zip(bp,cp)),'candidateWorse':sum(c<b-1e-9 for b,c in zip(bp,cp)),'same':sum(abs(c-b)<=1e-9 for b,c in zip(bp,cp)),'triggeredMarkets':[r['marketId'] for r in rows if r['triggered']]}};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');STATUS.write_text(json.dumps({'state':'COMPLETE','completed':len(rows),'errors':len(errs),'artifact':str(OUT)},indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(OUT),'completed':len(rows),'errors':len(errs)}))
if __name__=='__main__':main()
