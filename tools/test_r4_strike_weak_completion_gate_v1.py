from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_v1 as roll
OUT=ROOT/'data/research/r4_v0/hourly';PRE=OUT/'r4_rolling_gap_owner_fresh24_preregistered.json'
CFGS=('ROLL_KEEP_STRIKE_WEAK_COMPLETE_FLAT2','ROLL_KEEP_STRIKE_WEAK_REVERSE_FLAT2')
def load(mid):
 ps=sorted((ROOT/'data/hft_forward_paper_v1/markets').glob(f'{mid}_*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
 for p in ps:
  try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
  except Exception:continue
  if int(d.get('marketId') or 0)==int(mid) and d.get('orderMeta'):return d
 raise RuntimeError(mid)
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--offset',type=int,default=0);ap.add_argument('--count',type=int,default=8);a=ap.parse_args();ids=json.load(open(PRE,encoding='utf-8'))['marketIds'][a.offset:a.offset+a.count];rows=[];errs=[]
 for mid in ids:
  d=load(mid)
  for cfg in CFGS:
   try:r=roll.simulate(d,cfg);r['config']=cfg;rows.append(r)
   except Exception as e:errs.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
  print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errs)}),flush=True)
 ag={c:roll.agg([r for r in rows if r['config']==c]) for c in CFGS}; rep={'version':'R4_STRIKE_WEAK_COMPLETION_GATE_V1','researchOnly':True,'offset':a.offset,'ids':ids,'preRegistered':{'weakComplete':'FLAT second full-18 owner only when candidate Predict mid in (0.20,0.45], spot/strike is 2-10bps against candidate (therefore confirms opposite/favored side), secondsLeft 120-300, and at least one opposite-side live owner already exists','reverseControl':'same but spot/strike 2-10bps toward candidate','otherGuards':'receipt-clock public source <=1s old; thin queue + pMaker support + MPQ unchanged; strike permission only authorizes acquisition and preserves only its own queue age'},'aggregate':ag,'rows':rows,'errors':errs};p=OUT/f'r4_strike_weak_completion_gate_v1_o{a.offset}_n{len(ids)}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'errors':errs},ensure_ascii=False))
if __name__=='__main__':main()
