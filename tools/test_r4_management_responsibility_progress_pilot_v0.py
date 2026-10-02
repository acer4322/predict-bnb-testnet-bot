from __future__ import annotations
import json,sqlite3
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_pilot_v0_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_pilot_v0.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_pilot_v0_rows.csv'
LIFE=ROOT/'data/wallet_maker_book_inference.db';OFF=ROOT/'data/target_wallet_official_v1.db'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross']
RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s']
BASE=PORT+RESP+MEM
PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s']
FULL=BASE+PROG
CLASSES=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT'];EPS=1e-9

def qdf(db,sql,params=()):
 c=sqlite3.connect(f'file:{db.as_posix()}?mode=ro',uri=True);c.execute('pragma query_only=on');d=pd.read_sql_query(sql,c,params=params);c.close();return d

def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def binmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))}
def multimet(y,p):
 y=np.asarray(y,str);p=np.asarray(p,float);pred=np.asarray(CLASSES)[np.argmax(p,1)];Y=label_binarize(y,classes=CLASSES);rec=recall_score(y,pred,labels=CLASSES,average=None,zero_division=0);return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=CLASSES,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=CLASSES)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(CLASSES,rec)}}
def align(p,classes):
 co=list(classes);return np.column_stack([p[:,co.index(c)] for c in CLASSES])

