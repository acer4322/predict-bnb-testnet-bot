from __future__ import annotations
import argparse,json,tempfile,zipfile,shutil,sys,threading,time,importlib.util,joblib
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
def sib(name,file):
 p=Path(__file__).with_name(file);s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m);return m
v52=sib('eth_v52_for_v53','run_eth_repair_v52_generation_payment_epoch_multi_active.py');v38=v52.v38;EPS=1e-9

class V53MultiCycleAudit(v52.V52PaymentEpochMultiActive):
 def __init__(self,*a,**kw):
  super().__init__(*a,**kw);self.v53Seen={};self.v53Fills=[]
 def _refresh_carrier_ledger(self,t):
  out=super()._refresh_carrier_ledger(t)
  if not hasattr(self,'v53Seen'):return out
  for k,e in getattr(self,'carrierLedger',{}).items():
   cur=float(e.get('actualFilled') or 0.0);old=float(self.v53Seen.get(k,0.0))
   if cur>old+EPS:
    inc=cur-old;o=getattr(self,'orders',{}).get(k,{})
    obj=str(e.get('objectiveRole') or o.get('objective_role') or '')
    lane=str(e.get('lane') or o.get('execution_role') or '')
    ex=str(o.get('execution_role') or lane)
    taker=('TAKER' in ex.upper()) or str(lane).startswith('ACTIVE_')
    if obj=='REPAIR':role='ACTIVE_REPAIR' if taker else 'PASSIVE_REPAIR'
    elif obj=='EXPAND':role='ACTIVE_EXPAND' if taker else 'PASSIVE_EXPAND'
    else:role='ACTIVE_OTHER' if taker else 'PASSIVE_BASE_MM'
    self.v53Fills.append({'t':int(t),'key':k,'role':role,'objectiveRole':obj,'executionRole':ex,'lane':lane,'side':e.get('side') or o.get('side'),'price':float(o.get('price') or 0.0),'qty':inc,'parentId':e.get('parentId')})
   self.v53Seen[k]=cur
  return out
 @staticmethod
 def _cycle_stats(events):
  toks=[x['role'] for x in events]
  compressed=[]
  for x in toks:
   if not compressed or compressed[-1]!=x:compressed.append(x)
  anyrep={'PASSIVE_REPAIR','ACTIVE_REPAIR'}
  expands={'PASSIVE_EXPAND','ACTIVE_EXPAND'}
  rer=0;strict=0;expand_repaired=0
  expidx=[i for i,x in enumerate(toks) if x in expands]
  for j,i in enumerate(expidx):
   lo=(expidx[j-1]+1) if j else 0;hi=expidx[j+1] if j+1<len(expidx) else len(toks)
   pre=toks[lo:i];post=toks[i+1:hi]
   if any(x in anyrep for x in pre) and any(x in anyrep for x in post):rer+=1
   if 'PASSIVE_REPAIR' in pre and 'ACTIVE_REPAIR' in post:strict+=1
   if any(x in anyrep for x in post):expand_repaired+=1
  return {'compressedSequence':compressed,'repairExpandRepairRounds':rer,'passiveRepairExpandActiveRepairRounds':strict,'expandThenRepairRounds':expand_repaired}
 def run_exam_v53(self,models,winner):
  r=super().run_exam_v52(models,winner);ev=sorted(self.v53Fills,key=lambda x:(x['t'],x['key']))
  counts={};qty={}
  for x in ev:counts[x['role']]=counts.get(x['role'],0)+1;qty[x['role']]=qty.get(x['role'],0.0)+float(x['qty'])
  cs=self._cycle_stats(ev)
  r.update({'v53FillEvents':ev[:400],'v53RoleCounts':counts,'v53RoleQty':qty,**cs,'v53MakerFillEvents':sum(v for k,v in counts.items() if k.startswith('PASSIVE_')),'v53ActiveFillEvents':sum(v for k,v in counts.items() if k.startswith('ACTIVE_'))})
  return r

def main():
 ap=argparse.ArgumentParser()
 for n in ['bundle','lifecycle-model','capability-model','dagger-cache','timing-model','economic-model','price-model','surplus-model','v44-model','v47-model','market-ids']:ap.add_argument('--'+n,required=True)
 ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='eth_v53_'));stop=threading.Event()
 def hb():
  while not stop.wait(10):print(json.dumps({'heartbeat':'V53','ts':time.time()}),flush=True)
 threading.Thread(target=hb,daemon=True).start();print(json.dumps({'heartbeat':'V53_START'}),flush=True)
 try:
  zipfile.ZipFile(a.bundle).extractall(tmp);co=json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows'];by={int(r['marketId']):r for r in co};models,life,cap,tim,econ,price,sur=v38.v36.v34.v30.load_runtime(a);t44=joblib.load(a.v44_model)['models']['EVENT_VALUE_NORM'];t47=joblib.load(a.v47_model)['models']['GENERATION_AWARE_NORM'];rows=[]
  for mid in [int(x) for x in a.market_ids.split(',') if x.strip()]:
   cr=by[mid];sim=V53MultiCycleAudit(tmp/'tapes'/f'{mid}.json.xz','BOOK_IMBALANCE',models,life,0,0,capability=cap,timing=tim,economic=econ,price_envelope=price,surplus_value=sur,teacher=t44,genTeacher=t47)
   try:r=sim.run_exam_v53(models,cr['winner'])
   finally:sim.close()
   rows.append({'marketId':mid,'winner':cr['winner'],'windowEndMs':cr.get('windowEndMs'),'targetPnlScoringOnly':cr.get('targetPnlScoringOnly'),'functional':r})
   print(json.dumps({'marketId':mid,'pnl':r['pnlDiagnosticOnly'],'roles':r['v53RoleCounts'],'rounds':r['repairExpandRepairRounds'],'strictRounds':r['passiveRepairExpandActiveRepairRounds'],'seq':r['compressedSequence'][:18]},ensure_ascii=False),flush=True)
  roles={};qty={}
  for x in rows:
   for k,v in x['functional']['v53RoleCounts'].items():roles[k]=roles.get(k,0)+v
   for k,v in x['functional']['v53RoleQty'].items():qty[k]=qty.get(k,0.0)+v
  pnls=[float(x['functional']['pnlDiagnosticOnly']) for x in rows];target=[float(x.get('targetPnlScoringOnly') or 0.0) for x in rows]
  agg={'markets':len(rows),'roleCounts':roles,'roleQty':qty,'repairExpandRepairRounds':sum(x['functional']['repairExpandRepairRounds'] for x in rows),'strictFullRounds':sum(x['functional']['passiveRepairExpandActiveRepairRounds'] for x in rows),'marketsWith2PlusRounds':sum(x['functional']['repairExpandRepairRounds']>=2 for x in rows),'marketsWith2PlusStrictRounds':sum(x['functional']['passiveRepairExpandActiveRepairRounds']>=2 for x in rows),'ourPnlSum':sum(pnls),'ourWins':sum(p>0 for p in pnls),'targetPnlScoringSum':sum(target)}
  out={'version':'ETH_REPAIR_V53_MULTICYCLE_AUDIT','researchOnly':True,'behaviorChange':False,'aggregate':agg,'rows':rows,'boundary':['V52 behavior unchanged; instrumentation only','actual HFT fills only','role based on objective/execution carrier truth','no dream fill','no tuning','no 8781']};Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':agg},ensure_ascii=False),flush=True)
 finally:stop.set();shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
