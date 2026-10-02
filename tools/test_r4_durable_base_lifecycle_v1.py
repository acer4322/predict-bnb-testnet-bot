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

def geom(s):
 up,down,cu,cd=s; paired=min(up,down); cost=cu+cd; gross=up+down; au=cu/up if up>1e-12 else 0.; ad=cd/down if down>1e-12 else 0.
 return {'up':up,'down':down,'paired':paired,'cost':cost,'floor':paired-cost,'edge':1-(au+ad) if up>1e-12 and down>1e-12 else 0.,'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-12 else 0.}

def apply(s,z,q):
 up,down,cu,cd=s; px=float(z['px']); q=max(0.,float(q))
 if z['side']=='UP':up+=q;cu+=q*px
 else:down+=q;cd+=q*px
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

def durable_first(times,floors,h):
 if len(times)<2:return None
 H=h*1000
 for i,t0 in enumerate(times[:-1]):
  if floors[i]<=0:continue
  deadline=t0+H;j=i;ok=True
  while j<len(times)-1 and times[j+1]<deadline:
   if floors[j]<=0:ok=False;break
   j+=1
  if ok and floors[j]>0 and times[-1]>=deadline:return t0
 return None

def replay(ev,overlay=False):
 s=(0.,0.,0.,0.);floors=[];times=[];peak=-1e99;maxdd=0.;spent=0.;base_seen=False;mods=flags=tranches=crosshard=flathard=0
 for z in ev:
  pre=geom(s);q=float(z['sh']);postfull=geom(apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor']);flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
  if overlay and flag:
   flags+=1;sur='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT';rel='SURPLUS_SIDE' if z['side']==sur else ('WEAK_SIDE_CROSS' if sur!='FLAT' else 'FLAT');q0=q
   if rel=='SURPLUS_SIDE':q=safe_qty_zero(s,z,q);tranches+=int(q<q0-1e-9)
   else:
    q=0.;crosshard+=int(rel=='WEAK_SIDE_CROSS');flathard+=int(rel=='FLAT')
   mods+=int(q<q0-1e-9)
  s=apply(s,z,q);cur=geom(s)
  if base_seen and cur['floor']<pre['floor']:spent+=pre['floor']-cur['floor']
  if cur['floor']>0:base_seen=True
  peak=max(peak,cur['floor']);maxdd=max(maxdd,peak-cur['floor']);times.append(int(z['t']));floors.append(cur['floor'])
 posdur=sum((times[i+1]-times[i])/1000 for i in range(len(times)-1) if floors[i]>0)
 first=times[0] if times else 0
 d={h:durable_first(times,floors,h) for h in (5,15,30)}
 return {**geom(s),'positiveDurationSec':posdur,'maxFloorDrawdown':maxdd,'reserveSpentAfterBase':spent,'modifiedEvents':mods,'flags':flags,'tranches':tranches,'crossHard':crosshard,'flatHard':flathard,
         'durable5':d[5] is not None,'durable15':d[15] is not None,'durable30':d[30] is not None,
         'timeToDurable5Sec':((d[5]-first)/1000 if d[5] is not None else None),'timeToDurable15Sec':((d[15]-first)/1000 if d[15] is not None else None),'timeToDurable30Sec':((d[30]-first)/1000 if d[30] is not None else None)}

def md(x):return float(median(x)) if x else None

def q90(x):return float(np.quantile(np.asarray(x,float),.9)) if x else None

def summarize(records,stress):
 rec=[]
 for mid,ev0 in records:
  ev=stress_stream(ev0,stress)
  if not ev or not has_break(ev):continue
  b=replay(ev,False);o=replay(ev,True)
  if o['modifiedEvents']<=0:continue
  rec.append((mid,b,o))
 def delta(k):return [o[k]-b[k] for _,b,o in rec]
 out={'activeHistories':len(rec),'modifiedEvents':sum(o['modifiedEvents'] for _,_,o in rec),'trancheEvents':sum(o['tranches'] for _,_,o in rec),'crossHardEvents':sum(o['crossHard'] for _,_,o in rec),
      'medianDeltaFinalFloor':md(delta('floor')),'medianDeltaPositiveDurationSec':md(delta('positiveDurationSec')),'medianDeltaMaxFloorDrawdown':md(delta('maxFloorDrawdown')),'medianDeltaReserveSpentAfterBase':md(delta('reserveSpentAfterBase')),'medianDeltaCoverage':md(delta('coverage')),'medianDeltaAbsNet':md(delta('absnet')),
      'baselineDurable5Rate':sum(b['durable5'] for _,b,_ in rec)/len(rec) if rec else None,'overlayDurable5Rate':sum(o['durable5'] for _,_,o in rec)/len(rec) if rec else None,
      'baselineDurable15Rate':sum(b['durable15'] for _,b,_ in rec)/len(rec) if rec else None,'overlayDurable15Rate':sum(o['durable15'] for _,_,o in rec)/len(rec) if rec else None,
      'baselineDurable30Rate':sum(b['durable30'] for _,b,_ in rec)/len(rec) if rec else None,'overlayDurable30Rate':sum(o['durable30'] for _,_,o in rec)/len(rec) if rec else None,
      'medianTimeToDurable15BaselineSec':md([b['timeToDurable15Sec'] for _,b,_ in rec if b['timeToDurable15Sec'] is not None]),'medianTimeToDurable15OverlaySec':md([o['timeToDurable15Sec'] for _,_,o in rec if o['timeToDurable15Sec'] is not None]),
      'baselineAbsNetP90':q90([b['absnet'] for _,b,_ in rec]),'overlayAbsNetP90':q90([o['absnet'] for _,_,o in rec])}
 if rec:
  out['deltaDurable5Rate']=out['overlayDurable5Rate']-out['baselineDurable5Rate'];out['deltaDurable15Rate']=out['overlayDurable15Rate']-out['baselineDurable15Rate'];out['deltaDurable30Rate']=out['overlayDurable30Rate']-out['baselineDurable30Rate']
 else:out['deltaDurable5Rate']=out['deltaDurable15Rate']=out['deltaDurable30Rate']=None
 out['pass']=bool(len(rec)>=10 and out['medianDeltaFinalFloor'] is not None and out['medianDeltaFinalFloor']>0 and out['medianDeltaPositiveDurationSec'] is not None and out['medianDeltaPositiveDurationSec']>=0 and out['medianDeltaMaxFloorDrawdown'] is not None and out['medianDeltaMaxFloorDrawdown']<=1e-9 and out['medianDeltaReserveSpentAfterBase'] is not None and out['medianDeltaReserveSpentAfterBase']<=1e-9 and out['deltaDurable15Rate'] is not None and out['deltaDurable15Rate']>=-1e-12 and out['deltaDurable30Rate'] is not None and out['deltaDurable30Rate']>=-1e-12)
 return out

def main():
 records=[];scanned=0
 audit=json.loads((OUT/'r4_hft_base_break_support_audit_v1_20260826_171833.json').read_text(encoding='utf-8'))
 for h in audit.get('supportedHistories',[]):
  p=ROOT/str(h['file']);scanned+=1
  try:
   with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  except Exception:continue
  mid=int(d.get('marketId') or 0)
  if mid in FROZEN:continue
  ev=events(d)
  if ev:records.append((mid,ev))
 results={s:summarize(records,s) for s in STRESSES}
 eligible=[s for s in STRESSES[1:] if results[s]['activeHistories']>=10]
 keep=bool(results['NONE']['activeHistories']>=20 and results['NONE']['pass'] and len(eligible)>=2 and sum(results[s]['pass'] for s in eligible)>=2)
 status='KEEP_SIGNAL' if keep else 'REJECTED'
 now=datetime.now(TZ);rep={'version':'R4_DURABLE_BASE_LIFECYCLE_V1','createdAt':now.isoformat(),'candidate':'Evaluate the frozen relation-aware reserve-capped tranche as a durable safe-base lifecycle control on genuine non-live HFT BASE_BREAK streams. No tranche semantics or thresholds changed. Durable base means floor remains continuously >0 for fixed 5/15/30s horizons.','source':{'filesScanned':scanned,'nonLiveRecords':len(records)},'results':results,'eligibleStressVariants':eligible,'status':status,'gate':'Normal >=20 supported histories and pass; >=2 stress variants with >=10 histories must pass. Pass requires positive median final-floor lift, nonnegative positive-floor-duration lift, non-increasing median drawdown/reserve spend, and no decrease in 15s/30s durable-base rate. Abs-net remains diagnostic only.','guards':{'strictPastControlState':True,'futureOutcomeRuntimeInput':False,'winnerUnused':True,'special20260816Sealed':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'noEchtgeldTraining':True,'noDreamFill':True,'noThresholdSweep':True,'floorBoundary':0.0,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True},'note':'HFT is mechanism diagnostic only; KEEP_SIGNAL does not imply live authority.'}
 path=OUT/f"r4_durable_base_lifecycle_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'status':status,'results':results},ensure_ascii=False))
if __name__=='__main__':main()
