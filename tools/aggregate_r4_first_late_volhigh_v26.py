from __future__ import annotations
import glob,json,sqlite3
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; RET=ROOT/'data/research/lan_worker_returns'; OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_first_late_volhigh_v26_summary.json'
def rows(pattern,cfg=None):
 out={}
 for p in glob.glob(str(RET/pattern)):
  rp=Path(p)/'result.json'
  if not rp.exists():continue
  d=json.loads(rp.read_text(encoding='utf-8'))
  for r in d.get('rows',[]):
   if cfg is None or r.get('config')==cfg:out[int(r['marketId'])]=r
 return out
h={}
for pat in ['r4-cont-h2s-chal24-*','r4-cont-h2s-chalb-*','r4-profit-v2c-pair-*']:
 h.update(rows(pat,'CONT_STATE_H2_SUSPEND'))
v=rows('r4-firstvol-v26-*')
ids=sorted(set(h)&set(v));q=','.join('?'*len(ids));db=sqlite3.connect(str(ROOT/'data/target_wallet_official_v1.db'));wins={int(a):str(b) for a,b in db.execute(f'select market_id,winner from target_markets where market_id in ({q})',ids) if b in ('UP','DOWN')};db.close()
def pnl(r,w):f=r['final'];return (float(f['up']) if w=='UP' else float(f['down']))-float(f['cost'])
A={1807656,1807530,1807506,1807496,1807486,1807482,1807399,1807385,1804995,1804990,1804961,1804906,1804896,1804892,1804768,1804726,1804717,1804544,1804533,1804517,1804514,1804444,1804312,1804311}
B={1808846,1808837,1808225,1808010,1807985,1807661,1807657,1804301,1804298,1804227,1804217,1803889,1803886,1803771,1803732,1803722,1803560,1803530,1803520,1803511,1803469,1803466,1803452,1803306}
rr=[]
for m in ids:
 hp=pnl(h[m],wins[m]);vp=pnl(v[m],wins[m]);co='A' if m in A else 'B' if m in B else 'C';c=v[m].get('counts',{});rr.append({'marketId':m,'cohort':co,'h2Pnl':hp,'v26Pnl':vp,'delta':vp-hp,'h2Win':hp>0,'v26Win':vp>0,'firstLateDecision':c.get('firstLateVolatilityDecision',0),'firstLateHighAllowed':c.get('firstLateHighAllowed',0),'firstLateBlockedNotHigh':c.get('firstLateBlockedNotHigh',0),'lateAcq':c.get('lateWeakAcquisitions',0),'authorityOvershoot':v[m].get('authorityOvershoot')})
def agg(z):return {'n':len(z),'h2Pnl':sum(x['h2Pnl'] for x in z),'v26Pnl':sum(x['v26Pnl'] for x in z),'delta':sum(x['delta'] for x in z),'h2Wins':sum(x['h2Win'] for x in z),'v26Wins':sum(x['v26Win'] for x in z),'changed':sum(abs(x['delta'])>1e-9 for x in z),'highAllowed':sum(x['firstLateHighAllowed'] for x in z),'lateAcq':sum(x['lateAcq'] for x in z)}
summary={'version':'R4_FIRST_LATE_VOLHIGH_V26_SUMMARY','researchOnly':True,'all':agg(rr),'byCohort':{c:agg([x for x in rr if x['cohort']==c]) for c in 'ABC'},'rows':rr}
OUT.write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'all':summary['all'],'byCohort':summary['byCohort']},ensure_ascii=False));print('changed');
for x in sorted([x for x in rr if abs(x['delta'])>1e-9],key=lambda x:x['delta'],reverse=True):print(x)
