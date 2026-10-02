from __future__ import annotations
import bisect,json,math,glob,sqlite3
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import balanced_accuracy_score,f1_score,roc_auc_score,classification_report,confusion_matrix,log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OUT=ROOT/'data'/'research'/'supervisor_options_v0'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
REPORT=OUT/'student_state_supervisor_v0_report.json'
ART=OUT/'student_state_supervisor_v0.joblib'
ROWS=OUT/'student_state_supervisor_v0_states.csv'
SEED=20260820
CURRENT=[
 'seconds_left','maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage','taker_gross','taker_net','taker_abs_net','taker_imbalance_ratio','taker_paired_coverage','combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap','maker_taker_net_same_sign','last_maker_age_ms','last_taker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms','last_taker_up_age_ms','last_taker_down_age_ms','maker_fills_1s','maker_fills_5s','maker_fills_10s','taker_fills_1s','taker_fills_5s','taker_fills_10s','maker_shares_10s','taker_shares_10s','maker_absnet_change_10s','combined_absnet_change_10s','maker_avg_pair_edge','taker_avg_pair_edge','combined_avg_pair_edge','up_bid','up_ask','up_spread_ticks','up_bid_depth','up_ask_depth','down_bid','down_ask','down_spread_ticks','down_bid_depth','down_ask_depth','pair_bid_edge','pair_ask_edge','dominant_opp_bid_pair_edge','last_place_age_ms','placements_1s','placements_5s','placements_10s','placement_side_balance_5s','placement_side_balance_10s']
MEM_BASE=['maker_net','maker_abs_net','maker_paired_coverage','combined_net','combined_abs_net','combined_paired_coverage','worst_case_floor','best_case_pnl','maker_avg_pair_edge','combined_avg_pair_edge','pair_bid_edge','pair_ask_edge','placements_5s','taker_fills_5s']
LAGS=(3,10,30) # ~6/20/60s after 2s cadence

def numeric(d,cols):return d[cols].apply(pd.to_numeric,errors='coerce')
def weights(y):
 c=y.value_counts(); n=len(y); m={k:float(np.sqrt(n/max(1,int(v)))) for k,v in c.items()}; w=y.map(m).astype(float).to_numpy(); return w/w.mean()
def sample_2s(d):
 out=[]
 for _,g in d.groupby('market_id',sort=False):
  g=g.sort_values('checkpoint_ms'); keep=[]; last=-10**18
  for i,t in zip(g.index,pd.to_numeric(g.checkpoint_ms,errors='coerce').fillna(0).astype('int64')):
   if t-last>=1800:keep.append(i);last=t
  out.append(g.loc[keep])
 return pd.concat(out,ignore_index=True)
def add_memory(d):
 gb=d.groupby('market_id',sort=False); mem={}
 for b in MEM_BASE:
  cur=pd.to_numeric(d[b],errors='coerce')
  for lag in LAGS:mem[f'{b}_delta{lag}s']=cur-pd.to_numeric(gb[b].shift(lag),errors='coerce')
 return pd.concat([d,pd.DataFrame(mem,index=d.index)],axis=1),list(mem)
