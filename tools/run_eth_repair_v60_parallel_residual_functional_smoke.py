from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v53=sib('eth_v53_for_v60','run_eth_repair_v53_multicycle_audit.py');v38=v53.v38;v1=v53.v52.v51.v1;EPS=1e-9

class V60ParallelResidual(v53.V53MultiCycleAudit):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v60SeenExpand=set();self.v60Attempts=0;self.v60Submits=0;self.v60Blocks=0;self.v60Events=[]
 def _active_live_for_parent(self,pid):
  a=getattr(self,'activeByParent',{}).get(pid)
  if not a:return False
  o=self.orders.get(a.get('key'))
  try:return bool(o and v1.live(self.snap(o).get('status')))
  except Exception:return False
 def _strict_expand_candidates(self):
  ev=self.v53Fills
  out=[]
  for i,x in enumerate(ev):
   if x['role']!='PASSIVE_EXPAND' or x['key'] in self.v60SeenExpand:continue
   prev=[z for z in ev[:i] if z['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR','PASSIVE_EXPAND','ACTIVE_EXPAND')]
   if prev and prev[-1]['role']=='PASSIVE_REPAIR':out.append(x)
  return out
 def _try_residual(self,t,exp):
  self.v60SeenExpand.add(exp['key']);self.v60Attempts+=1
  rp=self.repairParent
  if rp is None:self.v60Blocks+=1;self.v60Events.append({'t':int(t),'event':'BLOCK','reason':'NO_REPAIR_PARENT','expandKey':exp['key']});return False
  pid=int(rp['id']);side=rp.get('side')
  if side not in ('UP','DOWN') or self._active_live_for_parent(pid):self.v60Blocks+=1;self.v60Events.append({'t':int(t),'event':'BLOCK','reason':'ACTIVE_OR_SIDE','expandKey':exp['key'],'parentId':pid});return False
  pay=self._current_payoffs();psv=self._find_passive(pid)
  if pay['gap']<=EPS or psv is None:self.v60Blocks+=1;self.v60Events.append({'t':int(t),'event':'BLOCK','reason':'NO_GAP_OR_PASSIVE','expandKey':exp['key'],'parentId':pid});return False
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:self.v60Blocks+=1;return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(legal,float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:self.v60Blocks+=1;self.v60Events.append({'t':int(t),'event':'BLOCK','reason':'NO_LEGAL_ACTIVE_SLICE','parentId':pid});return False
  ppx=float(o.get('price') or e.get('price') or 0.0);passive_legal=1.0/ppx if ppx>EPS else math.inf
  floor0,u,d,cost=self._raw_floor();u=float(u);d=float(d);cost=float(cost);u2=u+q if side=='UP' else u;d2=d+q if side=='DOWN' else d;c2=cost+ask*q;floor2=min(u2,d2)-c2;gap2=max(0.,float(pay['gap'])-q)
  allowed=floor2>=-EPS or (math.isfinite(passive_legal) and gap2+EPS>=passive_legal)
  if not allowed:self.v60Blocks+=1;self.v60Events.append({'t':int(t),'event':'BLOCK','reason':'V50_STRANDING','parentId':pid,'gap2':gap2,'passiveLegal':passive_legal});return False
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  ok=self._submit_active(t,pid,passive_key,side,ask,q,oid)
  if ok:
   self.v60Submits+=1;self.v60Events.append({'t':int(t),'event':'PARALLEL_RESIDUAL_SUBMIT','parentId':pid,'expandKey':exp['key'],'passiveKey':passive_key,'qty':q,'ask':ask,'projectedFloor':floor2,'projectedGap':gap2})
  return ok
 def process(self,t):
  super().process(t)
  # refresh after all inherited management actions at this receipt frontier
  self._refresh_carrier_ledger(t)
  for exp in self._strict_expand_candidates():self._try_residual(t,exp)
 def run_exam_v60(self,models,winner):
  r=super().run_exam_v53(models,winner);self._refresh_carrier_ledger(int(self.capEnd));ev=sorted(self.v53Fills,key=lambda x:(x['t'],x['key']));cs=self._cycle_stats(ev);counts={}
  for x in ev:counts[x['role']]=counts.get(x['role'],0)+1
  r.update({'v60Attempts':self.v60Attempts,'v60Submits':self.v60Submits,'v60Blocks':self.v60Blocks,'v60Events':self.v60Events[:120],'v60RoleCounts':counts,'v60RepairExpandRepairRounds':cs['repairExpandRepairRounds'],'v60StrictRounds':cs['passiveRepairExpandActiveRepairRounds'],'v60CompressedSequence':cs['compressedSequence'][:80]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v60_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V60','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V60_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V60ParallelResidual(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=sim.run_exam_v60(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'candidate':r});print(json.dumps({'marketId':mid,'attempts':r['v60Attempts'],'submits':r['v60Submits'],'roles':r['v60RoleCounts'],'rounds':r['v60RepairExpandRepairRounds'],'strict':r['v60StrictRounds'],'pnlDiagnosticOnly':r['pnlDiagnosticOnly']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['candidate'].get(k) or 0) for x in rows)
  agg={'markets':len(rows),'attempts':int(sm('v60Attempts')),'submits':int(sm('v60Submits')),'activeFillQty':sm('v36ActiveFillQty'),'rounds':int(sm('v60RepairExpandRepairRounds')),'strictRounds':int(sm('v60StrictRounds')),'truthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('overOwnedSubmitViolations'),'repairDrift':sm('repairToExpandAtFirstFill'),'responsibilityOverfill':sm('v51ResponsibilityOverfill'),'pnlDiagnosticOnly':sm('pnlDiagnosticOnly')}
  gates={'residualAuthorityExercised':agg['submits']>0,'activeActuallyFilled':agg['activeFillQty']>EPS,'strictCycleExercised':agg['strictRounds']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  out={'version':'ETH_REPAIR_V60_PARALLEL_RESIDUAL_FUNCTIONAL_SMOKE','researchOnly':True,'behaviorChange':True,'actionAuthority':'FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['authority only after actual PASSIVE_REPAIR then PASSIVE_EXPAND materialization','minimum legal active Repair slice only','V50 post-active passive recoverability mandatory','V51/V52 shared-budget handoff inherited','no PnL/winner/Target-future trigger','no threshold/qty/delay tuning','small-scale only','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
