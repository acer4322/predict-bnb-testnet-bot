from __future__ import annotations
import json, sqlite3, joblib, numpy as np
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; R=ROOT/'data/research/r3_v0'; DB=ROOT/'data/target_wallet_official_v1.db'
sys.path.insert(0,str((ROOT/'tools').resolve()))
import test_r3_r21_cooperation_formation_ab_v1 as ab
PATTERNS=ab.SEQ_ORDER

def rel(pattern,i,n):
 x=i/max(n-1,1)
 if pattern=='stall_then_late_fill': return (.55,.45) if x<.45 else (.95,.05)
 if pattern=='cancel_partial_then_ack': return (.45,.60) if x<.67 else (.98,.02)
 if pattern=='unknown_then_fill': return (.35,.70) if x<.50 else (.95,.05)
 if pattern=='out_of_order_after_terminal': return (.98,.02)
 if pattern=='target_revision_then_fill': return (.60,.40) if x<.50 else (.96,.04)
 if pattern=='partial_then_cancel_ack': return (.50,.55) if x<.70 else (.98,.02)
 return (1.,0.)
def soft(cps,pat,strength,leaseN):
 state='ALLOW_ASYMMETRY'; last=cps[0]['t']; pred=[]; lease=0
 for i,cp in enumerate(cps):
  trust,unc=rel(pat,i,len(cps)); pb,pc=cp['pb'],cp['pc']; bon=.48+strength*unc; boff=.38-.5*strength*unc; con=.35+.75*strength*unc; can=(cp['t']-last)>=ab.CFG['minDwellMs']
  if trust>.9: lease=0
  elif unc>.45 and state!='ALLOW_ASYMMETRY': lease=max(lease,leaseN)
  ns=state
  if can and pc>=con and lease<=0: ns='CROSSING_PROTECTION'
  elif state=='CROSSING_PROTECTION':
   if can and pc<con*.7 and lease<=0: ns='BUILD_WEAK_SIDE' if pb>=bon else 'ALLOW_ASYMMETRY'
  elif state=='BUILD_WEAK_SIDE':
   if lease>0: lease-=1
   elif can and pb<boff: ns='ALLOW_ASYMMETRY'
  elif can and pb>=bon: ns='BUILD_WEAK_SIDE'
  if ns!=state: state=ns; last=cp['t']
  pred.append(state)
 return pred
def main():
 old=json.loads((R/'r3_r21_cooperation_formation_ab_v1_report.json').read_text()); mids=[r['marketId'] for r in old['rows']]; pats={r['marketId']:r['contextPattern'] for r in old['rows']}
 c=sqlite3.connect(DB); cache={m:ab.build_snaps(c,m) for m in mids}; c.close(); configs=[]
 for strength,lease in [(.02,0),(.03,0),(.04,0),(.05,0),(.03,1),(.04,1),(.05,1),(.03,2)]:
  rows=[]
  for mid in mids:
   cps=cache[mid]; truth=[ab.tcore(x['teacher']) for x in cps]; A=ab.armA(cps); B=soft(cps,pats[mid],strength,lease); am=ab.metr(truth,A); bm=ab.metr(truth,B); rows.append({'marketId':mid,'pattern':pats[mid],'A_checkpointSync':am[0],'B_checkpointSync':bm[0],'A_transitionSync2cp':am[1],'B_transitionSync2cp':bm[1],'A_switches':am[2],'B_switches':bm[2]})
  avg=lambda k:float(np.mean([x[k] for x in rows])); configs.append({'strength':strength,'maxLease':lease,'metrics':{'A_checkpointSync':avg('A_checkpointSync'),'B_checkpointSync':avg('B_checkpointSync'),'deltaCheckpointSync':avg('B_checkpointSync')-avg('A_checkpointSync'),'A_transitionSync2cp':avg('A_transitionSync2cp'),'B_transitionSync2cp':avg('B_transitionSync2cp'),'deltaTransitionSync2cp':avg('B_transitionSync2cp')-avg('A_transitionSync2cp'),'A_switches':avg('A_switches'),'B_switches':avg('B_switches')},'rows':rows})
 rep={'version':'R3_R21_SOFT_CONTEXT_COOPERATION_V4','mode':'same frozen 18-market dream-fill descriptive AB','configs':configs,'authority':{'r21ActionAuthority':False,'r3FormationActionOwner':True}}
 (R/'r3_r21_soft_context_cooperation_v4_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps([{'strength':x['strength'],'lease':x['maxLease'],**x['metrics']} for x in configs],indent=2))
if __name__=='__main__':main()
