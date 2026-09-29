from __future__ import annotations
import bisect,json,sqlite3,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1';DB=Path('data/public_source_snapshot_archive_v2.db');OUT=LANE/'C2_FRESH_PARENT_FORWARD_REVERSION_V1.json';CSV=LANE/'C2_FRESH_PARENT_FORWARD_REVERSION_V1.csv'
s=importlib.util.spec_from_file_location('base','tools/validate_target_direction_c2_fresh_first_parent_choice_v1.py');base=importlib.util.module_from_spec(s);s.loader.exec_module(base)

def snapv(j,*ks):
 for k in ks:
  if k in j and j[k] is not None:
   try:return float(j[k])
   except:return np.nan
 return np.nan

def load_market(c,mid):
 ts=[];rows=[]
 for t,s in c.execute('select sampled_at_ms,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms',(int(mid),)):
  try:j=json.loads(s)
  except:j={}
  ts.append(int(t));rows.append({'t':int(t),'spot':snapv(j,'spotPrice','spot_price'),'fut':snapv(j,'futuresPrice','futures_price'),'sq':snapv(j,'spotQueueImbalance','spot_queue_imbalance'),'fq':snapv(j,'futuresQueueImbalance','futures_queue_imbalance'),'seconds':snapv(j,'secondsLeft','seconds_left')})
 return ts,rows

def attach_path(d,label,c,cache):
 out=[]
 for r in d.itertuples():
  mid=int(r.market_id)
  if mid not in cache:cache[mid]=load_market(c,mid)
  ts,rr=cache[mid];t=int(r.first_parent_placement_ms);i=bisect.bisect_left(ts,t)-1
  if i<0:continue
  b=rr[i];age=t-b['t']
  if age<=0 or age>2000 or not np.isfinite(b['spot']) or not np.isfinite(b['fut']):continue
  sign=1. if r.anchor=='UP' else -1.;x={'cohort':label,'market_id':mid,'entry_ms':int(r.entry_ms),'placement_ms':t,'anchor':r.anchor,'same_side_choice':int(r.first_parent_same_side),'placement_age_ms':age,'base_spot':b['spot'],'base_fut':b['fut'],'pl_o_spot_q':sign*b['sq'],'pl_o_fut_q':sign*b['fq']}
  for h in [1000,3000,5000]:
   k=bisect.bisect_left(ts,t+h)
   if k>=len(ts) or ts[k]>(t+h+1000):x[f'spot_ret_{h//1000}s']=np.nan;x[f'fut_ret_{h//1000}s']=np.nan;continue
   f=rr[k]
   if not np.isfinite(f['spot']) or not np.isfinite(f['fut']):x[f'spot_ret_{h//1000}s']=np.nan;x[f'fut_ret_{h//1000}s']=np.nan;continue
   x[f'spot_ret_{h//1000}s']=sign*(f['spot']/b['spot']-1)*10000;x[f'fut_ret_{h//1000}s']=sign*(f['fut']/b['fut']-1)*10000
  out.append(x)
 return out

def stat(z,col):
 q=z[col].dropna();return {'n':int(len(q)),'mean':float(q.mean()) if len(q) else None,'median':float(q.median()) if len(q) else None,'positiveRate':float((q>0).mean()) if len(q) else None}
def diff_boot(z,col,resamples=5000):
 z=z[['market_id','same_side_choice',col]].dropna();
 a=z[z.same_side_choice.eq(1)][col];b=z[z.same_side_choice.eq(0)][col]
 if len(a)<5 or len(b)<5:return None
 # market cluster resample, difference in mean same-opposite
 mids=z.market_id.unique();rng=np.random.default_rng(20260907);vals=[]
 for _ in range(resamples):
  sm=rng.choice(mids,size=len(mids),replace=True);parts=[z[z.market_id.eq(m)] for m in sm];q=pd.concat(parts,ignore_index=True);aa=q[q.same_side_choice.eq(1)][col];bb=q[q.same_side_choice.eq(0)][col]
  if len(aa) and len(bb):vals.append(float(aa.mean()-bb.mean()))
 return {'sameMean':float(a.mean()),'oppositeMean':float(b.mean()),'difference':float(a.mean()-b.mean()),'ci95':[float(x) for x in np.quantile(vals,[.025,.975])],'resamples':len(vals)}
def main():
 can=base.feat(base.build(LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv',PKG/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'));ext=base.feat(base.build(LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv',EXT/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'));c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True);cache={}
 try:rows=attach_path(can,'CANONICAL120',c,cache)+attach_path(ext,'OLDER173',c,cache)
 finally:c.close()
 d=pd.DataFrame(rows);d.to_csv(CSV,index=False);summary={}
 for cohort,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]:
  co={'rows':len(z),'markets':int(z.market_id.nunique()),'sameRate':float(z.same_side_choice.mean())};
  for h in [1,3,5]:
   co[f'{h}s']={'same':{'spot':stat(z[z.same_side_choice.eq(1)],f'spot_ret_{h}s'),'fut':stat(z[z.same_side_choice.eq(1)],f'fut_ret_{h}s')},'opposite':{'spot':stat(z[z.same_side_choice.eq(0)],f'spot_ret_{h}s'),'fut':stat(z[z.same_side_choice.eq(0)],f'fut_ret_{h}s')},'spotMeanDiffBootstrap':diff_boot(z,f'spot_ret_{h}s'),'futMeanDiffBootstrap':diff_boot(z,f'fut_ret_{h}s')}
  summary[cohort]=co
 out={'version':'OUR_C2_FRESH_PARENT_FORWARD_REVERSION_V1','status':'POST_HOC_ECONOMIC_FALSIFICATION','summary':summary,'guards':['Future 1/3/5s spot/futures path is outcome-only and never a runtime feature.','Return is oriented to prior clean anchor; positive means market subsequently moves back toward prior side.','Base snapshot is strict-past <=2s before fresh parent placement; future snapshot is first at/after horizon and no more than 1s late.','Analysis asks economic plausibility of contrarian fresh-parent choice, not profitability of the Prediction order itself.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
