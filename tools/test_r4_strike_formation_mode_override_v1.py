from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_v1 as roll
OUT=ROOT/'data/research/r4_v0/hourly'; PRE=OUT/'r4_rolling_gap_owner_fresh24_preregistered.json'
CFGS=('ROLL_KEEP_GAP_OWNER','ROLL_KEEP_GAP_OWNER_STRIKE_MOD_WEAK_OVERRIDE','ROLL_KEEP_GAP_OWNER_STRIKE_MOD_WEAK_REVERSE')
def load_market(mid):
 ps=sorted((ROOT/'data/hft_forward_paper_v1/markets').glob(f'{mid}_*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
 for p in ps:
  try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
  except:continue
  if int(d.get('marketId') or 0)==int(mid) and 'R2_RESIDUAL' in str(d.get('student') or '') and d.get('orderMeta'):return d
 raise RuntimeError(f'no market {mid}')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--offset',type=int,default=0);ap.add_argument('--count',type=int,default=8);ap.add_argument('--pre',type=Path,default=PRE);a=ap.parse_args();ids=json.load(open(a.pre,encoding='utf-8'))['marketIds'][a.offset:a.offset+a.count];rows=[];errs=[]
 for mid in ids:
  d=load_market(mid)
  for cfg in CFGS:
   try:r=roll.simulate(d,cfg);r['config']=cfg;rows.append(r)
   except Exception as e:errs.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
  print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
 ag={c:roll.agg([r for r in rows if r['config']==c]) for c in CFGS}
 by={}
 for mid in ids:
  by[str(mid)]={}
  for c in CFGS:
   r=next((x for x in rows if x['marketId']==mid and x['config']==c),None)
   if r:by[str(mid)][c]={'durable':r['durableBase'],'everSafe':r['everSafe'],'floor':r['final']['floor'],'absNet':r['final']['absNet'],'early':r['earlyMakerFillShares'],'surplus':r['earlySurplusFillShares'],'overrideUsed':r['counts'].get('strikeModeOverrideUsed',0),'overrideEligible':r['counts'].get('strikeModeOverrideEligible',0)}
 rep={'version':'R4_STRIKE_FORMATION_MODE_OVERRIDE_V1','researchOnly':True,'cohort':'same frozen fresh24','offset':a.offset,'ids':ids,'semantics':{'baseline':'GAP_OWNER unchanged','MOD_WEAK_OVERRIDE':'Only when baseline pMaker side support says NO, candidate side is currently WEAK, Predict edge is fixed 0.10-0.30, and strict-past spot-vs-strike supports current DOMINANT side, treat pMaker side support as satisfied. Thin queue, realized-gap ownership, MPQ, full-18 parent and all lifecycle guards remain unchanged.','reverseControl':'Same override but spot-vs-strike opposes current dominant side.'},'aggregate':ag,'byMarket':by,'errors':errs,'rows':rows}
 tag=Path(a.pre).stem.replace('r4_strike_formation_mode_override_',''); p=OUT/f'r4_strike_formation_mode_override_{tag}_o{a.offset}_n{len(ids)}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)),'aggregate':ag,'errors':errs},ensure_ascii=False))
if __name__=='__main__':main()
