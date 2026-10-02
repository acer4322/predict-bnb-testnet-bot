from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib,math
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v53=sib('eth_v53_for_v64','run_eth_repair_v53_multicycle_audit.py');v38=v53.v38;v1=v53.v52.v51.v1;EPS=1e-9
ACTIVE_WINDOW_MS=500

class V64ActiveExpandFallback(v53.V53MultiCycleAudit):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v64Handled=set();self.v64Active={};self.v64Submits=0;self.v64FillQty=0.;self.v64Events=[]
 def _later_v44_exists(self,key):
  e=self.carrierLedger.get(key,{});t0=int(e.get('submittedAt') or 0);oid=e.get('objectiveId')
  for k in self.v44Keys:
   if k==key:continue
   z=self.carrierLedger.get(k,{})
   if int(z.get('submittedAt') or 0)>t0 and (oid is None or z.get('objectiveId')==oid):return True
  return False
 def _submit_active_expand(self,t,source_key,e,o,remaining):
  if int(self.capEnd)-int(t)<=180000:return False
  side=e.get('side') or o.get('side');qv=v1.quotes(self.book)
  if side not in ('UP','DOWN') or not qv or qv.get(side,{}).get('ask') is None:return False
  ask=float(qv[side]['ask']);legal=1.0/ask if ask>EPS else math.inf;q=min(legal,float(remaining))
  if q<=EPS or q+EPS<legal:return False
  n=self.n;self.n+=1;native_side,native_price=v1.ex.native_order(side,ask)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),v1.ex.hbt.GTC,v1.ex.LIMIT,False))
  except Exception:return False
  key=f'{side}_{n}';oid=e.get('objectiveId') or o.get('objective_id')
  self.orders[key]={'n':n,'side':side,'price':ask,'qty':q,'cum':0.0,'placed':int(t),'status':'NEW','objective_role':'EXPAND','objective_id':oid,'execution_role':'TAKER_ACTIVE_EXPAND_SHARED'}
  self.placeHist.append((int(t),side,q,ask));self.submits+=1;self.localPending[side][key]={'remaining':q,'submitted':int(t)};self.submitRoleObserved[key]='EXPAND';self.submitRoleTruth[key]='EXPAND';self.submitRoleAuthorized[key]='EXPAND'
  self.carrierLedger[key]={'key':key,'side':side,'objectiveId':oid,'objectiveRole':'EXPAND','submittedQty':q,'actualFilled':0.0,'submittedAt':int(t),'cancelRequested':False,'terminalConfirmed':False,'lastStatus':None,'lastSeenAt':int(t),'parentId':None,'lane':'ACTIVE_EXPAND_SHARED'}
  self.submitTrace.append({'t':int(t),'side':side,'qty':q,'price':ask,'pendingRole':'EXPAND','parentId':None,'lane':'ACTIVE_EXPAND_SHARED'})
  self.v64Active[key]={'sourceKey':source_key,'submitAt':int(t),'fillSeen':0.0,'qty':q};self.v64Submits+=1;self.v64Events.append({'t':int(t),'event':'ACTIVE_EXPAND_FALLBACK_SUBMIT','sourceKey':source_key,'key':key,'objectiveId':oid,'side':side,'ask':ask,'qty':q,'submitRc':rc});return True
 def _scan_failed_v44(self,t):
  self._refresh_carrier_ledger(t)
  for k in sorted(self.v44Keys,key=lambda x:int(self.carrierLedger.get(x,{}).get('submittedAt') or 0)):
   if k in self.v64Handled:continue
   e=self.carrierLedger.get(k,{});o=self.orders.get(k,{})
   if not bool(e.get('terminalConfirmed')):continue
   filled=float(e.get('actualFilled') or 0.0);submitted=float(e.get('submittedQty') or o.get('qty') or 0.0);rem=max(0.,submitted-filled)
   self.v64Handled.add(k)
   if filled>EPS:self.v64Events.append({'t':int(t),'event':'PASSIVE_EXPAND_MATERIALIZED_NO_FALLBACK','sourceKey':k,'filled':filled});continue
   if self._later_v44_exists(k):self.v64Events.append({'t':int(t),'event':'FAILED_EXPAND_SUPERSEDED','sourceKey':k});continue
   if rem<=EPS:continue
   self._submit_active_expand(t,k,e,o,rem)
 def _reconcile_active_expand(self,t):
  self._refresh_carrier_ledger(t)
  for k,a in list(self.v64Active.items()):
   e=self.carrierLedger.get(k,{});cur=float(e.get('actualFilled') or 0.0);old=float(a.get('fillSeen') or 0.0)
   if cur>old+EPS:
    inc=cur-old;a['fillSeen']=cur;self.v64FillQty+=inc;self.v64Events.append({'t':int(t),'event':'ACTIVE_EXPAND_FALLBACK_FILL','key':k,'sourceKey':a['sourceKey'],'incQty':inc,'cumQty':cur})
   o=self.orders.get(k)
   try:live=bool(o and v1.live(self.snap(o).get('status')))
   except Exception:live=False
   if live and int(t)-int(a['submitAt'])>=ACTIVE_WINDOW_MS and k not in self.cancelRequestedAt:
    if self._cancel_key(t,k):self.v64Events.append({'t':int(t),'event':'ACTIVE_EXPAND_FALLBACK_CANCEL_REMAINDER','key':k,'ageMs':int(t)-int(a['submitAt'])})
 def process(self,t):
  super().process(t);self._reconcile_active_expand(t);self._scan_failed_v44(t)
 def cancel_expired(self,t):
  super().cancel_expired(t);self._reconcile_active_expand(t);self._scan_failed_v44(t)
 def run_exam_v64(self,models,winner):
  r=super().run_exam_v53(models,winner);self._reconcile_active_expand(int(self.capEnd));ev=sorted(self.v53Fills,key=lambda x:(x['t'],x['key']));cs=self._cycle_stats(ev);counts={}
  for x in ev:counts[x['role']]=counts.get(x['role'],0)+1
  generated_repaired=0
  for i,x in enumerate(ev):
   if x['key'] not in self.v64Active or x['role']!='ACTIVE_EXPAND':continue
   nxt=next((j for j in range(i+1,len(ev)) if ev[j]['role'] in ('PASSIVE_EXPAND','ACTIVE_EXPAND')),len(ev));post=ev[i+1:nxt]
   if any(z['role'] in ('PASSIVE_REPAIR','ACTIVE_REPAIR') for z in post):generated_repaired+=1
  r.update({'v64Submits':self.v64Submits,'v64FillQty':self.v64FillQty,'v64Events':self.v64Events[:160],'v64RoleCounts':counts,'v64Rounds':cs['repairExpandRepairRounds'],'v64StrictRounds':cs['passiveRepairExpandActiveRepairRounds'],'v64GeneratedExpandThenRepair':generated_repaired,'v64CompressedSequence':cs['compressedSequence'][:80]});return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v64_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V64','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V64_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];tape=tmp/'tapes'/f'{mid}.json.xz';b=v53.V53MultiCycleAudit(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:br=b.run_exam_v53(models,cr['winner'])
   finally:b.close()
   c=V64ActiveExpandFallback(tape,'BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:rr=c.run_exam_v64(models,cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'baseline':br,'candidate':rr});print(json.dumps({'marketId':mid,'submits':rr['v64Submits'],'fillQty':rr['v64FillQty'],'roles':rr['v64RoleCounts'],'rounds':[br['repairExpandRepairRounds'],rr['v64Rounds']],'strict':[br['passiveRepairExpandActiveRepairRounds'],rr['v64StrictRounds']],'generatedRepaired':rr['v64GeneratedExpandThenRepair']},ensure_ascii=False),flush=True)
  def sm(side,k):return sum(float(x[side].get(k) or 0) for x in rows)
  agg={'markets':len(rows),'activeExpandSubmits':int(sm('candidate','v64Submits')),'activeExpandFillQty':sm('candidate','v64FillQty'),'baselineRounds':int(sm('baseline','repairExpandRepairRounds')),'candidateRounds':int(sm('candidate','v64Rounds')),'roundGain':int(sm('candidate','v64Rounds')-sm('baseline','repairExpandRepairRounds')),'generatedRepaired':int(sm('candidate','v64GeneratedExpandThenRepair')),'truthMismatch':sm('candidate','authorizedSubmitWithTruthRoleMismatch'),'overOwned':sm('candidate','overOwnedSubmitViolations'),'repairDrift':sm('candidate','repairToExpandAtFirstFill'),'responsibilityOverfill':sm('candidate','v51ResponsibilityOverfill')}
  gates={'activeExpandActuallyFilled':agg['activeExpandFillQty']>EPS,'generatedActiveExpandGetsRepair':agg['generatedRepaired']>0,'roundsNonDecreasing':agg['roundGain']>=0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=EPS}
  out={'version':'ETH_REPAIR_V64_ACTIVE_EXPAND_EXECUTION_FALLBACK_SMOKE','researchOnly':True,'behaviorChange':True,'actionAuthority':'FUNCTIONAL_SMOKE_ONLY','aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'rows':rows,'boundary':['only V44-authorized Expand objective may fallback','passive carrier must be terminal and zero-fill','failed objective skipped if superseded by later same-objective V44 carrier','active qty capped by original authorized remaining qty and venue-min legal slice','<=180s no-new-exposure preserved','500ms active remainder window inherited from active execution mechanic','small-scale only','no PnL/winner/threshold/qty/delay tuning','no H100/no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'functionalPass':out['functionalPass'],'aggregate':agg,'gates':gates},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
