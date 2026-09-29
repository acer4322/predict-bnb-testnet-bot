from __future__ import annotations
import importlib.util,sys,json,lzma
from pathlib import Path
from statistics import median
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'tools'/'test_r4_hft_supported_relation_tranche_closed_loop_v2.py'
spec=importlib.util.spec_from_file_location('r4base',BASE);b=importlib.util.module_from_spec(spec);assert spec and spec.loader;sys.modules[spec.name]=b;spec.loader.exec_module(b)
SRC=b.SRC;OUT=b.OUT;TZ=b.TZ;FROZEN=b.FROZEN

def replay(ev,policy):
 s=(0.,0.,0.,0.);floors=[];times=[];peak=-1e99;maxdd=0.;spent=0.;firstpos=False;mods=tr=cross=flags=0;supp=0.
 for z in ev:
  pre=b.geom(s);q=z['sh'];postfull=b.geom(b.apply(s,z,q));reserve=max(0.,pre['floor']-postfull['floor']);flag=pre['floor']>0 and reserve>1e-12 and postfull['floor']<=0 and postfull['edge']<0
  if flag:
   flags+=1;surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT';relation='SURPLUS_SIDE' if z['side']==surplus else ('WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT');q0=q
   if policy=='HARD':q=0.
   elif policy=='FLAT_TRANCHE':
    if relation=='WEAK_SIDE_CROSS':q=0.;cross+=1
    else:q=b.safe_qty_zero(s,z,q);tr+=int(q<q0-1e-9)
   if q<q0-1e-9:mods+=1;supp+=q0-q
  s=b.apply(s,z,q);cur=b.geom(s)
  if firstpos and cur['floor']<pre['floor']:spent+=pre['floor']-cur['floor']
  if cur['floor']>0:firstpos=True
  peak=max(peak,cur['floor']);maxdd=max(maxdd,peak-cur['floor']);floors.append(cur['floor']);times.append(z['t'])
 duration=0.
 for i in range(len(floors)-1):
  if floors[i]>0:duration+=(times[i+1]-times[i])/1000
 return {**b.geom(s),'positiveDurationSec':duration,'maxFloorDrawdown':maxdd,'reserveSpentAfterBase':spent,'modifiedEvents':mods,'flags':flags,'tranches':tr,'crossHard':cross,'suppressedShares':supp,'everPositiveFloor':firstpos}

def md(xs):return float(median(xs)) if xs else None
def q90(xs):return float(np.quantile(np.asarray(xs,float),.9)) if xs else None

def main():
 rec=[];scanned=0
 for p in sorted(SRC.glob('*.json.xz')):
  scanned+=1
  try:
   with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
  except Exception:continue
  mid=int(d.get('marketId') or 0)
  if mid in FROZEN:continue
  ev=b.events(d)
  if not ev or not b.has_base_break(ev):continue
  base=replay(ev,'BASE');hard=replay(ev,'HARD');cand=replay(ev,'FLAT_TRANCHE');rec.append((mid,base,hard,cand))
 def delta(ix,k):return [x[ix][k]-x[1][k] for x in rec]
 out={'activeHistories':len(rec),'candidate':'WEAK_SIDE_CROSS hard-protected; SURPLUS_SIDE and FLAT MPQ BASE_BREAK fills capped only at exact post-floor=0 boundary.',
 'hardMedianDeltaFinalFloor':md(delta(2,'floor')),'candidateMedianDeltaFinalFloor':md(delta(3,'floor')),
 'hardMedianDeltaPositiveDurationSec':md(delta(2,'positiveDurationSec')),'candidateMedianDeltaPositiveDurationSec':md(delta(3,'positiveDurationSec')),
 'hardMedianDeltaMaxFloorDrawdown':md(delta(2,'maxFloorDrawdown')),'candidateMedianDeltaMaxFloorDrawdown':md(delta(3,'maxFloorDrawdown')),
 'hardMedianDeltaReserveSpentAfterBase':md(delta(2,'reserveSpentAfterBase')),'candidateMedianDeltaReserveSpentAfterBase':md(delta(3,'reserveSpentAfterBase')),
 'hardMedianDeltaCoverage':md(delta(2,'coverage')),'candidateMedianDeltaCoverage':md(delta(3,'coverage')),
 'hardMedianDeltaAbsNet':md(delta(2,'absnet')),'candidateMedianDeltaAbsNet':md(delta(3,'absnet')),
 'baselineAbsNetP90':q90([x[1]['absnet'] for x in rec]),'hardAbsNetP90':q90([x[2]['absnet'] for x in rec]),'candidateAbsNetP90':q90([x[3]['absnet'] for x in rec]),
 'modifiedEvents':sum(x[3]['modifiedEvents'] for x in rec),'trancheEvents':sum(x[3]['tranches'] for x in rec),'crossHardEvents':sum(x[3]['crossHard'] for x in rec),'suppressedShares':sum(x[3]['suppressedShares'] for x in rec),
 'floorImproved':sum(x[3]['floor']>x[1]['floor']+1e-9 for x in rec),'floorWorsened':sum(x[3]['floor']<x[1]['floor']-1e-9 for x in rec)}
 tail_ok=out['candidateAbsNetP90']<=out['baselineAbsNetP90']*1.10+1e-9
 out['qualityGatePass']=bool(len(rec)>=20 and out['candidateMedianDeltaFinalFloor']>0 and out['candidateMedianDeltaPositiveDurationSec']>=0 and out['candidateMedianDeltaMaxFloorDrawdown']<=1e-9 and out['candidateMedianDeltaReserveSpentAfterBase']<=1e-9 and tail_ok)
 now=datetime.now(TZ);report={'version':'R4_HFT_FLAT_TRANCHE_CLOSED_LOOP_V1','createdAt':now.isoformat(),'candidate':out['candidate'],'results':out,'gate':'Same high-quality-base gate as supported closed-loop V2; no tuning.','guards':{'noDreamFill':True,'noThresholdSweep':True,'floorBoundary':0.0,'excludedFrozenEchtgeldMarkets':sorted(FROZEN),'winnerUnused':True,'researchOnly':True,'noLiveR3Change':True,'no8781Change':True}}
 p=OUT/f"r4_hft_flat_tranche_closed_loop_v1_{now.strftime('%Y%m%d_%H%M%S')}.json";p.write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'results':out},ensure_ascii=False))
if __name__=='__main__':main()
