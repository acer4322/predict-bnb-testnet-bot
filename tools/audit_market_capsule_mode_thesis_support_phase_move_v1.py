from __future__ import annotations
import argparse,json
from pathlib import Path
import duckdb,pandas as pd,numpy as np
from sklearn.metrics import roc_auc_score
EPS=1e-9

def role(r):
 u=float(r.pre_up_shares);d=float(r.pre_down_shares);gap=abs(u-d);side=str(r.action_side);q=float(r.action_shares)
 if gap<=EPS:return 'BALANCED'
 weak='UP' if u<d else 'DOWN';strong='DOWN' if weak=='UP' else 'UP'
 if side==strong:return 'EXPAND'
 if side==weak:return 'MIXED' if q>gap+EPS else 'REPAIR'
 return 'OTHER'

def load(root):
 d=str(Path(root)/'decision_seams.parquet');p=str(Path(root)/'public_snapshots.parquet');con=duckdb.connect()
 q="""select d.market_id,d.seam_id,d.action_event_ms,d.same_timestamp_action_count,d.pre_up_shares,d.pre_down_shares,d.action_side,d.action_shares,d.public_sample_id, cast(json_extract(p.snapshot_json, '$.spotPrice') as double) spot, cast(json_extract(p.snapshot_json, '$.futuresPrice') as double) futures, cast(json_extract(p.snapshot_json, '$.chainlinkPrice') as double) chainlink, cast(json_extract(p.snapshot_json, '$.perpSpotBasisBps') as double) basis from read_parquet(?) d left join read_parquet(?) p on d.public_sample_id=p.id order by d.market_id,d.action_event_ms,d.seam_id"""
 df=con.execute(q,[d,p]).df();con.close();df['role']=df.apply(role,axis=1);return df

def ret(a,b):
 try:
  a=float(a);b=float(b)
  if not np.isfinite(a) or not np.isfinite(b) or a<=0:return np.nan
  return (b/a-1)*10000
 except:return np.nan

def build(df):
 out=[]
 for mid,g in df.groupby('market_id',sort=False):
  rows=[r for _,r in g.iterrows() if int(r.same_timestamp_action_count)==1]
  for i in range(1,len(rows)):
   cur=rows[i];prev=rows[i-1];pr=str(prev.role);cr=str(cur.role)
   if pr not in {'REPAIR','EXPAND'} or cr not in {'REPAIR','EXPAND'}:continue
   j=i-1
   while j-1>=0 and str(rows[j-1].role)==pr:j-=1
   ent=rows[j];sgn=1.0 if str(prev.action_side)=='UP' else -1.0
   basis=np.nan
   if pd.notna(cur.basis) and pd.notna(ent.basis):basis=sgn*(float(cur.basis)-float(ent.basis))
   out.append({'market_id':int(mid),'role_stay':1 if cr==pr else 0,'side_stay':1 if str(cur.action_side)==str(prev.action_side) else 0,'mode_spot':sgn*ret(ent.spot,cur.spot),'mode_futures':sgn*ret(ent.futures,cur.futures),'mode_chainlink':sgn*ret(ent.chainlink,cur.chainlink),'last_spot':sgn*ret(prev.spot,cur.spot),'last_futures':sgn*ret(prev.futures,cur.futures),'last_chainlink':sgn*ret(prev.chainlink,cur.chainlink),'mode_basis':basis})
 return pd.DataFrame(out)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--a',required=True);ap.add_argument('--b',required=True);ap.add_argument('--c',required=True);ap.add_argument('--d',required=True);ap.add_argument('--output',required=True);ns=ap.parse_args()
 sets={'A':ns.a,'B':ns.b,'C':ns.c,'D':ns.d};features=['mode_spot','mode_futures','mode_chainlink','last_spot','last_futures','last_chainlink','mode_basis'];res={'version':'MARKET_CAPSULE_MODE_THESIS_SUPPORT_PHASE_MOVE_V1_RESULT_20260907','researchOnly':True,'sets':{}}
 for name,root in sets.items():
  x=build(load(root));rr={'rows':len(x),'markets':int(x.market_id.nunique()),'roleStayRate':float(x.role_stay.mean()),'sideStayRate':float(x.side_stay.mean()),'labels':{}}
  for lab in ['role_stay','side_stay']:
   fs={}
   for f in features:
    z=x[[lab,f]].dropna();auc=None if len(z)<2 or z[lab].nunique()<2 else float(roc_auc_score(z[lab],z[f]));fs[f]={'n':len(z),'aucStay':auc,'stayMedian':float(z.loc[z[lab]==1,f].median()) if len(z[z[lab]==1]) else None,'switchMedian':float(z.loc[z[lab]==0,f].median()) if len(z[z[lab]==0]) else None}
   rr['labels'][lab]=fs
  res['sets'][name]=rr
 res['portable']={}
 for lab in ['role_stay','side_stay']:
  good=[]
  for f in features:
   vals=[res['sets'][s]['labels'][lab][f]['aucStay'] for s in ['B','C','D']]
   if all(v is not None and v>=.55 for v in vals):good.append({'feature':f,'externalAucs':vals})
  res['portable'][lab]=good
 p=Path(ns.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps(res,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