def build():
 fs=sorted(glob.glob(str(SRC/'target_blind_promoted_controller_closed_loop_v7_residual_on_dev*_states.csv')) + glob.glob(str(SRC/'target_blind_promoted_controller_closed_loop_v7_supervisor_r2_*_states.csv'))); xs=[]
 for f in fs:
  x=pd.read_csv(f).rename(columns={'marketId':'market_id','windowEndMs':'market_end_ms','atMs':'checkpoint_ms'});xs.append(x)
 d=pd.concat(xs,ignore_index=True);d=d[pd.to_numeric(d.seconds_left,errors='coerce').between(3,297,inclusive='both')].copy();d=sample_2s(d);d,mem=add_memory(d)
 # target market id by current read-only book market metadata, never a stale research CSV.
 # Target data remains offline teacher labels only.
 b=sqlite3.connect(f'file:{BOOK_DB.resolve().as_posix()}?mode=ro',uri=True);b.row_factory=sqlite3.Row;b.execute('pragma query_only=on')
 end_to_tm={int(r['window_end_ms']):int(r['market_id']) for r in b.execute('select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null')}
 # exact high-confidence Target maker placements
 placements={}
 try:
  for we in sorted(d.market_end_ms.astype('int64').unique()):
   tm=end_to_tm.get(int(we)); arr=[]
   if tm is not None:
    for r in b.execute("select placement_first_ms,target_side from maker_book_inference_v21_parent_lifecycles where market_id=? and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by placement_first_ms",(tm,)):
     arr.append((int(r['placement_first_ms']),str(r['target_side'])))
   placements[int(we)]=arr
 finally:b.close()
 takers={}
 tdb=sqlite3.connect(f'file:{TARGET_DB.resolve().as_posix()}?mode=ro',uri=True);tdb.row_factory=sqlite3.Row;tdb.execute('pragma query_only=on')
 try:
  for we in sorted(d.market_end_ms.astype('int64').unique()):
   tm=end_to_tm.get(int(we)); arr=[]
   if tm is not None:
    for r in tdb.execute("select first_event_ms,side,shares from target_parent_orders where market_id=? and asset='BTC' and role='TAKER' and quote_type='BID' and first_event_ms is not null order by first_event_ms,parent_id",(tm,)):
     arr.append((int(r['first_event_ms']),str(r['side']),float(r['shares'])))
   takers[int(we)]=arr
 finally:tdb.close()
 mode=[];maker_lbl=[];taker_lbl=[];teacher_side=[]
 for r in d.itertuples(index=False):
  we=int(r.market_end_ms);t=int(r.checkpoint_ms); ps=placements.get(we,[]); pts=[z[0] for z in ps]; i=bisect.bisect_right(pts,t); futp=[]
  while i<len(ps) and 0<ps[i][0]-t<=1000:futp.append(ps[i]);i+=1
  ts=takers.get(we,[]); ttimes=[z[0] for z in ts];j=bisect.bisect_right(ttimes,t); ft=ts[j] if j<len(ts) and 0<ts[j][0]-t<=3000 else None
  m='TAKER' if ft else 'MAKER' if futp else 'HOLD';mode.append(m)
  mn=float(getattr(r,'maker_net') or 0.0); repair_m=(mn>1e-9 and any(s=='DOWN' for _,s in futp)) or (mn<-1e-9 and any(s=='UP' for _,s in futp));maker_lbl.append('PASSIVE_REPAIR' if repair_m else 'NORMAL_MAKER')
  if ft:
   _,side,sh=ft; cn=float(getattr(r,'combined_net') or 0.0); post=cn+(sh if side=='UP' else -sh); rep=abs(post)<abs(cn)-1e-9 if abs(cn)>1e-9 else False;taker_lbl.append('ACTIVE_REPAIR' if rep else 'ACTIVE_OTHER');teacher_side.append(side)
  else:taker_lbl.append('ACTIVE_OTHER');teacher_side.append(None)
 d['teacher_mode']=mode;d['student_maker_gate_label']=maker_lbl;d['student_taker_gate_label']=taker_lbl;d['teacher_taker_side']=teacher_side
 return d,mem,fs

def usable_features(tr,features):
 out=[]
 for f in features:
  z=pd.to_numeric(tr[f],errors='coerce').dropna()
  if len(z)>=2 and z.nunique()>=2:out.append(f)
 return out
def fit_mult(tr,features,label):
 features=usable_features(tr,features)
 m=HistGradientBoostingClassifier(learning_rate=.07,max_iter=90,max_leaf_nodes=15,min_samples_leaf=18,l2_regularization=1,early_stopping=True,validation_fraction=.15,n_iter_no_change=15,random_state=SEED);y=tr[label].astype(str);m.fit(numeric(tr,features),y,sample_weight=weights(y));return m,features
def fit_bin(tr,features,label):return fit_mult(tr,features,label)
def met_multi(m,x,features,label):
 y=x[label].astype(str);X=numeric(x,features);p=m.predict_proba(X);pred=m.predict(X);cl=list(m.classes_);auc=None
 try:auc=float(roc_auc_score(y,p,labels=cl,multi_class='ovr',average='macro'))
 except:pass
 rep=classification_report(y,pred,labels=cl,output_dict=True,zero_division=0)
 return {'n':len(x),'rates':y.value_counts(normalize=True).to_dict(),'predRates':pd.Series(pred).value_counts(normalize=True).to_dict(),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,average='macro')),'macroOvrAuc':auc,'logLoss':float(log_loss(y,p,labels=cl)),'confusion':confusion_matrix(y,pred,labels=cl).tolist(),'perClass':{c:{k:float(rep[c][k]) for k in ('precision','recall','f1-score')} for c in cl}}
