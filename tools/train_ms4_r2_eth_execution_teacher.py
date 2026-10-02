from __future__ import annotations
import argparse,csv,json,math,os
from collections import deque,defaultdict
from pathlib import Path
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,precision_recall_fscore_support

FEATURES_H=[
'floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share',
'last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s',
'same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s',
'floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share']
FEATURES_C=[x for x in FEATURES_H if x not in ('last_price','last_shares','last_role_taker')]
EPS=1e-9

def fee(sh,px,role): return float(sh)*float(px)*.02 if str(role).upper()=='TAKER' else 0.0

def feat_state(up,down,cost,fees,hist,prev_t,t,last_event=None,include_last=True):
    pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down
    surplus='UP' if up>down+EPS else 'DOWN' if down>up+EPS else 'FLAT'
    while hist and t-hist[0][0]>15000: hist.popleft()
    r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist)
    old5=r5[0] if r5 else (hist[0] if hist else (t,'','',0.,ss,fl,ups,0.5))
    d={
      'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,
      'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,
      'age_since_last_ms':0. if prev_t is None else float(t-prev_t),
      'events_5s':float(len(r5)),'events_15s':float(len(r15)),
      'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),
      'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),
      'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),
      'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),
      'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),
      'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),
      'floor_to_upside_ratio':fl/ups if abs(ups)>EPS else 0.,'floor_per_base_share':fl/base if base>EPS else 0.,
      'upside_per_surplus_share':ups/ss if ss>EPS else 0.
    }
    if include_last:
      le=last_event or (hist[-1] if hist else None)
      d['last_price']=float(le[7]) if le else .5; d['last_shares']=float(le[3]) if le else 0.; d['last_role_taker']=1. if le and le[1]=='TAKER' else 0.
    return d

def read_groups(path):
    by=defaultdict(list); skipped=0
    with open(path,newline='',encoding='utf-8') as f:
      for r in csv.DictReader(f):
        try:
          by[int(r['market_id'])].append((str(r['parent_id']),str(r['role']).upper(),str(r['side']).upper(),int(float(r['first_event_ms'])),float(r['average_price']),float(r['shares'])))
        except Exception: skipped+=1
    for mid in by: by[mid].sort(key=lambda z:(z[3],z[0]))
    return by,skipped

def build_market(rr):
    if len(rr)<6:return [],[]
    up=down=cost=fees=0.; hist=deque(); prev_t=None; hstates=[]; cstates=[]
    taker_times=[r[3] for r in rr if r[1]=='TAKER']; tj=0
    for i,(pid,role,side,t,px,sh) in enumerate(rr):
      if i>0:
        f=feat_state(up,down,cost,fees,hist,prev_t,t,include_last=False)
        cstates.append(([float(f[k]) for k in FEATURES_C],int(role=='TAKER')))
      if side=='UP':up+=sh
      else:down+=sh
      cost+=px*sh; fees+=fee(sh,px,role)
      pu=up-cost-fees;pd=down-cost-fees;fl=min(pu,pd);ups=max(pu,pd);ss=abs(up-down)
      hist.append((t,role,side,sh,ss,fl,ups,px))
      while hist and t-hist[0][0]>15000:hist.popleft()
      f=feat_state(up,down,cost,fees,hist,prev_t,t,last_event=hist[-1],include_last=True)
      while tj<len(taker_times) and taker_times[tj]<=t:tj+=1
      y=int(tj<len(taker_times) and taker_times[tj]-t<=5000)
      hstates.append(([float(f[k]) for k in FEATURES_H],y)); prev_t=t
    return hstates,cstates

def metric(model,X,y):
    p=model.predict_proba(X)[:,1]; pred=(p>=.5).astype(int);pr,rc,f1,_=precision_recall_fscore_support(y,pred,average='binary',zero_division=0)
    return {'n':int(len(y)),'positiveRate':float(np.mean(y)),'predictedRate':float(np.mean(pred)),
      'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if len(np.unique(y))>1 else None,
      'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'precision':float(pr),'recall':float(rc),'f1':float(f1)}

def train(data,kind):
    mids=sorted(data); a=int(.6*len(mids));b=int(.8*len(mids));sets={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])};mats={}
    for name,ms in sets.items():
      rows=[z for m in mids if m in ms for z in data[m]]; X=np.asarray([z[0] for z in rows],float);y=np.asarray([z[1] for z in rows],int);mats[name]=(X,y)
    if kind=='hazard':kw=dict(max_iter=450,learning_rate=.035,max_leaf_nodes=31,min_samples_leaf=70,l2_regularization=3.5,class_weight='balanced',random_state=20260905)
    else:kw=dict(max_iter=350,learning_rate=.045,max_leaf_nodes=31,min_samples_leaf=60,l2_regularization=3.0,class_weight='balanced',random_state=20260905)
    model=HistGradientBoostingClassifier(**kw).fit(*mats['train'])
    return model,{n:metric(model,*xy) for n,xy in mats.items()}, {'trainMarkets':len(sets['train']),'validationMarkets':len(sets['validation']),'testMarkets':len(sets['test'])}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--csv',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();by,skipped=read_groups(a.csv)
    hd={};cd={}
    for mid,rr in by.items():
      h,c=build_market(rr)
      if h:hd[mid]=h
      if c:cd[mid]=c
    if not hd or not cd: raise RuntimeError(f'empty training set groups={len(by)} skipped={skipped}')
    hm,hmet,hsplit=train(hd,'hazard'); cm,cmet,csplit=train(cd,'channel')
    outdir=Path(os.environ['BTC5M_LAN_RESULT_DIR']) if str(a.output).upper()=='AUTO' else Path(a.output);outdir.mkdir(parents=True,exist_ok=True)
    hbundle={'model':hm,'features':FEATURES_H,'teacherVersion':'MS4_R2_ETH_ACTIVE_HAZARD_STRICTPAST_V1','horizonMs':5000,'asset':'ETH','researchOnly':True,'runtimeSafeFeatureContract':True,'removedFutureFeature':'event_index_norm'}
    cbundle={'model':cm,'features':FEATURES_C,'teacherVersion':'MS4_R2_ETH_ACTION_CHANNEL_STRICTPAST_V1','meaning':'At already-authorized next action opportunity choose TAKER vs MAKER','asset':'ETH','researchOnly':True,'runtimeSafeFeatureContract':True,'removedFutureFeature':'event_index_norm'}
    joblib.dump(hbundle,outdir/'ms4_r2_eth_active_hazard_v1.joblib');joblib.dump(cbundle,outdir/'ms4_r2_eth_action_channel_v1.joblib')
    rep={'version':'MS4_R2_ETH_EXECUTION_TEACHER_V1','researchOnly':True,'asset':'ETH','sourceGroups':len(by),'sourceSkippedRows':skipped,'marketsHazard':len(hd),'marketsChannel':len(cd),'rowsHazard':sum(len(v) for v in hd.values()),'rowsChannel':sum(len(v) for v in cd.values()),'featuresHazard':FEATURES_H,'featuresChannel':FEATURES_C,'removed':['event_index_norm'],'splitHazard':hsplit,'splitChannel':csplit,'hazard':hmet,'channel':cmet,'boundary':['Target future role/timing used only as training labels','runtime features strict-past portfolio/event history only','no winner/settlement/future parent count','no strategy authority','no quantity authority']}
    (outdir/'result.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
