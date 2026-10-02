from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
import joblib

ROOT=Path(__file__).resolve().parents[1]
LIFE_DB=ROOT/'data/wallet_maker_book_inference.db'
OFF_DB=ROOT/'data/target_wallet_official_v1.db'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_continue_weak_v0.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_continue_weak_v0_rows.csv'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_continue_weak_v0.joblib'

GEOM=['seconds_left','abs_gap','risk_deficit','coverage']
RESP=GEOM+['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']
FULL=RESP+['mode_age_s','events_5s','events_15s','transitions_15s']

def qdf(db,sql,params=()):
    c=sqlite3.connect(f'file:{db}?mode=ro',uri=True); c.execute('pragma query_only=on')
    d=pd.read_sql_query(sql,c,params=tuple(int(x) if isinstance(x,(np.integer,)) else x for x in params)); c.close(); return d

def hgb(seed):
    return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=40,l2_regularization=1,max_iter=260,random_state=seed)

def met(y,p):
    y=np.asarray(y,int); p=np.asarray(p,float)
    return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}

def ordinary_mask(ms):
    # Explicitly exclude 2026-08-16 Asia/Taipei special-market date.
    tz=timezone(timedelta(hours=8)); return datetime.fromtimestamp(ms/1000,tz).date().isoformat()!='2026-08-16'

