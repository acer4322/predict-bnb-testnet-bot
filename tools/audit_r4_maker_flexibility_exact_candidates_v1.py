from pathlib import Path
import json, math, collections
ROOT=Path(__file__).resolve().parents[1]
R=ROOT/'data/research/lan_worker_returns'
SOURCES=[
 R/'r4-temporal-recent8-v3/temporal.json',
 R/'r4-temporal-recent16a-v3/temporal.json',
 R/'r4-temporal-recent16b-v3/temporal.json',
 R/'r4-temporal-unseen15c-v3/temporal.json',
]

def f(x):
 try:
  v=float(x); return v if math.isfinite(v) else None
 except:return None

def main():
 rows=[]
 for p in SOURCES:
  if not p.exists(): continue
  d=json.loads(p.read_text(encoding='utf-8'))
  for r in d.get('rows',[]):
   if not r.get('exactBranchApplied'): continue
   forced=((r.get('counterfactualCompact') or {}).get('forced') or [])
   sm=(((forced[0] if forced else {}).get('activeOrderPathState') or {}).get('summary') or {})
   t=r.get('temporal') or {}
   age=f(sm.get('repairMeanOrderAgeMs')); q=f(sm.get('repairBestQuoteOffsetTicks')); rem=f(sm.get('repairSideRemainingQty'))
   cnt=f(sm.get('repairSideActiveCount')) or 0
   dep=f(sm.get('repairMeanDepletionRatio'))
   row={
    'marketId':int(r['marketId']),'conversion':r.get('conversion'),'deltaPnl':f(r.get('deltaPnl')),
    'repairActiveCount':int(cnt),'repairAgeS':age/1000 if age is not None else None,'repairQuoteOffsetTicks':q,
    'repairRemainingQty':rem,'repairDepletionRatio':dep,
    'makerFill10':f(t.get('makerFilledShares_10s')) or 0,'takerFill10':f(t.get('takerFilledShares_10s')) or 0,
    'deltaFloor10':f(t.get('delta_worst_case_floor_10s')),'deltaAbsNet10':f(t.get('delta_maker_abs_net_10s')),
   }
   # descriptive anatomy flags; not action thresholds
   row['hasRepairCarrier']=cnt>0
   row['veryOldCarrier']=age is not None and age>=30000
   row['farFromTouch']=q is not None and q>=5
   row['nearTouch']=q is not None and q<=2
   row['noRecentMakerProgress']=(row['makerFill10']==0)
   row['staleNoProgress']=row['hasRepairCarrier'] and row['veryOldCarrier'] and row['noRecentMakerProgress']
   row['staleFarNoProgress']=row['staleNoProgress'] and row['farFromTouch']
   rows.append(row)
 print('N',len(rows),collections.Counter(x['conversion'] for x in rows))
 flags=['hasRepairCarrier','veryOldCarrier','farFromTouch','nearTouch','noRecentMakerProgress','staleNoProgress','staleFarNoProgress']
 for conv in ['LOSS->WIN','LOSS->LOSS','WIN->LOSS','WIN->WIN']:
  z=[x for x in rows if x['conversion']==conv]
  print('\n',conv,'n',len(z))
  print({k:f"{sum(bool(x[k]) for x in z)}/{len(z)}" for k in flags})
 # active-taker dependency from role ablation
 role=[]
 for name in ['r4-roleabl-recent8-v1','r4-roleabl-recent16a-v1','r4-roleabl-recent16b-v1','r4-roleabl-unseen15c-v1']:
  p=R/name/'role_ablation.json'
  if p.exists(): role += json.loads(p.read_text(encoding='utf-8')).get('rows',[])
 rm={int(x['marketId']):x for x in role}
 for x in rows:
  a=rm.get(x['marketId']);
  if a:
   x['passiveOnlyConversion']=a.get('passiveOnlyConversion'); x['takerBlocked']=int(a.get('blockedTakerCount') or 0)
 passiveWins=[x for x in rows if x.get('conversion')=='LOSS->WIN' and x.get('passiveOnlyConversion')=='LOSS->WIN']
 print('\nPASSIVE_ONLY_LOSS_TO_WIN',len(passiveWins))
 print([{k:x.get(k) for k in ['marketId','repairAgeS','repairQuoteOffsetTicks','repairRemainingQty','makerFill10','takerFill10','deltaFloor10','deltaAbsNet10','staleNoProgress','staleFarNoProgress']} for x in passiveWins])
 out={'version':'R4_MAKER_FLEXIBILITY_EXACT_CANDIDATES_V1','researchOnly':True,'actionAuthority':False,'thresholdsDescriptiveOnly':True,'rows':rows}
 op=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_maker_flexibility_exact_candidates_v1.json';op.write_text(json.dumps(out,indent=2),encoding='utf-8')
if __name__=='__main__':main()
