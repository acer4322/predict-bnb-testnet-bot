from __future__ import annotations
import glob,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];RET=ROOT/'data/research/lan_worker_returns';OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_first_late_volhigh_v26_challenge_d_summary.json'
ids=json.loads((ROOT/'data/research/r4_v0/p0_provenance_v1/r4_first_late_volhigh_v26_challenge_d_contract.json').read_text(encoding='utf-8'))['marketIds']
def load(pattern,cfg=None):
 out={}
 for p in glob.glob(str(RET/pattern)):
  rp=Path(p)/'result.json'
  if not rp.exists():continue
  d=json.loads(rp.read_text(encoding='utf-8'))
  for r in d.get('rows',[]):
   if cfg is None or r.get('config')==cfg:out[int(r['marketId'])]=r
 return out
h=load('r4-v26d-h2-*','CONT_STATE_H2_SUSPEND');v=load('r4-v26d-cand-*')
q=','.join('?'*len(ids));db=sqlite3.connect(str(ROOT/'data/target_wallet_official_v1.db'));wins={int(a):str(b) for a,b in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',ids) if b in ('UP','DOWN')};db.close()
def pnl(r,w):f=r['final'];return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])
rows=[]
for m in ids:
 if m not in h or m not in v or m not in wins:continue
 hp=pnl(h[m],wins[m]);vp=pnl(v[m],wins[m]);c=v[m].get('counts',{});rows.append({'marketId':m,'winner':wins[m],'h2Pnl':hp,'v26Pnl':vp,'delta':vp-hp,'h2Win':hp>0,'v26Win':vp>0,'firstLateDecision':c.get('firstLateVolatilityDecision',0),'firstLateHighAllowed':c.get('firstLateHighAllowed',0),'firstLateBlockedNotHigh':c.get('firstLateBlockedNotHigh',0),'lateAcq':c.get('lateWeakAcquisitions',0),'authorityOvershoot':v[m].get('authorityOvershoot')})
summary={'version':'R4_FIRST_LATE_VOLHIGH_V26_CHALLENGE_D_SUMMARY','n':len(rows),'h2Pnl':sum(x['h2Pnl'] for x in rows),'v26Pnl':sum(x['v26Pnl'] for x in rows),'delta':sum(x['delta'] for x in rows),'h2Wins':sum(x['h2Win'] for x in rows),'v26Wins':sum(x['v26Win'] for x in rows),'lossToWin':sum((not x['h2Win']) and x['v26Win'] for x in rows),'winToLoss':sum(x['h2Win'] and (not x['v26Win']) for x in rows),'changed':sum(abs(x['delta'])>1e-9 for x in rows),'highAllowed':sum(x['firstLateHighAllowed'] for x in rows),'lateAcq':sum(x['lateAcq'] for x in rows),'rows':rows}
OUT.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({k:summary[k] for k in ['n','h2Pnl','v26Pnl','delta','h2Wins','v26Wins','lossToWin','winToLoss','changed','highAllowed','lateAcq']},ensure_ascii=False));
for x in sorted([x for x in rows if abs(x['delta'])>1e-9],key=lambda x:x['delta'],reverse=True):print(x)