def main():
    # fixed recent 300 lifecycle markets, then chronological order by first event time
    mids=qdf(str(LIFE_DB),"select distinct market_id from maker_book_inference_v21_parent_lifecycles where placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70 order by market_id desc limit 120").market_id.astype(int).tolist()
    ph=','.join('?'*len(mids))
    life=qdf(str(LIFE_DB),f"select market_id,order_hash,target_side,placement_first_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({ph}) and placement_first_ms is not null and confidence>=.75 and placement_coverage>=.85 and fill_allocation_coverage>=.70",mids)
    parents=qdf(str(OFF_DB),f"select market_id,parent_id,order_hash,side,first_event_ms,last_event_ms,shares,average_price from target_parent_orders where market_id in ({ph}) and role='MAKER' and quote_type='BID'",mids)
    markets=qdf(str(OFF_DB),f"select market_id,window_end_ms from target_markets where market_id in ({ph})",mids)
    ev=qdf(str(OFF_DB),f"select market_id,role,side,event_ms,price,shares from wallet_shadow_target_events where market_id in ({ph}) and quote_type='BID' order by market_id,event_ms,id",mids)
    parents=parents.merge(markets,on='market_id',how='left')
    life_groups={int(k):g for k,g in life.groupby('market_id')}
    ev_groups={int(k):g.sort_values('event_ms') for k,g in ev.groupby('market_id')}
    raw=[]
    for mid,gp in parents.groupby('market_id'):
        ge=ev_groups.get(int(mid)); gl=life_groups.get(int(mid))
        if ge is None or gl is None: continue
        times=ge.event_ms.to_numpy(np.int64); sides=ge.side.astype(str).to_numpy(); sh=ge.shares.to_numpy(float); px=ge.price.to_numpy(float)
        up=np.cumsum(np.where(sides=='UP',sh,0.)); dn=np.cumsum(np.where(sides=='DOWN',sh,0.)); cost=np.cumsum(sh*px)
        starts=gl.placement_first_ms.to_numpy(np.int64); ends=gl.last_target_ms.fillna(gl.placement_first_ms).to_numpy(np.int64); lsides=gl.target_side.astype(str).to_numpy()
        for r in gp.sort_values('first_event_ms').itertuples():
            t=int(r.first_event_ms)
            if not ordinary_mask(t): continue
            j=np.searchsorted(times,t,side='left')-1
            u=float(up[j]) if j>=0 else 0.; d=float(dn[j]) if j>=0 else 0.; c=float(cost[j]) if j>=0 else 0.
            gap=u-d
            if abs(gap)<1e-9: continue
            dom='UP' if gap>0 else 'DOWN'; weak='DOWN' if dom=='UP' else 'UP'; mode='ADD' if str(r.side)==dom else 'REPAIR'
            active=(starts<=t)&(ends>=t)
            weak_mask=active&(lsides==weak); dom_mask=active&(lsides==dom)
            wa=int(weak_mask.sum()); da=int(dom_mask.sum())
            weak_age=float((t-starts[weak_mask]).max()/1000) if wa else 0.; dom_age=float((t-starts[dom_mask]).max()/1000) if da else 0.
            floor=min(u,d)-c; gross=u+d; coverage=(2*min(u,d)/gross) if gross>0 else 0.; sec=float((r.window_end_ms-t)/1000) if pd.notna(r.window_end_ms) else np.nan
            raw.append({'market_id':int(mid),'t':t,'side':str(r.side),'mode':mode,'weak_side':weak,'dominant_side':dom,'shares':float(r.shares),'seconds_left':sec,'abs_gap':abs(gap),'risk_deficit':max(0.,-floor),'coverage':coverage,'weak_active_owners':wa,'dominant_active_owners':da,'weak_oldest_age_s':weak_age,'dominant_oldest_age_s':dom_age})
    d=pd.DataFrame(raw)
    # collapse same-timestamp parents into one observed management event
    out=[]
    for (mid,t),g in d.groupby(['market_id','t'],sort=True):
        w=np.maximum(g.shares.to_numpy(float),1e-9); w=w/w.sum(); repair=float(np.sum((g['mode'].to_numpy()=='REPAIR')*w));
        row={'market_id':int(mid),'t':int(t),'mode':'REPAIR' if repair>=.5 else 'ADD','weak_side':str(g.weak_side.iloc[0]),'dominant_side':str(g.dominant_side.iloc[0])}
        for c in ['seconds_left','abs_gap','risk_deficit','coverage','weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s']:
            row[c]=float(np.sum(g[c].to_numpy(float)*w))
        out.append(row)
    x=pd.DataFrame(out).sort_values(['market_id','t']).reset_index(drop=True)
    # lifecycle memory + label: same weak-side REPAIR responsibility appears again within 5s
    rows=[]
    for mid,g in x.groupby('market_id',sort=False):
        g=g.sort_values('t').reset_index(drop=True); ts=g.t.to_numpy(np.int64); modes=g['mode'].astype(str).to_numpy(); weak=g.weak_side.astype(str).to_numpy()
        run_start=0
        for i,r in g.iterrows():
            if i==0 or modes[i]!=modes[i-1]: run_start=i
            t=int(ts[i]); lo5=np.searchsorted(ts,t-5000,side='left'); lo15=np.searchsorted(ts,t-15000,side='left')
            transitions=sum(1 for k in range(max(1,lo15),i+1) if modes[k]!=modes[k-1])
            future_end=np.searchsorted(ts,t+5000,side='right')
            cont=0
            if modes[i]=='REPAIR':
                for k in range(i+1,future_end):
                    if modes[k]=='REPAIR' and weak[k]==weak[i]: cont=1; break
            z=r.to_dict(); z.update({'mode_age_s':float((t-ts[run_start])/1000),'events_5s':int(i-lo5+1),'events_15s':int(i-lo15+1),'transitions_15s':int(transitions),'continue_weak_5s':int(cont)})
            rows.append(z)
    z=pd.DataFrame(rows); z=z[(z['mode']=='REPAIR') & z[FULL].notna().all(axis=1)].copy()
    # require enough future horizon within market: drop last event if no 5s observable window
    last=z.groupby('market_id').t.transform('max'); z=z[(last-z.t)>=5000].copy()
    order=z.groupby('market_id').t.min().sort_values().index.astype(int).tolist(); n=len(order); initial=max(80,int(n*.6)); rem=n-initial; sizes=[rem//4]*4
    for i in range(rem%4): sizes[i]+=1
    blocks=[]; cur=initial
    for bi,sz in enumerate(sizes,1):
        trm=order[:cur]; tem=order[cur:cur+sz]; cur+=sz; tr=z[z.market_id.isin(trm)]; te=z[z.market_id.isin(tem)]
        if len(te)==0: continue
        bm={'block':bi,'trainMarkets':len(trm),'testMarkets':tem,'testRows':int(len(te)),'models':{}}
        for j,(name,feats) in enumerate([('GEOMETRY',GEOM),('PLUS_RESPONSIBILITY',RESP),('PLUS_MEMORY',FULL)]):
            m=hgb(20268100+bi*10+j); m.fit(tr[feats],tr.continue_weak_5s); p=m.predict_proba(te[feats])[:,1]; bm['models'][name]=met(te.continue_weak_5s,p)
        blocks.append(bm)
    def summ(name):
        q=[b['models'][name] for b in blocks]; return {'blocks':len(q),'meanAuc':float(np.mean([a['auc'] for a in q])),'worstAuc':float(np.min([a['auc'] for a in q])),'stdAuc':float(np.std([a['auc'] for a in q])),'meanAp':float(np.mean([a['ap'] for a in q])),'worstAp':float(np.min([a['ap'] for a in q])),'meanLogLoss':float(np.mean([a['logLoss'] for a in q])),'worstLogLoss':float(np.max([a['logLoss'] for a in q]))}
    summary={k:summ(k) for k in ['GEOMETRY','PLUS_RESPONSIBILITY','PLUS_MEMORY']}
    # Freeze final research model on first 80% chronology; last 20% remains untouched final holdout.
    cut=max(1,int(n*.8)); train_m=order[:cut]; hold_m=order[cut:]; tr=z[z.market_id.isin(train_m)]; ho=z[z.market_id.isin(hold_m)]
    final=hgb(20268999); final.fit(tr[FULL],tr.continue_weak_5s); hp=final.predict_proba(ho[FULL])[:,1]; hold=met(ho.continue_weak_5s,hp); joblib.dump({'model':final,'features':FULL,'version':'R4_MANAGEMENT_CONTINUE_WEAK_V0_120M','authority':'RESEARCH_ONLY'},MODEL)
    z.to_csv(ROWS,index=False)
    art={'version':'R4_MANAGEMENT_CONTINUE_WEAK_V0_120M','researchOnly':True,'actionAuthority':False,'purpose':'Train first dedicated R4 management head: conditional on an already-active weak-side REPAIR responsibility, predict whether the same weak-side responsibility continues within 5s.','coverage':{'selectedLifecycleMarkets':len(mids),'usableMarkets':int(z.market_id.nunique()),'rows':int(len(z)),'positiveRate':float(z.continue_weak_5s.mean())},'features':{'GEOMETRY':GEOM,'PLUS_RESPONSIBILITY':RESP,'PLUS_MEMORY':FULL},'blocks':blocks,'summary':summary,'frozenModel':{'trainMarkets':len(train_m),'holdoutMarkets':len(hold_m),'holdout':hold,'path':str(MODEL.relative_to(ROOT)).replace('\\','/')},'guards':['Target label is responsibility continuation, not exact next order price/size/action.','Combined inventory uses strict-past MAKER+TAKER BID fills.','Owner lifecycle is inferred Target proxy; runtime analogue must use OUR exact owner ledger + R3.1 lifecycle facts.','2026-08-16 Asia/Taipei excluded.','No winner/settlement/future feature.','No action authority; no threshold sweep.']}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'summary':summary,'frozenModel':art['frozenModel']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
