from __future__ import annotations
import argparse,json,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_first_carrier_repair_liveness_2x2_1945869 as x
pe=x.pe

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-id',type=int,default=1945869);ap.add_argument('--output',required=True);a=ap.parse_args();mid=int(a.market_id)
 tmp=Path(tempfile.mkdtemp(prefix='dtrace_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(r['marketId']):r for r in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[mid]
  models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];tape=tmp/'tapes'/f'{mid}.json.xz'
  s=pe.make(x.D_Both,tape,models,life,cap,tim,econ,price,sur,t44,t47)
  try:
   r=s.run_first_carrier_relay(models,cr['winner']);cons,bound,parents=pe.alloc(s,r)
   physical=[]
   for key,e in getattr(s,'carrierLedger',{}).items():
    q=float(e.get('actualFilled') or 0.0)
    if q<=1e-9:continue
    o=getattr(s,'orders',{}).get(key,{})
    physical.append({'key':key,'side':e.get('side') or o.get('side'),'actualFilled':q,'submittedQty':e.get('submittedQty') or o.get('qty'),'price':o.get('price'),'objectiveRole':e.get('objectiveRole') or o.get('objective_role'),'lane':e.get('lane'),'parentId':e.get('parentId'),'objectiveId':e.get('objectiveId') or o.get('objective_id'),'submittedAt':e.get('submittedAt') or o.get('placed')})
   physical.sort(key=lambda z:int(z.get('submittedAt') or 0))
   names=['v53Fills','fillTrace','allocationV2Events','v84Events','v80ManagementEvents','transitionEvents','reservationAwareEvents','firstCarrierEvents','paymentAwareLeaseEvents','expandFillBirthEvents','expandFillBirthPending','expandFillResponsibilityEvents','expandFillResponsibilityPending','generationEpochEvents','v48GenerationEvents','v48PaymentEvents','v70dEvents','v70gGenerationEvents','v83Admissions','v44Decisions','parentBirthTrace','submitTrace','repairChurn','queueLeaseEvents','passiveEvidenceEvents','initialRepairEpochEvents']
   sim_events={k:getattr(s,k) for k in names if hasattr(s,k)}
  finally:s.close()
  out={'marketId':mid,'winnerPostHocOnly':cr['winner'],'pnl':r.get('pnlDiagnosticOnly'),'floor':r.get('floor'),'fills':r.get('actualFillEvents'),'rounds':r.get('v70dSemanticRounds') or r.get('rounds'),'repairParentBirths':r.get('repairParentBirths'),'repairParentCompletions':r.get('repairParentCompletions'),'physicalCarriers':physical,'events':sim_events,'allocationParents':parents,'allocationConservation':cons,'allocationParentDebtBounded':bound,'safety':pe.safety(r)}
  Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
  compact={k:v for k,v in sim_events.items() if k in ['expandFillBirthEvents','generationEpochEvents','v70dEvents','v70gGenerationEvents','v83Admissions','v44Decisions','parentBirthTrace']}
  print(json.dumps({'pnl':out['pnl'],'floor':out['floor'],'fills':out['fills'],'rounds':out['rounds'],'physicalCarriers':physical,'keyEvents':compact,'allocationParents':parents},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
