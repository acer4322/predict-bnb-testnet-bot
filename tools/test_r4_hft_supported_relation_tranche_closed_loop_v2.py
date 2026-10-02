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

def geom(s):
 up,down,cu,cd=s; paired=min(up,down); cost=cu+cd; gross=up+down; au=cu/up if up>1e-12 else 0.; ad=cd/down if down>1e-12 else 0.
 return {'up':up,'down':down,'paired':paired,'cost':cost,'floor':paired-cost,'edge':1-(au+ad) if up>1e-12 and down>1e-12 else 0.,'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-12 else 0.}

def apply(s,z,q):
 up,down,cu,cd=s; px=z['px']
 if z['side']=='UP': up+=q; cu+=q*px
 else: down+=q; cd+=q*px
 return (up,down,cu,cd)

def events(d):
 out=[]
 for e in d.get('makerFillEvents') or []:
  q=float(e.get('deltaShares') or 0); px=float(e.get('price') or 0); side=str(e.get('side') or '').upper(); t=int(e.get('observedAtMs') or e.get('atMs') or 0)
  if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'}:out.append({'t':t,'role':'MAKER','side':side,'px':px,'sh':q})
 for e in d.get('takerEvents') or []:
  q=float(e.get('shares') or e.get('filledShares') or e.get('deltaShares') or e.get('qty') or 0); px=float(e.get('price') or e.get('fillPrice') or e.get('avgPrice') or 0); side=str(e.get('side') or '').upper(); t=int(e.get('observedAtMs') or e.get('atMs') or e.get('fillMs') or 0)
  if q>0 and 0<=px<=1.05 and side in {'UP','DOWN'}:out.append({'t':t,'role':'TAKER','side':side,'px':px,'sh':q})
 return sorted(out,key=lambda x:x['t'])

def safe_qty_zero(s,z,q):
 if geom(apply(s,z,q))['floor']>=0:return q
 lo,hi=0.,q
 for _ in range(60):
  mid=(lo+hi)/2
  if geom(apply(s,z,mid))['floor']>=0:lo=mid
  else:hi=mid
 return lo

def has_base_break(ev):
 s=(0.,0.,0.,0.)
 for z in ev:
  pre=geom(s); post=geom(apply(s,z,z['sh'])); reserve=max(0.,pre['floor']-post['floor'])
  if pre['floor']>0 and reserve>1e-12 and post['floor']<=0 and post['edge']<0:return True
  s=apply(s,z,z['sh'])
 return False

def replay(ev,policy):
 s=(0.,0.,0.,0.); floors=[];times=[];peak=-1e99;maxdd=0.;spent=0.;firstpos=False;mods=tr=cross=flat=flags=0;supp=0.
 first_pos_t=None
 for z in ev:
  pre=geom(s); q=z['sh']; postfull=geom(apply(s,z,q)); reserve=max(0.,pre['floor']-postfull['floor']); flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
  if flag:
   flags+=1; surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'; relation='SURPLUS_SIDE' if z['side']==surplus else ('WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT')
   q0=q
   if policy=='HARD':q=0.
   elif policy=='REL':
    if relation=='SURPLUS_SIDE':q=safe_qty_zero(s,z,q); tr+=int(q<q0-1e-9)
    else:
     q=0.; cross+=int(relation=='WEAK_SIDE_CROSS'); flat+=int(relation=='FLAT')
   if q<q0-1e-9:mods+=1;supp+=q0-q
  s=apply(s,z,q); cur=geom(s)
  if firstpos and cur['floor']<pre['floor']:spent+=pre['floor']-cur['floor']
  if cur['floor']>0:
   if not firstpos:first_pos_t=z['t']
   firstpos=True
  peak=max(peak,cur['floor']);maxdd=max(maxdd,peak-cur['floor']);floors.append(cur['floor']);times.append(z['t'])
 duration=0.
 for i in range(len(floors)-1):
  if floors[i]>0:duration+=(times[i+1]-times[i])/1000
 return {**geom(s),'positiveDurationSec':duration,'maxFloorDrawdown':maxdd,'reserveSpentAfterBase':spent,'modifiedEvents':mods,'flags':flags,'tranches':tr,'crossHard':cross,'flatHard':flat,'suppressedShares':supp,'everPositiveFloor':firstpos,'firstPositiveFloorMs':first_pos_t}

def md(xs):return float(median(xs)) if xs else None

def q90(xs):return float(np.quantile(np.asarray(xs,float),.9)) if xs else None

def main():
 rec=[]; scanned=0
 for p in sorted(SRC.glob('*.json.xz')):
  scanned+=1
  try:
   with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  except Exception:continue
  mid=int(d.get('marketId') or 0)
  if mid in FROZEN:continue
  ev=events(d)
  if not ev or not has_base_break(ev):continue
  b=replay(ev,'BASE');h=replay(ev,'HARD');r=replay(ev,'REL');rec.append((mid,d.get('student'),b,h,r))
 def delta(ix,k):return [x[ix][k]-x[2][k] for x in rec]
 out={'activeHistories':len(rec),'baselineEverPositive':sum(x[2]['everPositiveFloor'] for x in rec),'hardEverPositive':sum(x[3]['everPositiveFloor'] for x in rec),'relationEverPositive':sum(x[4]['everPositiveFloor'] for x in rec),
 'hardMedianDeltaFinalFloor':md(delta(3,'floor')),'relationMedianDeltaFinalFloor':md(delta(4,'floor')),
 'hardMedianDeltaPositiveDurationSec':md(delta(3,'positiveDurationSec')),'relationMedianDeltaPositiveDurationSec':md(delta(4,'positiveDurationSec')),
 'hardMedianDeltaMaxFloorDrawdown':md(delta(3,'maxFloorDrawdown')),'relationMedianDeltaMaxFloorDrawdown':md(delta(4,'maxFloorDrawdown')),
 'hardMedianDeltaReserveSpentAfterBase':md(delta(3,'reserveSpentAfterBase')),'relationMedianDeltaReserveSpentAfterBase':md(delta(4,'reserveSpentAfterBase')),
 'hardMedianDeltaCoverage':md(delta(3,'coverage')),'relationMedianDeltaCoverage':md(delta(4,'coverage')),
 'hardMedianDeltaAbsNet':md(delta(3,'absnet')),'relationMedianDeltaAbsNet':md(delta(4,'absnet')),
 'baselineAbsNetP90':q90([x[2]['absnet'] for x in rec]),'hardAbsNetP90':q90([x[3]['absnet'] for x in rec]),'relationAbsNetP90':q90([x[4]['absnet'] for x in rec]),
 'relationModifiedEvents':sum(x[4]['modifiedEvents'] for x in rec),'relationTrancheEvents':sum(x[4]['tranches'] for x in rec),'relationCrossHardEvents':sum(x[4]['crossHard'] for x in rec),'relationFlatHardEvents':sum(x[4]['flatHard'] for x in rec),'relationSuppressedShares':sum(x[4]['suppressedShares'] for x in rec),
 'floorImprovedRelation':sum(x[4]['floor']>x[2]['floor']+1e-9 for x in rec),'floorWorsenedRelation':sum(x[4]['floor']<x[2]['floor']-1e-9 for x in rec)}
 tail_ok=out['relationAbsNetP90']<=out['baselineAbsNetP90']*1.10+1e-9 if out['baselineAbsNetP90'] is not None else True
 out['qualityGatePass']=bool(len(rec)>=20 and out['relationMedianDeltaFinalFloor'] is not None and out['relationMedianDeltaFinalFloor']>0 and out['relationMedianDeltaPositiveDurationSec'] is not None and out['relationMedianDeltaPositiveDurationSec']>=0 and out['relationMedianDeltaMaxFloorDrawdown'] is not None and out['relationMedianDeltaMaxFloorDrawdown']<=1e-9 and out['relationMedianDeltaReserveSpentAfterBase'] is not None and out['relationMedianDeltaReserveSpentAfterBase']<=1e-9 and tail_ok)
 now=datetime.now(TZ);report={'version':'R4_HFT_SUPPORTED_RELATION_TRANCHE_CLOSED_LOOP_V2','createdAt':now.isoformat(),'candidate':'Frozen relation-aware reserve-capped tranche evaluated only on non-live HFT closed-loop histories with genuine realized positive-floor -> BASE_BREAK support. Future MPQ relation and sizing are recomputed from overlay-modified inventory.','guards':{'noDreamFill':True,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'noThresholdSweep':True,'floorBoundary':0.0,'winnerUnused':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True},'source':{'filesScanned':scanned,'supportHistories':len(rec)},'results':out,'gate':'High-quality-base preservation: >=20 supported histories, positive median final-floor and duration effects, non-increasing median max floor drawdown/reserve spend, abs-net P90 <=110% baseline.'}
 path=OUT/f"r4_hft_supported_relation_tranche_closed_loop_v2_{now.strftime('%Y%m%d_%H%M%S')}.json";path.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(path.relative_to(ROOT)).replace('\\','/'),'results':out},ensure_ascii=False))
if __name__=='__main__':main()
