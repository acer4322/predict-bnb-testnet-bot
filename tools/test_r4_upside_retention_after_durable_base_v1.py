from __future__ import annotations
import json,lzma
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/hft_forward_paper_v1/markets'; OUT=ROOT/'data/research/r4_v0/hourly'; TZ=ZoneInfo('Asia/Taipei')
FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}
STRESSES=['NONE','WEAK_DROP_ALTERNATE','WEAK_PARTIAL_HALF_ALTERNATE','MAKER_DROP_EVERY5']
NONINF=0.80

def geom(s):
 up,down,cu,cd=s; paired=min(up,down); cost=cu+cd; gross=up+down; au=cu/up if up>1e-12 else 0.; ad=cd/down if down>1e-12 else 0.
 pu=up-cost; pd=down-cost
 return {'up':up,'down':down,'paired':paired,'cost':cost,'floor':min(pu,pd),'upside':max(pu,pd),'edge':1-(au+ad) if up>1e-12 and down>1e-12 else 0.,'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-12 else 0.}

def apply(s,z,q):
 up,down,cu,cd=s; px=float(z['px']); q=max(0.,float(q))
 if z['side']=='UP': up+=q;cu+=q*px
 else: down+=q;cd+=q*px
 return up,down,cu,cd

def events(d):
 out=[]
 for e in d.get('makerFillEvents') or []:
  q=float(e.get('deltaShares') or 0);px=float(e.get('price') or 0);side=str(e.get('side') or '').upper();t=int(e.get('observedAtMs') or e.get('atMs') or 0)
  if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'}:out.append({'t':t,'role':'MAKER','side':side,'px':px,'sh':q})
 for e in d.get('takerEvents') or []:
  q=float(e.get('shares') or e.get('filledShares') or e.get('deltaShares') or e.get('qty') or 0);px=float(e.get('price') or e.get('fillPrice') or e.get('avgPrice') or 0);side=str(e.get('side') or '').upper();t=int(e.get('observedAtMs') or e.get('atMs') or e.get('fillMs') or 0)
  if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'}:out.append({'t':t,'role':'TAKER','side':side,'px':px,'sh':q})
 return sorted(out,key=lambda x:x['t'])

def stress_stream(ev,stress):
 s=(0.,0.,0.,0.);maker=weakctr=0;out=[]
 for z0 in ev:
  z=dict(z0);pre=geom(s);q=z['sh'];weak='UP' if pre['up']<pre['down'] else 'DOWN' if pre['down']<pre['up'] else None
  if z['role']=='MAKER':
   maker+=1
   if weak and z['side']==weak:
    weakctr+=1
    if stress=='WEAK_DROP_ALTERNATE' and weakctr%2==0:q=0.
    elif stress=='WEAK_PARTIAL_HALF_ALTERNATE' and weakctr%2==0:q*=.5
   if stress=='MAKER_DROP_EVERY5' and maker%5==0:q=0.
  if q>1e-12:
   z['sh']=q;out.append(z);s=apply(s,z,q)
 return out

def safe_qty_zero(s,z,q):
 if geom(apply(s,z,q))['floor']>=0:return q
 lo,hi=0.,q
 for _ in range(60):
  mid=(lo+hi)/2
  if geom(apply(s,z,mid))['floor']>=0:lo=mid
  else:hi=mid
 return lo

def has_break(ev):
 s=(0.,0.,0.,0.)
 for z in ev:
  pre=geom(s);post=geom(apply(s,z,z['sh']));reserve=max(0.,pre['floor']-post['floor'])
  if pre['floor']>0 and reserve>1e-12 and post['floor']<=0 and post['edge']<0:return True
  s=apply(s,z,z['sh'])
 return False

def durable_first_index(times,floors,h=15):
 if len(times)<2:return None
 H=h*1000
 for i,t0 in enumerate(times[:-1]):
  if floors[i]<=0:continue
  deadline=t0+H;j=i;ok=True
  while j<len(times)-1 and times[j+1]<deadline:
   if floors[j]<=0:ok=False;break
   j+=1
  if ok and floors[j]>0 and times[-1]>=deadline:return i
 return None

def replay(ev,overlay=False):
 s=(0.,0.,0.,0.);states=[];times=[];mods=0
 for z in ev:
  pre=geom(s);q=float(z['sh']);postfull=geom(apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor']);flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
  if overlay and flag:
   sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT';rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT');q0=q
   if rel=='SURPLUS_SIDE':q=safe_qty_zero(s,z,q)
   else:q=0.
   mods+=int(q<q0-1e-9)
  s=apply(s,z,q);states.append(geom(s));times.append(int(z['t']))
 floors=[x['floor'] for x in states];di=durable_first_index(times,floors,15)
 if di is None:
  return {**geom(s),'modifiedEvents':mods,'durable15':False,'postPeakUpside':None,'postTerminalUpside':None,'postPeakSurplus':None,'postMeanEdge':None,'postReserveSpend':None,'postRelapse':None}
 post=states[di:]
 spend=0.
 for a,b in zip(post[:-1],post[1:]):
  if b['floor']<a['floor']:spend+=a['floor']-b['floor']
 return {**geom(s),'modifiedEvents':mods,'durable15':True,'postPeakUpside':max(x['upside'] for x in post),'postTerminalUpside':post[-1]['upside'],'postPeakSurplus':max(x['absnet'] for x in post),'postMeanEdge':float(np.mean([x['edge'] for x in post])),'postReserveSpend':spend,'postRelapse':any(x['floor']<=0 for x in post[1:])}

def md(x):return float(median(x)) if x else None

def ratio(a,b):
 if a is None or b is None:return None
 if abs(b)<1e-12:return 1.0 if abs(a)<1e-12 else (999.0 if a>0 else -999.0)
 return a/b

def summarize(records,stress):
 rec=[]
 for mid,ev0 in records:
  ev=stress_stream(ev0,stress)
  if not ev or not has_break(ev):continue
  b=replay(ev,False);o=replay(ev,True)
  if o['modifiedEvents']<=0:continue
  rec.append((mid,b,o))
 both=[x for x in rec if x[1]['durable15'] and x[2]['durable15']]
 bp=md([b['postPeakUpside'] for _,b,o in both]);op=md([o['postPeakUpside'] for _,b,o in both])
 bt=md([b['postTerminalUpside'] for _,b,o in both]);ot=md([o['postTerminalUpside'] for _,b,o in both])
 br=sum(b['postRelapse'] for _,b,o in both)/len(both) if both else None;orr=sum(o['postRelapse'] for _,b,o in both)/len(both) if both else None
 out={'activeHistories':len(rec),'bothDurable15Histories':len(both),'baselineMedianPostPeakUpside':bp,'overlayMedianPostPeakUpside':op,'peakUpsideRetention':ratio(op,bp),'baselineMedianPostTerminalUpside':bt,'overlayMedianPostTerminalUpside':ot,'terminalUpsideRetention':ratio(ot,bt),'baselineMedianPostPeakSurplus':md([b['postPeakSurplus'] for _,b,o in both]),'overlayMedianPostPeakSurplus':md([o['postPeakSurplus'] for _,b,o in both]),'baselineMedianPostMeanEdge':md([b['postMeanEdge'] for _,b,o in both]),'overlayMedianPostMeanEdge':md([o['postMeanEdge'] for _,b,o in both]),'baselineMedianPostReserveSpend':md([b['postReserveSpend'] for _,b,o in both]),'overlayMedianPostReserveSpend':md([o['postReserveSpend'] for _,b,o in both]),'baselineRelapseRate':br,'overlayRelapseRate':orr}
 out['pass']=bool(len(both)>=10 and out['peakUpsideRetention'] is not None and out['peakUpsideRetention']>=NONINF and out['terminalUpsideRetention'] is not None and out['terminalUpsideRetention']>=NONINF and orr is not None and br is not None and orr<=br+1e-12)
 return out

def main():
 records=[];audit=json.loads((OUT/'r4_hft_base_break_support_audit_v1_20260826_171833.json').read_text(encoding='utf-8'))
 for h in audit.get('supportedHistories',[]):
  p=ROOT/str(h['file'])
  try:
   with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  except Exception:continue
  mid=int(d.get('marketId') or 0)
  if mid in FROZEN:continue
  ev=events(d)
  if ev:records.append((mid,ev))
 results={s:summarize(records,s) for s in STRESSES};eligible=[s for s in STRESSES[1:] if results[s]['bothDurable15Histories']>=10]
 keep=bool(results['NONE']['pass'] and len(eligible)>=2 and sum(results[s]['pass'] for s in eligible)>=2)
 status='KEEP_SIGNAL' if keep else 'REJECTED'
 now=datetime.now(TZ);rep={'version':'R4_UPSIDE_RETENTION_AFTER_DURABLE_BASE_V1','createdAt':now.isoformat(),'candidate':'Evaluate whether the frozen relation-aware reserve-capped tranche retains favorable payoff asymmetry after 15s durable-base formation on genuine non-live HFT BASE_BREAK histories. No action semantics changed.','nonInferiorityMargin':NONINF,'results':results,'eligibleStressVariants':eligible,'status':status,'gate':'Normal and >=2 eligible stress variants must retain >=80% median post-durable peak and terminal upside, with no increase in post-durable floor relapse rate. Fixed once; no sweep.','guards':{'special20260816Sealed':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'noEchtgeldTraining':True,'noDreamFill':True,'noThresholdSweep':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True,'futureOutcomeRuntimeInput':False},'note':'HFT is mechanism diagnostic only; PnL is not a gate.'}
 path=OUT/f"r4_upside_retention_after_durable_base_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'results':results},ensure_ascii=False))
if __name__=='__main__':main()