def met_bin(m,x,features,label,pos):
 y=x[label].astype(str);X=numeric(x,features);cl=list(m.classes_);pi=cl.index(pos);p=m.predict_proba(X)[:,pi];pred=m.predict(X);yy=y.eq(pos).astype(int);pp=pd.Series(pred,index=y.index).eq(pos).astype(int)
 return {'n':len(x),'rate':float(yy.mean()),'predRate':float(pp.mean()),'auc':float(roc_auc_score(yy,p)) if yy.nunique()>1 else None,'balancedAccuracy':float(balanced_accuracy_score(yy,pp)),'f1':float(f1_score(yy,pp,zero_division=0))}
def main():
 OUT.mkdir(parents=True,exist_ok=True);d,mem,fs=build();markets=d[['market_id','market_end_ms']].drop_duplicates().sort_values(['market_end_ms','market_id']);ids=markets.market_id.astype(int).tolist();a=int(len(ids)*.68);b=int(len(ids)*.84);spl={'train':set(ids[:a]),'validation':set(ids[a:b]),'test':set(ids[b:])};parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in spl.items()};variants={'currentOnly':CURRENT,'currentPlusMemory':CURRENT+mem};results={};mods={}
 for vn,feats in variants.items():
  mm,mf=fit_mult(parts['train'],feats,'teacher_mode');maker_train=parts['train'][parts['train'].teacher_mode.eq('MAKER')];taker_train=parts['train'][parts['train'].teacher_mode.eq('TAKER')];mg,mgf=fit_bin(maker_train,feats,'student_maker_gate_label') if maker_train.student_maker_gate_label.nunique()>1 else (None,[]);tg,tgf=fit_bin(taker_train,feats,'student_taker_gate_label') if taker_train.student_taker_gate_label.nunique()>1 else (None,[]);mods[vn]={'mode':mm,'modeFeatures':mf,'maker':mg,'makerFeatures':mgf,'taker':tg,'takerFeatures':tgf};results[vn]={}
  for s in ('validation','test'):
   z=parts[s];rz={'mode':met_multi(mm,z,mf,'teacher_mode')};zm=z[z.teacher_mode.eq('MAKER')];zt=z[z.teacher_mode.eq('TAKER')];rz['makerGate']=met_bin(mg,zm,mgf,'student_maker_gate_label','PASSIVE_REPAIR') if mg is not None and len(zm) else None;rz['takerGate']=met_bin(tg,zt,tgf,'student_taker_gate_label','ACTIVE_REPAIR') if tg is not None and len(zt) else None;results[vn][s]=rz
 art={'version':'STUDENT_STATE_SUPERVISOR_V0','researchOnly':True,'runtimePromotion':False,'models':mods['currentPlusMemory'],'trainingMarkets':sorted(spl['train']),'memorySemantics':'~6/20/60s strict-past deltas after 2s cadence'};joblib.dump(art,ART);d.to_csv(ROWS,index=False)
 rep={'reportVersion':'STUDENT_STATE_SUPERVISOR_V0','researchOnly':True,'question':'Can Target next high-level action be learned from OUR strict-past portfolio/public state on the same market/time, avoiding Target-state policy collapse?','source':{'markets':int(d.market_id.nunique()),'rows':len(d),'files':[Path(x).name for x in fs],'cohort':'R2 residual ON exposed dev00-24 only'},'teacher':{'modeCounts':d.teacher_mode.value_counts().to_dict(),'makerGateCounts':d[d.teacher_mode.eq('MAKER')].student_maker_gate_label.value_counts().to_dict(),'takerGateCounts':d[d.teacher_mode.eq('TAKER')].student_taker_gate_label.value_counts().to_dict(),'mode':'TAKER if exact Target Taker starts within3s; else MAKER if high-confidence anchored Target Maker placement within1s; else HOLD','conditional':'repair semantics evaluated against OUR current inventory, not Target private inventory'},'splitMarkets':{k:len(v) for k,v in spl.items()},'results':results,'artifact':str(ART),'states':str(ROWS),'decisionRule':'Small-sample bridge only. Proceed to larger consistent-R2 trajectory generation and learning-curve test only if student-state model restores noncollapsed TAKER/MAKER behavior and chronological validation/test are directionally stable. No PnL tuning.','guards':['No winner/PnL.','No special 2026-08-16.','No final75-99.','Target future action only teacher label.','No 8784/8786/Echtgeld changes.']};REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
