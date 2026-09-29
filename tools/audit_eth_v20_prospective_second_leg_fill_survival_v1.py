from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,joblib,importlib.util,os,math
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).resolve().with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
try:
 from tools import run_eth_repair_functional_exam_v20_deterministic_reserve_obligation as v20
except ImportError:v20=sib('v20prospective','run_eth_repair_functional_exam_v20_deterministic_reserve_obligation.py')
try:
 from tools import run_eth_repair_functional_exam_v9_conjunctive_parallel_router as v9
except ImportError:v9=sib('v9prospective','run_eth_repair_functional_exam_v9_conjunctive_parallel_router.py')
try:
 from tools import run_eth_repair_functional_exam_v13_anchored_wait10_scheduler as v13
except ImportError:v13=sib('v13prospective','run_eth_repair_functional_exam_v13_anchored_wait10_scheduler.py')
try:
 from tools import run_eth_repair_functional_exam_v15_economic_lane_admission as v15
except ImportError:v15=sib('v15prospective','run_eth_repair_functional_exam_v15_economic_lane_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v16_phase_aware_economic_admission as v16
except ImportError:v16=sib('v16prospective','run_eth_repair_functional_exam_v16_phase_aware_economic_admission.py')
try:
 from tools import run_eth_repair_functional_exam_v17_post_safe_surplus_authority as v17
except ImportError:v17=sib('v17prospective','run_eth_repair_functional_exam_v17_post_safe_surplus_authority.py')
from tools import run_eth_repair_functional_exam_v3_objective_handoff as v3
EPS=1e-9

class ProspectiveFillAuditSim(v20.DeterministicReserveObligationSim):
 def __init__(self,*a,fill_art=None,**kw):
  super().__init__(*a,**kw);self.fillArt=fill_art;self.fillFeatures=list(fill_art['features']);self.fillModel=fill_art['models']['fill_5s'];self.builderAudits=[]
 def _native_depth(self,side,p):
  if side=='UP':return float(self.book.get('bids',{}).get(round(float(p),10),0.0))
  return float(self.book.get('asks',{}).get(round(1.0-float(p),10),0.0))
 def _prospective_p5(self,qv,first_side,first_p,first_q,repair_side,repair_p):
  u=float(self.inv['UP'])+(first_q if first_side=='UP' else 0.0);d=float(self.inv['DOWN'])+(first_q if first_side=='DOWN' else 0.0);cost=float(self.cost)+first_q*first_p
  gross=u+d;net=u-d;ab=abs(net);paired=min(u,d);floor=paired-cost;best=max(u,d)-cost
  bid=float(qv[repair_side]['bid']);ask=float(qv[repair_side]['ask']);qty=max(first_q,1.0/repair_p if repair_p>EPS else 1e9)
  vals={k:math.nan for k in self.fillFeatures}
  vals.update({'side_is_up':float(repair_side=='UP'),'order_age_ms':0.0,'quote_price':float(repair_p),'status_none':0.0,'status_new':1.0,'status_partial':0.0,'cum_exec_qty':0.0,'remaining_qty':float(qty),'remaining_ratio':1.0,'partial_fill_ratio':0.0,'active_same_count':1.0,'active_opp_count':0.0,'quote_offset_ticks':(bid-repair_p)/0.01,'current_bid':bid,'current_ask':ask,'current_spread_ticks':(ask-bid)/0.01,'initial_depth':self._native_depth(repair_side,repair_p),'public_cum_depletion':0.0,'public_depletion_ratio':0.0,'public_any_depletion':0.0,'maker_gross':gross,'maker_net':net,'maker_abs_net':ab,'maker_imbalance_ratio':ab/gross if gross>EPS else 0.0,'maker_paired_coverage':2*paired/gross if gross>EPS else 1.0,'taker_gross':0.0,'taker_net':0.0,'taker_abs_net':0.0,'taker_paired_coverage':1.0,'combined_gross':gross,'combined_net':net,'combined_abs_net':ab,'combined_imbalance_ratio':ab/gross if gross>EPS else 0.0,'combined_paired_coverage':2*paired/gross if gross>EPS else 1.0,'worst_case_floor':floor,'best_case_pnl':best,'abs_payoff_gap':abs(best-floor),'last_maker_age_ms':0.0,'maker_fills_1s':1.0,'maker_fills_5s':1.0,'maker_fills_10s':1.0,'maker_shares_5s':first_q,'maker_shares_10s':first_q})
  if first_side=='UP':vals['last_maker_up_age_ms']=0.0
  else:vals['last_maker_down_age_ms']=0.0
  frame=pd.DataFrame([{k:vals[k] for k in self.fillFeatures}],columns=self.fillFeatures)
  return float(self.fillModel.predict_proba(frame)[0,1]),vals
 def _start_reserve_builder(self,t,qv):
  floor,u,d,cost,absr,s=self._thin_balanced_package(qv)
  if self.reserveBuilder is not None or self.repairParent is not None or self.outstanding_total()>EPS:return False
  if not (0<=floor<1.0 and absr<=.02 and s<1.-1e-9):return False
  pu=float(qv['UP']['bid']);pd=float(qv['DOWN']['bid']);side='UP' if pu<pd-EPS else 'DOWN' if pd<pu-EPS else ('UP' if (self.n%2==0) else 'DOWN');p=float(qv[side]['bid']);q=1/p if p>EPS else 1e9
  opp='DOWN' if side=='UP' else 'UP';ceiling=1.0-p-0.01;ask=float(qv[opp]['ask']);raw=min(ceiling,ask-0.01);rp=math.floor((raw+1e-10)*100.0)/100.0
  if q<=EPS or q>12.+EPS or rp<=0 or rp>=ask-EPS or p+rp>=1.-EPS:return False
  score,vals=self._prospective_p5(qv,side,p,q,opp,rp)
  ok=super()._start_reserve_builder(t,qv)
  if ok and self.reserveBuilder is not None:
   self.reserveBuilder['prospectiveFillP5']=score;self.reserveBuilder['prospectiveRepairPrice']=rp;self.reserveBuilder['prospectiveRepairBid']=float(qv[opp]['bid']);self.reserveBuilder['prospectiveRepairAsk']=ask;self.reserveBuilder['prospectiveInitialDepth']=float(vals['initial_depth']);self.reserveBuilder['prospectiveQuoteOffsetTicks']=float(vals['quote_offset_ticks']);self.reserveBuilder['auditStartAt']=int(t);self.builderAudits.append(self.reserveBuilder)
  return ok
 def audit_rows(self):
  out=[]
  for i,rb in enumerate(self.builderAudits,1):
   out.append({'cycleIndex':i,'firstSide':rb.get('firstSide'),'firstPrice':rb.get('firstPrice'),'firstQty':rb.get('firstQty'),'firstFillAt':rb.get('firstFillAt'),'prospectiveFillP5':rb.get('prospectiveFillP5'),'prospectiveRepairPrice':rb.get('prospectiveRepairPrice'),'prospectiveRepairBid':rb.get('prospectiveRepairBid'),'prospectiveRepairAsk':rb.get('prospectiveRepairAsk'),'prospectiveInitialDepth':rb.get('prospectiveInitialDepth'),'prospectiveQuoteOffsetTicks':rb.get('prospectiveQuoteOffsetTicks'),'completed':bool(rb.get('completed')),'repairFillPrice':rb.get('repairFillPrice'),'repairFillAt':rb.get('repairFillAt'),'deterministicObligationActivated':bool(rb.get('deterministicObligationActivated'))})
  return out

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--lifecycle-model',required=True);ap.add_argument('--capability-model',required=True);ap.add_argument('--dagger-cache',required=True);ap.add_argument('--timing-model',required=True);ap.add_argument('--economic-model',required=True);ap.add_argument('--price-model',required=True);ap.add_argument('--surplus-model',required=True);ap.add_argument('--fill-model',required=True);ap.add_argument('--market-ids',default='1829435');a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v20_pfill_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);cohort=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in cohort};models,_,_=joblib.load(a.dagger_cache);life=v3.LifecycleRuntime(Path(a.lifecycle_model));cap=v9.ConjunctiveCapabilityRuntime(a.capability_model,'cpu');tim=v13.Wait10Runtime(a.timing_model);econ=v15.EconomicLaneValueRuntime(a.economic_model);price=v16.RepairPriceEnvelopeRuntime(a.price_model);sur=v17.SurplusValueRuntime(a.surplus_model);fill=joblib.load(a.fill_model);rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=ProspectiveFillAuditSim(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,fill_art=fill)
   try:r=sim.run_exam_v20(models,cr['winner']);aud=sim.audit_rows()
   finally:sim.close()
   rows.append({'marketId':mid,'functional':r,'cycles':aud});print(json.dumps({'marketId':mid,'cycles':[{'p5':x['prospectiveFillP5'],'completed':x['completed'],'price':x['prospectiveRepairPrice'],'depth':x['prospectiveInitialDepth'],'offset':x['prospectiveQuoteOffsetTicks']} for x in aud]},ensure_ascii=False),flush=True)
  flat=[c for r in rows for c in r['cycles']];comp=[float(c['prospectiveFillP5']) for c in flat if c['completed']];fail=[float(c['prospectiveFillP5']) for c in flat if not c['completed']];out={'version':'ETH_V20_PROSPECTIVE_SECOND_LEG_FILL_SURVIVAL_AUDIT_V1','researchOnly':True,'rows':rows,'aggregate':{'cycles':len(flat),'completed':sum(bool(c['completed']) for c in flat),'meanP5Completed':float(np.mean(comp)) if comp else None,'meanP5Failed':float(np.mean(fail)) if fail else None,'scoreSeparationObserved':bool(comp and fail and min(comp)>max(fail))},'boundary':['shadow only; V20 behavior unchanged','frozen open_order_fill_lifecycle_v0 fill5s model','Student HFT model only; no Target/winner/future runtime data','prospective state assumes first leg just filled then second leg posts at economic ceiling']};op=Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