def enrich(d,mids):
 ph=','.join('?'*len(mids))
 life=qdf(LIFE,f"select market_id,order_hash,target_side,placement_first_ms,last_target_ms,placement_allocated_shares,expected_parent_shares from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
 ev=qdf(OFF,f"select market_id,order_hash,role,side,quote_type,event_ms,shares from wallet_shadow_target_events where market_id in ({ph}) and quote_type='BID' order by market_id,event_ms,id",mids)
 po=qdf(OFF,f"select market_id,order_hash,first_event_ms from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID'",mids)
 lg={int(k):g.copy() for k,g in life.groupby('market_id')};eg={int(k):g.copy() for k,g in ev.groupby('market_id')};pg={int(k):g.copy() for k,g in po.groupby('market_id')};out=[]
 for mid,g in d.groupby('market_id'):
  gl=lg.get(int(mid));ge=eg.get(int(mid));gp=pg.get(int(mid))
  if gl is None or ge is None or gp is None:continue
  ge=ge.sort_values('event_ms'); et=ge.event_ms.to_numpy(np.int64); eside=ge.side.astype(str).to_numpy(); esh=ge.shares.to_numpy(float)
  up=np.cumsum(np.where(eside=='UP',esh,0.));dn=np.cumsum(np.where(eside=='DOWN',esh,0.))
  # maker fills indexed per order hash for strict-past progress
  mk=ge[ge.role.astype(str).str.upper().eq('MAKER')].copy(); byhash={str(h):x.sort_values('event_ms') for h,x in mk.groupby('order_hash') if pd.notna(h)}
  for r in g.itertuples():
   t=int(r.t);j=np.searchsorted(et,t,side='left')-1;u=float(up[j]) if j>=0 else 0.;dd=float(dn[j]) if j>=0 else 0.;gap=u-dd
   if abs(gap)<=EPS:continue
   dom='UP' if gap>0 else 'DOWN';weak='DOWN' if dom=='UP' else 'UP';curhash=set(gp.loc[gp.first_event_ms.astype(np.int64)==t,'order_hash'].dropna().astype(str))
   starts=gl.placement_first_ms.to_numpy(np.int64);ends=gl.last_target_ms.fillna(gl.placement_first_ms).to_numpy(np.int64);hashes=gl.order_hash.fillna('').astype(str).to_numpy();sides=gl.target_side.astype(str).to_numpy();comm=gl.placement_allocated_shares.fillna(0.).to_numpy(float);exp=gl.expected_parent_shares.fillna(0.).to_numpy(float);comm=np.where(comm>EPS,comm,exp);active=(starts<t)&(ends>=t)&np.array([h not in curhash for h in hashes])&(comm>EPS)
   vals={}
   for name,side in [('weak',weak),('dominant',dom)]:
    mask=active&(sides==side);idx=np.where(mask)[0];totc=totr=recent=0.
    for ii in idx:
     h=hashes[ii];c=float(comm[ii]);x=byhash.get(h);real=rec=0.
     if x is not None:
      tt=x.event_ms.to_numpy(np.int64);qq=x.shares.to_numpy(float);k=np.searchsorted(tt,t,side='left');real=float(qq[:k].sum());lo=np.searchsorted(tt,t-5000,side='left');rec=float(qq[lo:k].sum())
     totr+=min(real,c);totc+=c;recent+=rec
    vals[f'{name}_unresolved_shares']=float(max(0.,totc-totr));vals[f'{name}_progress_ratio']=float(totr/totc) if totc>EPS else 0.;vals[f'{name}_fill_shares_5s']=float(recent)
   z={c:getattr(r,c) for c in d.columns};z.update(vals);out.append(z)
 return pd.DataFrame(out)

def main():
 pre=json.loads(PREREG.read_text(encoding='utf-8'));d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);mo=d.groupby('market_id').t.min().sort_values();mids=mo.index.astype(int).tolist()[-120:];d=d[d.market_id.isin(mids)].copy().sort_values(['market_id','t']);e=enrich(d,mids).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL).copy();e.to_csv(ROWS,index=False);ms=e.groupby('market_id').t.min().sort_values().index.astype(int).tolist();initial=max(60,int(len(ms)*2/3));rem=len(ms)-initial;sizes=[rem//3]*3
 for i in range(rem%3):sizes[i]+=1
 cur=initial;blocks=[]
 for bi,sz in enumerate(sizes,1):
  trm=ms[:cur];tem=ms[cur:cur+sz];cur+=sz;tr=e[e.market_id.isin(trm)];te=e[e.market_id.isin(tem)];tr0=tr[tr.build_now==1];te0=te[te.build_now==1];b={'block':bi,'trainMarkets':len(trm),'testMarkets':len(tem),'testRows':int(len(te0))}
  for jj,(name,feats) in enumerate([('M0_BASE',BASE),('M0_PROGRESS',FULL)]):
   m=hgb(27000+bi*20+jj).fit(tr0[feats],tr0.continue_weak_5s.astype(int));b[name]=binmet(te0.continue_weak_5s,m.predict_proba(te0[feats])[:,1])
  tr1=tr0[tr0.management_label_5s.notna()&(tr0.management_label_5s!='')];te1=te0[te0.management_label_5s.notna()&(te0.management_label_5s!='')]
  for jj,(name,feats) in enumerate([('M1_BASE',BASE),('M1_PROGRESS',FULL)]):
   m=hgb(28000+bi*20+jj).fit(tr1[feats],tr1.management_label_5s);b[name]=multimet(te1.management_label_5s,align(m.predict_proba(te1[feats]),m.classes_))
  blocks.append(b);print(json.dumps({'block':bi,'m0base':b['M0_BASE'],'m0progress':b['M0_PROGRESS'],'m1base':b['M1_BASE'],'m1progress':b['M1_PROGRESS']},ensure_ascii=False),flush=True)
 def sb(n):
  q=[b[n] for b in blocks];return {'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q]))}
 def sm(n):
  q=[b[n] for b in blocks];return {'meanMacroAuc':float(np.mean([x['macroAuc'] for x in q])),'worstMacroAuc':float(np.min([x['macroAuc'] for x in q])),'meanMacroAp':float(np.mean([x['macroAp'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanHandoffRecall':float(np.mean([x['perClassRecall']['HANDOFF_ALLOW'] for x in q])),'meanObserveRecall':float(np.mean([x['perClassRecall']['OBSERVE_NO_EVENT'] for x in q]))}
 s={n:sb(n) for n in ['M0_BASE','M0_PROGRESS']};s.update({n:sm(n) for n in ['M1_BASE','M1_PROGRESS']});a=s['M1_BASE'];p=s['M1_PROGRESS'];m0a=s['M0_BASE'];m0p=s['M0_PROGRESS'];rule1=(p['meanMacroAuc']>=a['meanMacroAuc']+.005 and p['meanLogLoss']<=a['meanLogLoss'] and p['worstMacroAuc']>=a['worstMacroAuc']-.005 and p['meanHandoffRecall']>=a['meanHandoffRecall']);rule2=(p['meanMacroAuc']>a['meanMacroAuc'] and p['meanLogLoss']<a['meanLogLoss'] and m0p['meanAuc']>m0a['meanAuc'] and m0p['meanLogLoss']<m0a['meanLogLoss']);keep=bool(rule1 or rule2)
 art={'version':'R4_MANAGEMENT_RESPONSIBILITY_PROGRESS_PILOT_V0','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'coverage':{'requestedMarkets':120,'eligibleMarkets':int(e.market_id.nunique()),'rows':int(len(e)),'forwardBlocks':len(blocks)},'featuresAdded':PROG,'summary':s,'blocks':blocks,'worthScaleToFull300':keep,'status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED','guards':pre['guards']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'coverage':art['coverage'],'summary':s},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
