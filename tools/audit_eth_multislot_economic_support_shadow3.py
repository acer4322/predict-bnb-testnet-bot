from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
import joblib
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))
import tools.run_eth_parent_occupancy_prospective_guard_ab as pg
import tools.run_eth_initial_repair_quota_partition_candidate_only_1946475 as base
import tools.run_eth_dagger60_smoke_v1 as v1
from tools.eth_repair_modular.spread_aware_maker_priority import SpreadAwareMakerPriorityPolicyV1,SpreadAwareMakerPriorityContext
EPS=1e-9;pe=base.pe
class Shadow(pg.ProspectiveGuardParentOccupancyHFT):
 def __init__(self,*a,**kw):self.rows=[];self.sp=SpreadAwareMakerPriorityPolicyV1();super().__init__(*a,**kw)
 def _shadow(self,t):
  rp=getattr(self,'repairParent',None)
  if not isinstance(rp,dict):return
  pid=int(rp.get('id'));side=str(rp.get('side') or '').upper()
  if side not in ('UP','DOWN'):return
  qv=v1.quotes(self.book)
  if not qv or side not in qv:return
  bid=float(qv[side]['bid']);ask=float(qv[side]['ask']);target=float(self.sp.evaluate(SpreadAwareMakerPriorityContext(bid,ask,0.01,None)).price)
  debt=max(0.0,float(self._parent_debt_now(pid)));legal=1.0/target if target>EPS else math.inf;slots=int(math.floor((debt+EPS)/legal)) if math.isfinite(legal) and legal>EPS else 0
  row={'t':int(t),'parentId':pid,'side':side,'debt':debt,'bestBid':bid,'bestAsk':ask,'targetPrice':target,'venueMinQty':legal,'structuralSlots':slots}
  if slots>=2:
   try:
    x,v=self._feature(int(t),side,legal,target);phase='baseAcquisitionRepair' if float(v['net_pair_reserve_ratio'])<=0 else 'reserveRepair';env=float(self.priceEnvelope.q80(phase,v));pair=float(v['candidate_pair_sum']);priceok=pair<=env+EPS;expok=True
    if phase=='reserveRepair' and not(float(v['match_fraction'])>0 and float(v['pair_edge'])>=-EPS):
     sc,th=self.economic.score('expensive_repair_recovers_30s',x);expok=float(sc)>=float(th);row.update({'expensiveRecoveryScore':float(sc),'expensiveRecoveryThreshold':float(th)})
    row.update({'phase':phase,'candidatePairSum':pair,'q80Envelope':env,'priceEnvelopeAllow':priceok,'economicAllow':bool(priceok and expok)})
   except Exception as e:row.update({'economicAllow':False,'error':repr(e)})
  else:row['economicAllow']=False
  self.rows.append(row)
 def process(self,t):
  out=super().process(t);self._shadow(int(t));return out
 def run_shadow(self,models,winner):
  r=self.run_guard(models,winner);return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='multislot_econ_shadow3_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(x['marketId']):x for x in co};models,life,cap,tim,econ,price,sur=pe.v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];outs=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   s=pe.make(Shadow,tmp/'tapes'/f'{mid}.json.xz',models,life,cap,tim,econ,price,sur,t44,t47)
   try:r=s.run_shadow(models,by[mid]['winner']);rows=s.rows
   finally:s.close()
   multi=[x for x in rows if int(x.get('structuralSlots') or 0)>=2];allow=[x for x in multi if x.get('economicAllow')];outs.append({'marketId':mid,'repairParentBirths':int(r.get('repairParentBirths') or 0),'shadowClocks':len(rows),'multiSlotClocks':len(multi),'economicMultiSlotClocks':len(allow),'firstEconomicAllows':allow[:12],'maxStructuralSlots':max([int(x.get('structuralSlots') or 0) for x in rows],default=0)})
  out={'version':'ETH_MULTISLOT_ECONOMIC_SUPPORT_SHADOW3_V1','date':'2026-09-05','behaviorChange':False,'rows':outs,'selectionRule':'replicate only markets with multiSlotClocks>0 and economicMultiSlotClocks>0','boundary':['baseline behavior unchanged','strict-past only','structural venue-min capacity + existing V16 economic admission','no winner/PnL selection','no Target runtime input','no 8781']};Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False))
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
