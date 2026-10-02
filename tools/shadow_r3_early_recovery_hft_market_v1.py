from __future__ import annotations
import argparse,json,sys
from pathlib import Path
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
D=ROOT/'data/research/r3_v0'; SK=joblib.load(D/'r3_early_recovery_continuation_pilot500_v1.joblib'); F=SK['features']

def geom(events,t):
 up=dn=cost=fees=0.; maker=repair=readd=0
 for e in events:
  if e['t']>t: break
  if e['side']=='UP':up+=e['sh']
  else:dn+=e['sh']
  cost+=e['px']*e['sh']; fees+=e['px']*e['sh']*.02 if e['role']=='TAKER' else 0.
 pu,pd=up-cost-fees,dn-cost-fees; g=up+dn; ab=abs(up-dn); pc=2*min(up,dn)/g if g else 0.
 return {'floor':min(pu,pd),'abs':ab,'gross':g,'pc':pc}

def shadow(mid:int):
 r=run_market(mid,taker_sizing_mode='r3_rawq',post_add_shadow_artifact=D/'r3_post_add_our_state_student_v2_stream.joblib',post_add_lifecycle_control=True)
 ev=[]
 for x in r.get('makerFillEvents',[]): ev.append({'t':int(x['atMs']),'role':'MAKER','side':str(x['side']),'px':float(x['price']),'sh':float(x['deltaShares']),'effect':None})
 for x in r.get('takerEvents',[]): ev.append({'t':int(x['atMs']),'role':'TAKER','side':str(x['side']),'px':float(x['price']),'sh':float(x['shares']),'effect':None})
 ev.sort(key=lambda z:z['t'])
 # assign structural effect from pre-event inventory geometry
 up=dn=0.
 for z in ev:
  pre=up-dn
  if z['role']=='TAKER' and abs(pre)>1e-9: z['effect']='ADD' if ((pre>0 and z['side']=='UP') or (pre<0 and z['side']=='DOWN')) else 'REPAIR'
  if z['side']=='UP':up+=z['sh']
  else:dn+=z['sh']
 attempts=[]
 for a in r.get('takerAttempts',[]):
  if a.get('result')!='FILLED' or str(a.get('structuralEffect'))!='ADD_EFFECT': continue
  t=int(a['atMs']); fill_candidates=[z for z in ev if z['role']=='TAKER' and z['t']>=t and z['t']<=t+3000 and z['side']==a.get('side')]
  if not fill_candidates: continue
  ft=fill_candidates[0]['t']; g0=geom(ev,ft)
  # pre-floor approximate from fills strictly before fill
  gp=geom([z for z in ev if z['t']<ft],ft-1); spent=max(0.,gp['floor']-g0['floor'])
  row={'atMs':t,'fillMs':ft,'side':a.get('side'),'requestedShares':a.get('requestedShares'),'result':a.get('result'),'scores':[]}
  for hz in (2000,5000):
   zt=ft+hz; gh=geom(ev,zt); past=[z for z in ev if ft<z['t']<=zt]
   mk=sum(z['role']=='MAKER' for z in past); rp=sum(z.get('effect')=='REPAIR' for z in past); ra=sum(z.get('effect')=='ADD' for z in past)
   rec=max(0.,gh['floor']-g0['floor'])/max(spent,1e-9) if spent>1e-9 else 0.
   vals={'elapsed_s':hz/1000.,'floor_recovery_frac':rec,'absnet_change_frac':(gh['abs']-g0['abs'])/max(g0['abs'],1.),'paired_coverage':gh['pc'],'paired_change':gh['pc']-g0['pc'],'maker_events':mk,'repair_events':rp,'readd_events':ra,'gross_change_frac':(gh['gross']-g0['gross'])/max(g0['gross'],1.),'event_index_norm':max(0.,min(1.,1-float((r.get('decisionRows') or [{}])[-1].get('secondsLeft',0) or 0)/300.))}
   x=np.asarray([[float(vals[f]) for f in F]],float); p=float(SK['model'].predict_proba(x)[0,1]); row['scores'].append({'horizonMs':hz,'pEarlyRecovery':p,**vals})
  attempts.append(row)
 fp=r['studentRollout']['finalPortfolio']; out={'version':'R3_EARLY_RECOVERY_HFT_SHADOW_V1','marketId':mid,'researchOnly':True,'actionAuthority':False,'attempts':attempts,'summary':{'nAdd':len(attempts),'meanP2s':float(np.mean([x['scores'][0]['pEarlyRecovery'] for x in attempts])) if attempts else None,'meanP5s':float(np.mean([x['scores'][1]['pEarlyRecovery'] for x in attempts])) if attempts else None,'finalFloor':fp.get('worst_case_floor'),'finalUpside':fp.get('best_case_pnl'),'finalAbsNet':fp.get('combined_abs_net')}}
 p=D/f'r3_early_recovery_hft_shadow_market{mid}_v1.json';p.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p),'marketId':mid,'summary':out['summary']}))
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);a=ap.parse_args();shadow(a.market_id)
