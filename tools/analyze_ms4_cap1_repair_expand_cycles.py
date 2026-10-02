from __future__ import annotations
import json,sys,statistics
from pathlib import Path

def main():
 p=Path(sys.argv[1]);d=json.load(open(p));cell=sys.argv[2] if len(sys.argv)>2 else 'MS4_R28_FANOUT_CAP1'; rows=[r for r in d['rows'] if r.get('cell')==cell]; out=[]
 for r in rows:
  mid=r['marketId']; hist=sorted(r.get('slotHistory') or [],key=lambda e:int(e.get('t',0))); last_expand={}; repairs={}
  active_keys={e.get('key') for e in hist if e.get('event') in {'FAILURE_EVIDENCE_ACTIVE_DRAIN_SUBMIT','MS4_R2_ACTIVE_REPAIR_SUBMIT'} and e.get('key')}
  for e in hist:
   if e.get('event')!='ROLE_FILL_SPLIT':continue
   gen=int(e.get('generationAtSubmit') or -1); role=e.get('role'); side=e.get('side'); q=float(e.get('fillInc') or 0); price=float(e.get('price') or 0); rq=float(e.get('repairAllocated') or 0); key=e.get('key'); t=int(e.get('t',0))
   if role in {'ECONOMIC_CORE','SATELLITE_REPAIR'} and rq>1e-9:
    repair_side=side; surplus_side='DOWN' if repair_side=='UP' else 'UP'; repairs.setdefault((gen,surplus_side),[]).append((t,rq,price,key,'ACTIVE_REPAIR' if key in active_keys else role))
   if role=='SATELLITE_EXPAND' and q>1e-9:
    k=(gen,side); prev=int(last_expand.get(k,-1)); rr=[x for x in repairs.get(k,[]) if x[0]>prev and x[0]<=t]; last_expand[k]=t
    total=sum(x[1] for x in rr); matched=min(total,q)
    if total>1e-9 and matched>1e-9:
     # FIFO weighted repair price over only matched qty
     rem=matched; notional=0; used=[]
     for x in rr:
      take=min(rem,x[1]);
      if take>0:notional+=take*x[2];used.append((x[0],take,x[2],x[3],x[4]));rem-=take
      if rem<=1e-9:break
     rp=notional/matched; ps=rp+price; gain=matched*(1-ps)
     out.append({'marketId':mid,'generation':gen,'side':side,'expandT':t,'expandPrice':price,'expandQty':q,'repairQtySincePriorExpand':total,'matchedQty':matched,'weightedRepairPrice':rp,'pairSum':ps,'matchedFloorGain':gain,'repairFills':used,'pnl':r.get('pnlDiagnosticOnly')})
 agg={'segments':len(out),'favorableLt1':sum(x['pairSum']<1-1e-9 for x in out),'nonDamagingLe1':sum(x['pairSum']<=1+1e-9 for x in out),'meanPairSum':statistics.mean([x['pairSum'] for x in out]) if out else None,'medianPairSum':statistics.median([x['pairSum'] for x in out]) if out else None,'totalMatchedFloorGain':sum(x['matchedFloorGain'] for x in out)}
 res={'version':'MS4_CAP1_REPAIR_EXPAND_MARGINAL_CYCLE_AUDIT_V1','cell':cell,'aggregate':agg,'segments':out}; op=Path('data/research/r4_v0/p0_provenance_v1/MS4_CAP1_REPAIR_EXPAND_MARGINAL_CYCLE_AUDIT_V1_20260906.json');op.write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps(agg,indent=2));
 for x in out:print(x['marketId'],round(x['pairSum'],4),round(x['matchedFloorGain'],4),'q',round(x['matchedQty'],3),'pnl',round(float(x['pnl']),3))
if __name__=='__main__':main()
