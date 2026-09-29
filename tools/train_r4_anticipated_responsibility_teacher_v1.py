from __future__ import annotations
import bisect,json,lzma,math
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/hft_forward_paper_v1/markets';OUT=ROOT/'data/research/r4_v0/hourly/r4_anticipated_responsibility_teacher_v1.json'
FROZEN={1700601,1700655,1701123,1701140,1701356,1701359,1701523,1701531}
LEADS=(1000,2000,3000,5000)
LOCAL=['combined_net_toward_side','weak_gap_now','weak_gap_shortfall18','candidate_is_flat','candidate_is_surplus','candidate_is_weak_lt18','combined_abs_net','maker_abs_net','combined_paired_coverage','maker_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_fills_1s','maker_fills_5s','maker_shares_5s','maker_shares_10s','taker_fills_1s','taker_fills_5s','taker_shares_5s','taker_shares_10s','p_maker_side','p_maker_opp','p_taker_1s','p_taker_3s','p_residual_wake','active_maker_orders','book_age_ms','predict_toward_side']
EXTERNAL=['spot_toward_side','chainlink_toward_side']
SETS={'LOCAL':LOCAL,'EXTERNAL_ONLY':EXTERNAL,'LOCAL_EXTERNAL':LOCAL+EXTERNAL}
def choose(n=160):
 ps=sorted(SRC.glob('*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True);seen=set();out=[]
 for p in ps:
  try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
  except:continue
  mid=int(d.get('marketId') or 0);student=str(d.get('student') or '')
  if not mid or mid in seen or mid in FROZEN or 'R2_RESIDUAL' not in student or not d.get('orderMeta') or not d.get('decisionRows'):continue
  seen.add(mid);out.append((p,d))
  if len(out)>=n:break
 return out
def weak(net,side):
 if abs(net)<1e-9:return False
 return (net>0 and side=='DOWN') or (net<0 and side=='UP')
def row_at(rows,t):
 ts=[int(r.get('decisionMs') or 0) for r in rows];j=bisect.bisect_right(ts,int(t))-1;return rows[j] if j>=0 else None
def val(d,path,default=0.):
 x=d
 for k in path:
  if not isinstance(x,dict):return default
  x=x.get(k)
 try:return float(x) if x is not None and math.isfinite(float(x)) else default
 except:return default
def oriented(v,side):return float(v)*(1 if side=='UP' else -1)
def main():
 samples=[];markets=[]
 for p,d in choose():
  mid=int(d['marketId']);dec=sorted(d.get('decisionRows') or [],key=lambda r:int(r.get('decisionMs') or 0));markets.append((mid,min(int(r.get('decisionMs') or 0) for r in dec if int(r.get('decisionMs') or 0)>0)))
  for oid,o in (d.get('orderMeta') or {}).items():
   need=int(o.get('placedAtMs') or 0);side=str(o.get('side') or '').upper();qty=float(o.get('shares') or 0)
   if not need or side not in ('UP','DOWN') or qty<17.5:continue
   rn=row_at(dec,need);pn=(rn or {}).get('portfolio') or {};net_need=val(pn,['combined_net']);abs_need=val(pn,['combined_abs_net']);label=int(weak(net_need,side) and abs_need>=18.-1e-9)
   for lead in LEADS:
    r=row_at(dec,need-lead)
    if not r:continue
    po=r.get('portfolio') or {};net=val(po,['combined_net']);ab=val(po,['combined_abs_net'])
    # Only study genuinely anticipatory cases: a full 18-share weak responsibility is not yet realized.
    if weak(net,side) and ab>=18.-1e-9:continue
    mo=r.get('models') or {};di=r.get('direction') or {};co=di.get('components') or {};sgn=1 if side=='UP' else -1
    samples.append({'market_id':mid,'t':int(r.get('decisionMs') or 0),'lead_s':lead/1000.,'y':label,'seconds_left_proxy':max(0.,(need-int(r.get('decisionMs') or 0))/1000.),'candidate_side':side,'combined_net_toward_side':net*(1 if side=='UP' else -1),'candidate_relation_now':('FLAT' if abs(net)<1e-9 else ('SURPLUS' if net*(1 if side=='UP' else -1)>0 else 'WEAK_LT18')),'weak_gap_now':(ab if weak(net,side) else 0.0),'weak_gap_shortfall18':(max(0.0,18.0-ab) if weak(net,side) else 18.0),'combined_abs_net':ab,'maker_abs_net':val(po,['maker_abs_net']),'combined_paired_coverage':val(po,['combined_paired_coverage']),'maker_paired_coverage':val(po,['maker_paired_coverage']),'worst_case_floor':val(po,['worst_case_floor']),'best_case_pnl':val(po,['best_case_pnl']),'abs_payoff_gap':val(po,['abs_payoff_gap']),'maker_fills_1s':val(po,['maker_fills_1s']),'maker_fills_5s':val(po,['maker_fills_5s']),'maker_shares_5s':val(po,['maker_shares_5s']),'maker_shares_10s':val(po,['maker_shares_10s']),'taker_fills_1s':val(po,['taker_fills_1s']),'taker_fills_5s':val(po,['taker_fills_5s']),'taker_shares_5s':val(po,['taker_shares_5s']),'taker_shares_10s':val(po,['taker_shares_10s']),'p_maker_side':val(mo,['pMakerUp'] if side=='UP' else ['pMakerDown']),'p_maker_opp':val(mo,['pMakerDown'] if side=='UP' else ['pMakerUp']),'p_taker_1s':val(mo,['pTaker1s']),'p_taker_3s':val(mo,['pTaker3s']),'p_residual_wake':val(mo,['pResidualWake']),'active_maker_orders':val(r,['activeMakerOrders']),'book_age_ms':val(r,['bookAgeMs']),'candidate_is_flat':1.0 if abs(net)<1e-9 else 0.0,'candidate_is_surplus':1.0 if (abs(net)>=1e-9 and net*(1 if side=='UP' else -1)>0) else 0.0,'candidate_is_weak_lt18':1.0 if (weak(net,side) and ab<18.-1e-9) else 0.0,'predict_toward_side':oriented(val(co,['predict','value']),side),'spot_toward_side':oriented(val(co,['spot','value']),side),'chainlink_toward_side':oriented(val(co,['chainlink','value']),side)})
 df=pd.DataFrame(samples).sort_values(['t','market_id']);df.to_csv(ROOT/'data/research/r4_v0/hourly/r4_anticipated_responsibility_teacher_v1_rows.csv',index=False);order=[m for m,_ in sorted(markets,key=lambda z:z[1])];a=int(len(order)*.70);b=int(len(order)*.85);parts={'train':set(order[:a]),'validation':set(order[a:b]),'test':set(order[b:])};res={}
 def mdl():return Pipeline([('i',SimpleImputer(strategy='median')),('m',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=140,l2_regularization=3.0,min_samples_leaf=30,random_state=42,class_weight='balanced'))])
 def met(y,p):return {'n':len(y),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
 for name,feats in SETS.items():
  tr=df[df.market_id.isin(parts['train'])];m=mdl().fit(tr[feats],tr.y.astype(int));res[name]={}
  for sk,ids in parts.items():
   x=df[df.market_id.isin(ids)];res[name][sk]=met(x.y.astype(int),m.predict_proba(x[feats])[:,1])
 inc={}
 for sk in ('validation','test'):
  q=res['LOCAL'][sk];f=res['LOCAL_EXTERNAL'][sk];inc[sk]={'externalOverLocal_auc':f['auc']-q['auc'] if f['auc'] is not None and q['auc'] is not None else None,'externalOverLocal_ap':f['ap']-q['ap'],'externalOverLocal_logLossImprovement':q['logLoss']-f['logLoss']}
 rep={'version':'R4_ANTICIPATED_RESPONSIBILITY_TEACHER_V1','researchOnly':True,'question':'Before a full 18-share weak-side responsibility is realized, can strictly-past local Formation/controller state predict that the future frozen Maker need will arrive with >=18 shares of weak-side gap, and do measured external direction components add value?','dataset':{'rows':len(df),'markets':df.market_id.nunique(),'positiveRate':float(df.y.mean()),'leadsMs':list(LEADS),'splitMarkets':{k:len(v) for k,v in parts.items()}},'features':{'local':LOCAL,'external':EXTERNAL},'results':res,'incremental':inc,'guards':['Future need state is research label only.','Inputs use only latest decision row at or before checkpoint.','Rows already possessing >=18 realized weak-side responsibility are excluded.','This teacher concerns OUR frozen R2 responsibility formation, not proof of Target private information.','No threshold action authority; no HFT/live change.']};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'dataset':rep['dataset'],'results':res,'incremental':inc},ensure_ascii=False))
if __name__=='__main__':main()
