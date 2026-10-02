from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v53=sib('eth_v53_for_v61','run_eth_repair_v53_multicycle_audit.py');v38=v53.v38;v1=v53.v52.v51.v1;EPS=1e-9

class V61PendingResidual(v53.V53MultiCycleAudit):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v61Pending={};self.v61SeenExpand=set();self.v61Created=0;self.v61Attached=0;self.v61SatisfiedExistingActive=0;self.v61ResolvedPassive=0;self.v61Waits=0;self.v61Blocks=0;self.v61Events=[]
 def _new_strict_expands(self):
  ev=self.v53Fills;out=[]
  for i,x in enumerate(ev):
   if x['role']!='PASSIVE_EXPAND' or x['key'] in self.v61SeenExpand:continue
   prior=[z for z in ev[:i] if z['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR','PASSIVE_EXPAND','ACTIVE_EXPAND')]
   if prior and prior[-1]['role']=='PASSIVE_REPAIR':out.append(x)
  return out
 def _active_fill_after(self,t):return next((z for z in self.v53Fills if z['role']=='ACTIVE_REPAIR' and int(z['t'])>int(t)),None)
 def _active_live_for_parent(self,pid):
  a=getattr(self,'activeByParent',{}).get(pid)
  if not a:return False
  o=self.orders.get(a.get('key'))
  try:return bool(o and v1.live(self.snap(o).get('status')))
  except Exception:return False
 def _attempt_attach(self,t,ob):
  if ob['state']!='PENDING':return
  af=self._active_fill_after(ob['expandT'])
  if af is not None:
   ob['state']='SATISFIED_EXISTING_ACTIVE';self.v61SatisfiedExistingActive+=1;self.v61Events.append({'t':int(t),'event':'SATISFIED_EXISTING_ACTIVE','expandKey':ob['expandKey'],'activeKey':af['key'],'activeT':af['t']});return
  rp=self.repairParent;pay=self._current_payoffs()
  if pay['gap']<=EPS:
   # only resolve after at least one later receipt; same-receipt zero gap may simply mean the new parent is not born yet
   if int(t)>int(ob['expandT']):ob['state']='RESOLVED_WITHOUT_ACTIVE';self.v61ResolvedPassive+=1;self.v61Events.append({'t':int(t),'event':'RESOLVED_WITHOUT_ACTIVE','expandKey':ob['expandKey']})
   else:self.v61Waits+=1
   return
  if rp is None:
   self.v61Waits+=1;return
  pid=int(rp['id']);side=rp.get('side');psv=self._find_passive(pid)
  if side not in ('UP','DOWN') or psv is None or self._active_live_for_parent(pid):self.v61Waits+=1;return
  passive_key,e,rem,o=psv;qv=v1.quotes(self.book)
  if not qv or qv.get(side,{}).get('ask') is None:self.v61Waits+=1;return
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(legal,float(pay['gap']),float(rem))
  if q<=EPS or q+EPS<legal:self.v61Waits+=1;return
  ppx=float(o.get('price') or e.get('price') or 0.0);passive_legal=1.0/ppx if ppx>EPS else math.inf
  floor0,u,d,cost=self._raw_floor();u=float(u);d=float(d);cost=float(cost);u2=u+q if side=='UP' else u;d2=d+q if side=='DOWN' else d;c2=cost+ask*q;floor2=min(u2,d2)-c2;gap2=max(0.,float(pay['gap'])-q)
  if not (floor2>=-EPS or (math.isfinite(passive_legal) and gap2+EPS>=passive_legal)):
   self.v61Blocks+=1;self.v61Events.append({'t':int(t),'event':'V50_STRANDING_BLOCK','expandKey':ob['expandKey'],'parentId':pid,'gap2':gap2,'passiveLegal':passive_legal});return
  oid=e.get('objectiveId') or (self.repairLaneObjective.get('id') if getattr(self,'repairLaneObjective',None) else None)
  if self._submit_active(t,pid,passive_key,side,ask,q,oid):
   ob.update({'state':'ATTACHED_ACTIVE','parentId':pid,'activeSubmitT':int(t)});self.v61Attached+=1;self.v61Events.append({'t':int(t),'event':'PENDING_RESIDUAL_ATTACHED_ACTIVE','expandKey':ob['expandKey'],'parentId':pid,'passiveKey':passive_key,'qty':q,'ask':ask,'projectedFloor':floor2,'projectedGap':gap2})
 def process(self,t):
  super().process(t);self._refresh_carrier_ledger(t)
  for x in self._new_strict_expands():
   self.v61SeenExpand.add(x['key']);self.v61Pending[x['key']]={'expandKey':x['key'],'expandT':int(x['t']),'state':'PENDING'};self.v61Created+=1;self.v61Events.append({'t':int(t),'event':'PENDING_RESIDUAL_BIRTH','expandKey':x['key'],'expandT':int(x['t'])})
  for ob in list(self.v61Pending.values()):self._attempt_attach(t,ob)
 def run_exam_v61(self,models,winner):
  r=super().run_exam_v53(models,winner);self._refresh_carrier_ledger(int(self.capEnd));ev=sorted(self.v53Fills,key=lambda x:(x['t'],x['key']));cs=self._cycle_stats(ev);counts={}
  for x in ev:counts[x['role']]=counts.get(x['role'],0)+1
  r.update({'v61Created':self.v61Created,'v61Attached':self.v61Attached,'v61SatisfiedExistingActive':self.v61SatisfiedExistingActive,'v61ResolvedPassive':self.v61ResolvedPassive,'v61Waits':self.v61Waits,'v61Blocks':self.v61Blocks,'v61PendingFinal':sum(ob['state']=='PENDING' for ob in self.v61Pending.values()),'v61States':{k:v['state'] for k,v in self.v61Pending.items()},'v61Events':self.v61Events[:160],'v61RoleCounts':counts,'v61RepairExpandRepairRounds':cs['repairExpandRepairRounds'],'v61StrictRounds':cs['passiveRepairExpandActiveRepairRounds'],'v61CompressedSequence':cs['compressedSequence'][:80]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v61_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V61','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V61_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V61PendingResidual(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=sim.run_exam_v61(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'candidate':r});print(json.dumps({'marketId':mid,'created':r['v61Created'],'attached':r['v61Attached'],'existingActive':r['v61SatisfiedExistingActive'],'resolvedPassive':r['v61ResolvedPassive'],'pending':r['v61PendingFinal'],'roles':r['v61RoleCounts'],'rounds':r['v61RepairExpandRepairRounds'],'strict':r['v61StrictRounds']},ensure_ascii=False),flush=True)
  def sm(k):return sum(float(x['candidate'].get(k) or 0) for x in rows)
  agg={'markets':len(rows),'created':int(sm('v61Created')),'attached':int(sm('v61Attached')),'satisfiedExistingActive':int(sm('v61SatisfiedExistingActive')),'resolvedPassive':int(sm('v61ResolvedPassive')),'pendingFinal':int(sm('v61PendingFinal')),'activeFillQty':sm('v36ActiveFillQty'),'rounds':int(sm('v61RepairExpandRepairRounds')),'strictRounds':int(sm('v61StrictRounds')),'truthMismatch':sm('authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('overOwnedSubmitViolations'),'repairDrift':sm('repairToExpandAtFirstFill'),'responsibilityOverfill':sm('v51ResponsibilityOverfill')}
  gates={'obligationBirthExercised':agg['created']>0,'obligationCanBeSatisfiedOrAttached':agg['attached']+agg['satisfiedExistingActive']>0,'strictCycleExercised':agg['strictRounds']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  out={'version':'ETH_REPAIR_V61_PENDING_RESIDUAL_OBLIGATION_SMOKE','researchOnly':True,'behaviorChange':True,'actionAuthority':'FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['PASSIVE_REPAIR->PASSIVE_EXPAND births pending residual obligation','obligation is not consumed until Repair responsibility/carrier materializes','existing inherited Active Repair after expansion satisfies obligation without duplicate submit','V50/V51/V52 safety inherited','no PnL/winner/Target-future trigger','small-scale only','no tuning/no H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
