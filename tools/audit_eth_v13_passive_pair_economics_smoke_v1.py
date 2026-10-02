from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,statistics
from pathlib import Path
import numpy as np
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))

def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13_pair_audit','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9_pair_audit','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
from tools import run_eth_repair_functional_exam_v1 as ex1
EPS=1e-9

def match_events(events):
 unmatched={'UP':[],'DOWN':[]};pairs=[]
 for e in events:
  side=str(e['side']);qty=float(e['shares']);price=float(e['price']);rel=int(e.get('rel') or 0);opp='DOWN' if side=='UP' else 'UP';rem=qty
  lots=unmatched[opp]
  while rem>EPS and lots:
   lot=lots[0];m=min(rem,lot['qty']);ps=float(lot['price'])+price
   pairs.append({'qty':m,'pairSum':ps,'newSide':side,'newPrice':price,'newRel':rel,'oldSide':opp,'oldPrice':float(lot['price']),'oldRel':int(lot['rel'])})
   rem-=m;lot['qty']-=m
   if lot['qty']<=EPS:lots.pop(0)
  if rem>EPS:unmatched[side].append({'qty':rem,'price':price,'rel':rel})
 paired=sum(x['qty'] for x in pairs);reserve=sum(x['qty']*max(0.,1-x['pairSum']) for x in pairs);debt=sum(x['qty']*max(0.,x['pairSum']-1) for x in pairs)
 return {
  'pairedQtyTracked':paired,
  'weightedPairSum':sum(x['qty']*x['pairSum'] for x in pairs)/paired if paired else None,
  'pairReserve':reserve,'pairDebt':debt,'netPairEdge':reserve-debt,
  'pairedQtyBelow098':sum(x['qty'] for x in pairs if x['pairSum']<.98),
  'pairedQtyBelow1':sum(x['qty'] for x in pairs if x['pairSum']<1.),
  'pairedQtyEq1Band':sum(x['qty'] for x in pairs if .98<=x['pairSum']<=1.02),
  'pairedQtyAbove1':sum(x['qty'] for x in pairs if x['pairSum']>1.),
  'pairedQtyAbove102':sum(x['qty'] for x in pairs if x['pairSum']>1.02),
  'repairCompletedPairQty':sum(x['qty'] for x in pairs if x['newRel']==1),
  'repairCompletedWeightedPairSum':(sum(x['qty']*x['pairSum'] for x in pairs if x['newRel']==1)/sum(x['qty'] for x in pairs if x['newRel']==1)) if sum(x['qty'] for x in pairs if x['newRel']==1)>EPS else None,
  'unmatchedQty':sum(l['qty'] for arr in unmatched.values() for l in arr),
  'pairs':pairs
 }

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--market-ids',default='1816255,1816860,1818007,1818920,1820056,1820148');ap.add_argument('--output');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_pair_audit_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort if r['split']!='TRAIN40'};ids=[int(x) for x in a.market_ids.split(',') if x.strip()];models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');timing=v13.Wait10Runtime(a.timing_model);rows=[]
  cfg=ex1.SCENARIOS['CONTROL']
  for i,mid in enumerate(ids,1):
   cr=by[mid];sim=v13.AnchoredWait10Sim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,cfg['fillObsLagMs'],cfg['ackReleaseLagMs'],capability=cap,timing=timing)
   try:
    base=sim.run_exam_v13(models,cr['winner']);eco=match_events(sim.authHist)
   finally:sim.close()
   row={'marketId':mid,'pnlDiagnosticOnly':base['pnlDiagnosticOnly'],'pairCoverage':base['pairCoverage'],'absNet':base['absNet'],**{k:v for k,v in eco.items() if k!='pairs'}};rows.append(row);print(json.dumps({'progress':i,**row},ensure_ascii=False),flush=True)
  paired=sum(r['pairedQtyTracked'] for r in rows);reserve=sum(r['pairReserve'] for r in rows);debt=sum(r['pairDebt'] for r in rows)
  agg={'markets':len(rows),'pairedQtyTracked':paired,'weightedPairSum':sum((r['weightedPairSum'] or 0)*r['pairedQtyTracked'] for r in rows)/paired if paired else None,'pairReserve':reserve,'pairDebt':debt,'netPairEdge':reserve-debt,'pairedQtyBelow1':sum(r['pairedQtyBelow1'] for r in rows),'pairedQtyAbove1':sum(r['pairedQtyAbove1'] for r in rows),'pairedQtyAbove102':sum(r['pairedQtyAbove102'] for r in rows),'repairCompletedPairQty':sum(r['repairCompletedPairQty'] for r in rows),'meanFinalPairCoverage':statistics.mean(r['pairCoverage'] for r in rows),'meanFinalAbsNet':statistics.mean(r['absNet'] for r in rows),'totalPnlDiagnosticOnly':sum(r['pnlDiagnosticOnly'] for r in rows)}
  smoke={'pairedQtyNonzero':paired>0,'reserveDebtAccountingFinite':all(np.isfinite([reserve,debt,agg['netPairEdge']])),'bothCheapAndExpensivePairsObserved':agg['pairedQtyBelow1']>0 and agg['pairedQtyAbove1']>0}
  out={'version':'ETH_V13_PASSIVE_PAIR_ECONOMICS_SMOKE_V1','researchOnly':True,'policyChanged':False,'metricSemantics':'FIFO matching of actual authoritative fill events; pairSum=opposite unmatched fill price + new fill price','aggregate':agg,'smokeGates':smoke,'smokeVerified':all(smoke.values()),'rows':rows,'boundary':['diagnostic only','consumed development markets','actual HFT fills from authHist','no winner/PnL policy input','no admission rule changed']};op=Path(a.output) if a.output else Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'smokeVerified':out['smokeVerified'],'aggregate':agg},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
