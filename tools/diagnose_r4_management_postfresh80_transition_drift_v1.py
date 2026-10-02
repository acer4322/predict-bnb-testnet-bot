from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from scipy.stats import ks_2samp,spearmanr
ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_rows_v2.csv'
NEW=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_postfresh80_objective_transition_rows_v1.csv'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_postfresh80_transition_drift_v1.json'
BASE=['seconds_left','abs_gap','risk_deficit','floor','upside','absNet','coverage','floor_per_gross','current_commitment','seconds_since_prev_parent','events_5s','events_15s','transitions_15s','mode_age_s','distinct_objective_keys_15s']

def build(path):
 d=pd.read_csv(path).sort_values(['market_id','t','parent_id']).copy(); rows=[]
 for mid,g in d.groupby('market_id',sort=False):
  prev=set(); last_risk=None
  for t,h in g.groupby('t',sort=True):
   if last_risk is not None:
    for _,r in h.iterrows():
     z=r.to_dict(); fam=str(r.objective_family); z['prev_pb_set']=float('PAIR_BALANCE' in prev); z['prev_ss_set']=float('STATE_SHAPING' in prev); z['switch']=int(fam not in prev); z['risk_deficit_delta_strict1']=float(pd.to_numeric(r.risk_deficit,errors='coerce')-last_risk); rows.append(z)
   prev=set(map(str,h.objective_family)); last_risk=float(pd.to_numeric(h.risk_deficit,errors='coerce').mean())
 return pd.DataFrame(rows)

def feature_stats(ref,z,features):
 o={}
 y=z['switch'].to_numpy(int)
 for f in features:
  a=pd.to_numeric(ref[f],errors='coerce').dropna().to_numpy(float); b=pd.to_numeric(z[f],errors='coerce').dropna().to_numpy(float)
  if not len(a) or not len(b): continue
  sd=float(np.std(a)); smd=float((np.mean(b)-np.mean(a))/(sd+1e-9)); ks=float(ks_2samp(a,b).statistic)
  xb=pd.to_numeric(z[f],errors='coerce'); m=xb.notna(); rho=float(spearmanr(xb[m],z.loc[m,'switch']).statistic) if m.sum()>5 and xb[m].nunique()>1 else None
  o[f]={'refMean':float(np.mean(a)),'mean':float(np.mean(b)),'refMedian':float(np.median(a)),'median':float(np.median(b)),'smdVsOld':smd,'ksVsOld':ks,'spearmanToSwitch':rho,'missingRate':float(1-len(b)/len(z))}
 return o

def main():
 old=build(OLD); new=build(NEW); mids=[int(x) for x in sorted(new.market_id.unique(),key=lambda m:new.loc[new.market_id==m,'t'].min())]; feats=BASE+['prev_pb_set','prev_ss_set','risk_deficit_delta_strict1']
 blocks=[]
 old_stats=feature_stats(old,old,feats)
 for i in range(4):
  z=new[new.market_id.isin(mids[i*20:(i+1)*20])].copy(); fs=feature_stats(old,z,feats); ranked=sorted(fs.items(),key=lambda kv:abs(kv[1]['smdVsOld']),reverse=True)
  blocks.append({'block':i+1,'markets':mids[i*20:(i+1)*20],'rows':int(len(z)),'switchRate':float(z.switch.mean()),'topAbsSmd':[{'feature':k,**v} for k,v in ranked[:8]],'features':fs})
 # conditional drift: compare feature->switch rank correlations old vs latest block
 latest=blocks[-1]['features']; cond=[]
 for f in feats:
  ro=old_stats.get(f,{}).get('spearmanToSwitch'); rn=latest.get(f,{}).get('spearmanToSwitch')
  if ro is None or rn is None: continue
  cond.append({'feature':f,'oldRho':ro,'latestRho':rn,'deltaRho':rn-ro,'signFlip':bool(ro*rn<0)})
 cond=sorted(cond,key=lambda x:abs(x['deltaRho']),reverse=True)
 rep={'version':'R4_MANAGEMENT_POSTFRESH80_TRANSITION_DRIFT_V1','researchOnly':True,'oldRows':int(len(old)),'newRows':int(len(new)),'blocks':blocks,'latestConditionalDriftTop':cond[:12],'guard':'Diagnostic only on frozen developmentBackfillMarkets; no untouched promotion validation access and no model/threshold fitting.'}
 OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps({'oldRows':len(old),'newRows':len(new),'blockSummary':[{'block':b['block'],'rows':b['rows'],'switchRate':b['switchRate'],'topAbsSmd':b['topAbsSmd'][:5]} for b in blocks],'latestConditionalDriftTop':cond[:10]},indent=2))
if __name__=='__main__': main()
