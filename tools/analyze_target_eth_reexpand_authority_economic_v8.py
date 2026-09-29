from __future__ import annotations
import argparse,json,sqlite3,math
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score
from sklearn.isotonic import IsotonicRegression

FEATURES=['repair_progress','debt_ratio','paircov','floor_ratio','best_pnl_ratio','gap_ratio','ms_since_expand','last_transition_expand','weak_avg_cost','dom_avg_cost']
EPS=1e-9

def build_rows(db):
    c=sqlite3.connect(db);c.row_factory=sqlite3.Row
    ends={int(r['market_id']):int(r['window_end_ms']) for r in c.execute("select market_id,window_end_ms from target_markets where asset='ETH' and window_end_ms is not null")}
    rows=list(c.execute("select parent_id,market_id,side,first_event_ms,average_price,shares from target_parent_orders where asset='ETH' and role='MAKER' order by market_id,first_event_ms,parent_id"));c.close()
    out=[];cur=None;u=d=cost=cu=cd=0.0;debt=base=0.0;last_expand_t=None;last_tr=0
    for r in rows:
        mid=int(r['market_id'])
        if mid!=cur:
            cur=mid;u=d=cost=cu=cd=0.0;debt=base=0.0;last_expand_t=None;last_tr=0
        sh=float(r['shares']);px=float(r['average_price']);t=int(r['first_event_ms']);pre_gap=abs(u-d);gross=u+d;paired=min(u,d)
        if debt>EPS and base>EPS and gross>EPS:
            progress=max(0.0,min(1.0,(base-debt)/base));paircov=2*paired/gross;gap_ratio=pre_gap/gross;floor=paired-cost;best=max(u,d)-cost
            weak='UP' if u<d-EPS else 'DOWN' if d<u-EPS else None;dom='DOWN' if weak=='UP' else 'UP' if weak=='DOWN' else None
            au=cu/u if u>EPS else 0.0;ad=cd/d if d>EPS else 0.0;wav=au if weak=='UP' else ad if weak=='DOWN' else 0.0;dav=ad if dom=='DOWN' else au if dom=='UP' else 0.0
            end=ends.get(mid);seconds_left=(end-t)/1000.0 if end else 0.0
            x=[progress,debt/max(gross,1.0),paircov,floor/max(cost,1.0),best/max(cost,1.0),gap_ratio,float(t-(last_expand_t or t)),1.0 if last_tr>0 else 0.0,wav,dav]
            side=str(r['side']);post_u=u+sh if side=='UP' else u;post_d=d+sh if side=='DOWN' else d;delta=abs(post_u-post_d)-pre_gap
            if abs(delta)>EPS: out.append({'marketId':mid,'t':t,'end':end,'secondsLeft':seconds_left,'x':x,'y':1 if delta>0 else 0})
        side=str(r['side'])
        if side=='UP':u+=sh;cu+=sh*px
        else:d+=sh;cd+=sh*px
        cost+=sh*px;post_gap=abs(u-d);delta=post_gap-pre_gap
        if delta>EPS:
            debt=max(0.0,debt)+delta;base=debt;last_expand_t=t;last_tr=1
        elif delta<-EPS and debt>EPS:
            debt=max(0.0,debt-(-delta));last_tr=-1
            if debt<=EPS:debt=base=0.0;last_expand_t=None
        elif abs(delta)>EPS:last_tr=-1 if delta<0 else 1
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();rows=build_rows(a.db)
    ends=sorted(set(r['end'] for r in rows if r['end'] is not None));cut=ends[int(len(ends)*.70)]
    tr=[r for r in rows if r['end']<cut];va=[r for r in rows if r['end']>=cut]
    Xtr=np.asarray([r['x'] for r in tr],float);ytr=np.asarray([r['y'] for r in tr],int);Xv=np.asarray([r['x'] for r in va],float);yv=np.asarray([r['y'] for r in va],int)
    iso=IsotonicRegression(y_min=0,y_max=1,increasing=True,out_of_bounds='clip').fit(Xtr[:,0],ytr);p0=iso.predict(Xv[:,0])
    m=HistGradientBoostingClassifier(max_iter=180,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=50,l2_regularization=4.0,class_weight='balanced',random_state=17).fit(Xtr,ytr);p=m.predict_proba(Xv)[:,1]
    res={'version':'TARGET_ETH_REEXPAND_AUTHORITY_ECONOMIC_V8','features':FEATURES,'rows':len(rows),'trainN':len(tr),'validationN':len(va),'cutoffWindowEndMs':cut,'positiveRateTrain':float(ytr.mean()),'positiveRateValidation':float(yv.mean()),'progressOnly':{'auc':float(roc_auc_score(yv,p0)),'ap':float(average_precision_score(yv,p0))},'economicMemory':{'auc':float(roc_auc_score(yv,p)),'ap':float(average_precision_score(yv,p))},'deltaAuc':float(roc_auc_score(yv,p)-roc_auc_score(yv,p0))}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(res,indent=2),encoding='utf-8');print(json.dumps(res),flush=True)
if __name__=='__main__':main()
