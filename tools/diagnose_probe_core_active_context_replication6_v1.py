from __future__ import annotations
import argparse,importlib.util,json,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd().parent/'run_eth_quantity_responsibility_ladder_v3f_initial_probe_core_handback.py'
if _STAGED.exists():
 spec=importlib.util.spec_from_file_location('staged_v3f',_STAGED);v3f=importlib.util.module_from_spec(spec);spec.loader.exec_module(v3f)
else: import tools.run_eth_quantity_responsibility_ladder_v3f_initial_probe_core_handback as v3f
v3b=v3f.v3b; EPS=1e-12

def run(tape,Cls):
 s=Cls(tape)
 try:return s.run_qty('__UNSCORED__')
 finally:s.close()

def live_roles_at(hist,t):
 live={}; keyrole={}
 for e in hist:
  et=int(e.get('t',0))
  if et>t:break
  ev=e.get('event');k=e.get('key')
  if ev=='ROLE_SLOT_SUBMIT': keyrole[k]=str(e.get('role'));live[k]={'role':str(e.get('role')),'side':str(e.get('side')),'price':float(e.get('price')),'qty':float(e.get('qty'))}
  elif ev=='SLOT_RELEASE' and k in live: live.pop(k,None)
 by={}
 for x in live.values():by[x['role']]=by.get(x['role'],0)+1
 return {'count':len(live),'byRole':by,'orders':list(live.values())}

def recent_fills(hist,t,lookback):
 xs=[e for e in hist if e.get('event')=='ROLE_FILL' and t-lookback<=int(e.get('t',0))<t];by={}
 for e in xs:by[str(e.get('role'))]=by.get(str(e.get('role')),0)+1
 return {'n':len(xs),'byRole':by}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 with tempfile.TemporaryDirectory(prefix='probe_core_ctx_') as td:
  root=Path(td)
  with zipfile.ZipFile(a.bundle) as z:
   for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
  for mid in mids:
   tape=root/'tapes'/f'{mid}.json.xz';A=run(tape,v3b.FifoAggregateResponsibilityLadderV3B);B=run(tape,v3f.InitialProbeCoverageCoreHandbackV3F)
   eb=next((e for e in B['quantityLadderEvents'] if e.get('event')=='QTY_FIFO_INITIAL_PROBE_CORE_ACTIVE_HANDBACK_ELIGIBLE'),None)
   if not eb:rows.append({'marketId':mid,'eligible':False});continue
   t=int(eb['t']);rid=int(eb['originResponsibilityId']);lot=next((x for x in A['quantityResponsibilities'] if int(x['id'])==rid),None)
   act=next((e for e in A['quantityLadderEvents'] if e.get('event')=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT' and int(e.get('originResponsibilityId',-1))==rid and int(e.get('t',0))>=t),None)
   fill=next((e for e in A['quantityLadderEvents'] if e.get('event')=='QTY_FIFO_MANAGED_FILL' and e.get('route')=='ACTIVE' and act and e.get('key')==act.get('key')),None)
   ord_e=next((e for e in B['quantityLadderEvents'] if e.get('event')=='QTY_FIFO_ORDINARY_HANDBACK_SUBMIT' and int(e.get('originResponsibilityId',-1))==rid and int(e.get('t',0))>=t),None)
   active_px=float(fill['executionPrice']) if fill else (float(act['limitPrice']) if act else None); expand_px=float(lot['price']) if lot else None
   row={'marketId':mid,'t':t,'rid':rid,'origin':lot,'active':act,'activeFill':fill,'ordinary':ord_e,'activePairSum':(expand_px+active_px) if expand_px is not None and active_px is not None else None,'ordinaryPairSum':(expand_px+float(ord_e['price'])) if expand_px is not None and ord_e else None,'activePremiumTicks':((active_px-float(ord_e['price']))/.01) if active_px is not None and ord_e else None,'liveAtDecision':live_roles_at(A['slotHistory'],t),'recent1s':recent_fills(A['slotHistory'],t,1000),'recent3s':recent_fills(A['slotHistory'],t,3000)};rows.append(row)
   print(json.dumps({'marketId':mid,'expandPx':expand_px,'activePx':active_px,'ordinaryPx':ord_e.get('price') if ord_e else None,'activePair':row['activePairSum'],'ordinaryPair':row['ordinaryPairSum'],'premiumTicks':row['activePremiumTicks'],'live':row['liveAtDecision']['byRole'],'recent3':row['recent3s']},ensure_ascii=False),flush=True)
 op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps({'version':'PROBE_CORE_ACTIVE_CONTEXT_REPLICATION6_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'boundary':['fixed replication6','strict-past context only','no winner/PnL features','no NEW24-B']},ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
