from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
 sp=importlib.util.spec_from_file_location('ms4r1',_STAGED);r1=importlib.util.module_from_spec(sp);sp.loader.exec_module(r1)
else:import tools.run_eth_ms4_r1_queue_aware_repair as r1
EPS=1e-9;ACTIVE_WINDOW_MS=500

class ParallelActiveRepairResidual(r1.QueueAwareRepairRoutingSim):
 def __init__(self,tape,max_slots=4):
  super().__init__(tape,max_slots);self.activeKeys=set();self.activeMeta={};self.activeScopeDone=set();self.activeStats=Counter();self.activeFillQty=0.0
 def _active_reserved_repair(self):
  z=0.0
  for k in self.activeKeys:
   if self.key_scope_gen.get(k)==self.scopeGeneration:z+=max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0)))
  return z
 def _reserved_repair_quota(self,repair_side=None):
  return float(super()._reserved_repair_quota(repair_side)+self._active_reserved_repair())
 def _submit_parallel_active(self,t):
  if self.scopeSide is None or self.scopeGeneration in self.activeScopeDone:return False
  end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
  if end-int(t)<=r1.v2.NO_NEW_EXPOSURE_MS:return False
  rs='DOWN' if self.scopeSide=='UP' else 'UP'
  # Require a live passive Repair carrier: this is an execution lane for an existing responsibility, not a new objective.
  if not (self._live_role_rows('ECONOMIC_CORE',rs) or self._live_role_rows('SATELLITE_REPAIR',rs)):return False
  qv=r1.v2.base.quotes(self.book)
  if not qv or qv.get(rs,{}).get('ask') is None:return False
  p=float(qv[rs]['ask']);q=1.0/p if p>EPS else math.inf
  if not math.isfinite(q) or q<=EPS or q>12.0+EPS:return False
  debt=self._scope_debt_qty();reserved=self._reserved_repair_quota(rs);avail=max(0.0,debt-reserved)
  if avail+EPS<q:self.activeStats['NO_RESIDUAL_LEGAL_TRANCHE']+=1;return False
  before=self._physical_floor();after=self._candidate_alone_floor(rs,p,q)
  if after<=before+EPS:self.activeStats['NOT_FLOOR_IMPROVING']+=1;return False
  n=self.n;self.n+=1;native_side,native_price=r1.v2.base.ex.native_order(rs,p)
  try:
   if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),r1.v2.base.ex.hbt.GTC,r1.v2.base.ex.hbt.LIMIT,False))
   else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),r1.v2.base.ex.hbt.GTC,r1.v2.base.ex.hbt.LIMIT,False))
  except Exception:self.activeStats['SUBMIT_EXCEPTION']+=1;return False
  key=f'{rs}_{n}';self.orders[key]={'n':n,'side':rs,'price':p,'qty':q,'cum':0.0,'placed':int(t),'status':'NEW'};self.placeHist.append((int(t),rs,q,p));self.submits+=1
  self.key_role[key]='SATELLITE_REPAIR';self.key_scope_gen[key]=int(self.scopeGeneration);self.role_submits['SATELLITE_REPAIR']+=1
  self.keyRepairQuotaAuthorized[key]=q;self.keyRepairQuotaRemaining[key]=q;self.keyOverflowQtyAuthorized[key]=0.0;self.keyOverflowQtyRemaining[key]=0.0;self.totalRepairQuotaAuthorized+=q
  self.activeKeys.add(key);self.activeMeta[key]={'submitAt':int(t),'fillSeen':0.0};self.activeScopeDone.add(self.scopeGeneration);self.activeStats['SUBMIT']+=1
  self.slot_history.append({'t':int(t),'event':'PARALLEL_ACTIVE_REPAIR_SUBMIT','key':key,'scopeGeneration':self.scopeGeneration,'side':rs,'ask':p,'qty':q,'debt':debt,'passiveReservedBefore':reserved,'submitRc':rc});self._audit_reservation();return True
 def _manage_active(self,t):
  for key in list(self.activeKeys):
   o=self.orders.get(key);a=self.activeMeta.get(key)
   if not o or not a:continue
   cur=float(o.get('cum') or 0.0);old=float(a.get('fillSeen') or 0.0)
   if cur>old+EPS:self.activeFillQty+=cur-old;a['fillSeen']=cur;self.activeStats['FILL_EVENT']+=1
   try:s=self.snap(o);live=r1.v2.base.live(s.get('status'))
   except Exception:live=False
   if live and int(t)-int(a['submitAt'])>=ACTIVE_WINDOW_MS and not o.get('cancelRequested'):
    co=self.bt.orders(0).get(o['n'])
    if co is not None and bool(co.cancellable):
     try:self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.activeStats['CANCEL_REMAINDER']+=1
     except Exception:pass
 def process(self,t):super().process(t);self._manage_active(t)
 def _open_one_option(self,t,qv,end):
  super()._open_one_option(t,qv,end);self._submit_parallel_active(t)
 def run_d7(self,winner):
  r=super().run_v88(winner);r['parallelActiveRepair']=dict(self.activeStats);r['parallelActiveRepairFillQty']=float(self.activeFillQty);return r

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d7_parallel_active_'))
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
  for mid in mids:
   cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=r1.QueueAwareRepairRoutingSim(tape,4)
   try:r0=c.run_v88(cr['winner'])
   finally:c.close()
   rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0});s=ParallelActiveRepairResidual(tape,4)
   try:r=s.run_d7(cr['winner'])
   finally:s.close()
   rows.append({'marketId':mid,'cell':'MS4_D7_PARALLEL_ACTIVE_REPAIR_RESIDUAL','winnerPostHocOnly':cr['winner'],**r})
   print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d7Sub':r['submits'],'r1Fill':r0['fillEvents'],'d7Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d7Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d7Floor':r['floor'],'active':r['parallelActiveRepair'],'activeQty':r['parallelActiveRepairFillQty'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
  c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D7_PARALLEL_ACTIVE_REPAIR_RESIDUAL'};cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeFillQty':n[m]['parallelActiveRepairFillQty']} for m in mids]
  out={'version':'MS4_D7_PARALLEL_ACTIVE_REPAIR_RESIDUAL_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['one parallel Active Repair per responsibility scope','requires existing passive Repair carrier','Active qty uses only unreserved authoritative Repair debt; zero Overflow','does not consume 4 passive price slots','500ms active remainder window inherited from prior active mechanic','no new risk credit','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
 finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
