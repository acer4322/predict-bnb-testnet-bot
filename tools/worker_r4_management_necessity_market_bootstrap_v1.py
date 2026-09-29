from pathlib import Path
import os,json,numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score
ROOT=Path.cwd(); ST=ROOT/'.lan_worker_v1/staging'; OUT=Path(os.environ['BTC5M_LAN_RESULT_DIR']); OUT.mkdir(parents=True,exist_ok=True)
coh={'FRESH24':'r4_management_hft_shadow_fresh24_v1_rows.csv','UNSEEN24':'r4_management_hft_shadow_unseen24_v1_rows.csv','REPLICATION3':'r4_management_hft_shadow_replication3_v1_rows.csv'}
features=['absnet_ratio','abs_gap','risk_deficit','coverage','floor_per_gross','floor','weak_active_owners']
rng=np.random.default_rng(8292026); B=2000; rep={}
for name,fn in coh.items():
 d=pd.read_csv(ST/fn); d=d[(d.seconds_left<=180)&(d.seconds_left>60)&(d.build_now==1)].copy(); ps=d[['p_continue_full','p_handoff_full','p_observe_full']].to_numpy(float); labs=np.array(['CONTINUE','HANDOFF','OBSERVE']); d['decision']=labs[np.argmax(ps,axis=1)]; d['teacher']=d.management_label_5s.map({'CONTINUE_WEAK':'CONTINUE','HANDOFF_ALLOW':'HANDOFF','OBSERVE_NO_EVENT':'OBSERVE'}).fillna('UNKNOWN'); x=d[(d.decision=='CONTINUE')&d.teacher.isin(['OBSERVE','CONTINUE'])].copy(); x['y']=(x.teacher=='OBSERVE').astype(int); markets=x.marketId.unique(); fr={}
 for f in features:
  vals=[]
  for _ in range(B):
   samp=rng.choice(markets,size=len(markets),replace=True); parts=[]
   for i,m in enumerate(samp):
    g=x[x.marketId==m].copy(); g['_boot_market']=i; parts.append(g)
   z=pd.concat(parts,ignore_index=True)[[f,'y']].replace([np.inf,-np.inf],np.nan).dropna()
   if z.y.nunique()==2: vals.append(float(roc_auc_score(z.y,z[f])))
  a=np.array(vals,float); fr[f]={'n_boot':int(len(a)),'auc_median':float(np.median(a)),'p_auc_gt_0_5':float((a>0.5).mean()),'p_auc_lt_0_5':float((a<0.5).mean()),'ci95':[float(np.quantile(a,.025)),float(np.quantile(a,.975))]}
 rep[name]={'markets':int(len(markets)),'rows':int(len(x)),'features':fr}
res={'version':'R4_MANAGEMENT_NECESSITY_MARKET_BOOTSTRAP_WORKER_V1','strictPast':True,'B':B,'seed':8292026,'cohorts':rep}
(OUT/'result.json').write_text(json.dumps(res,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'out':str(OUT/'result.json'),'B':B}))
