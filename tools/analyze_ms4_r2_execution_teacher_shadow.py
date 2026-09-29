from __future__ import annotations
import argparse,glob,json,sqlite3
from collections import deque,defaultdict
from pathlib import Path
import numpy as np,joblib
from sklearn.metrics import roc_auc_score,average_precision_score
EPS=1e-9

def metric(y,p):
    y=np.asarray(y,int);p=np.asarray(p,float)
    return {'n':int(len(y)),'positiveRate':float(y.mean()) if len(y) else None,'scoreMean':float(p.mean()) if len(p) else None,
            'auc':float(roc_auc_score(y,p)) if len(y) and len(np.unique(y))>1 else None,
            'ap':float(average_precision_score(y,p)) if len(y) and len(np.unique(y))>1 else None,
            'p10':float(np.quantile(p,.1)) if len(p) else None,'median':float(np.quantile(p,.5)) if len(p) else None,'p90':float(np.quantile(p,.9)) if len(p) else None}

def feat(up,dn,cost,hist,prev_t,t,last_event=None):
    floor=min(up-cost,dn-cost);upside=max(up-cost,dn-cost);ss=abs(up-dn);base=min(up,dn);gross=up+dn;sur='UP' if up>dn+EPS else 'DOWN' if dn>up+EPS else 'FLAT'
    h=list(hist);r5=[x for x in h if t-x[0]<=5000];r15=[x for x in h if t-x[0]<=15000];old5=r5[0] if r5 else (r15[0] if r15 else (t,'','',0.,ss,floor,upside,.5))
    le=last_event or (h[-1] if h else None)
    return {'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':ss,'base_pair_shares':base,
      'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':cost/gross if gross else 0.,
      'last_price':float(le[7]) if le else .5,'last_shares':float(le[3]) if le else 0.,'last_role_taker':1. if le and le[1]=='TAKER' else 0.,
      'age_since_last_ms':0. if prev_t is None else float(max(0,t-prev_t)),'events_5s':float(len(r5)),'events_15s':float(len(r15)),
      'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),
      'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),
      'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),
      'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(floor-old5[5]),'upside_change_5s':float(upside-old5[6]),
      'floor_to_upside_ratio':floor/upside if abs(upside)>EPS else 0.,'floor_per_base_share':floor/base if base>EPS else 0.,'upside_per_surplus_share':upside/ss if ss>EPS else 0.}

def prob(bundle,f):
    X=np.asarray([[float(f.get(k,0.)) for k in bundle['features']]],float);return float(bundle['model'].predict_proba(X)[0,1])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--patterns',required=True);ap.add_argument('--target-db',required=True);ap.add_argument('--hazard-model',required=True);ap.add_argument('--channel-model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    files=[]
    for pat in a.patterns.split(';'):files.extend(glob.glob(pat))
    hz=joblib.load(a.hazard_model);ch=joblib.load(a.channel_model)
    rows=[]
    for p in sorted(set(files)):
      d=json.load(open(p,encoding='utf-8'))
      rows.extend([r for r in d.get('rows',[]) if str(r.get('cell','')).startswith('MS4_R1_')])
    mids=sorted({int(r['marketId']) for r in rows});con=sqlite3.connect(a.target_db);ph=','.join('?'*len(mids));q=con.execute(f"select market_id,role,first_event_ms from target_parent_orders where asset='ETH' and market_id in ({ph}) order by market_id,first_event_ms,parent_id",mids).fetchall();con.close();target=defaultdict(list)
    for m,role,t in q:target[int(m)].append((int(t),str(role).upper()))
    hazard=[];channel=[];per=[]
    for r in sorted(rows,key=lambda z:int(z['marketId'])):
      mid=int(r['marketId']);te=target[mid];taker_times=[t for t,role in te if role=='TAKER'];up=dn=cost=0.;hist=deque();prev_t=None;hm=[];cm=[]
      for ev in r.get('slotHistory',[]):
        typ=ev.get('event');t=int(ev.get('t') or 0)
        while hist and t-hist[0][0]>15000:hist.popleft()
        if typ=='ROLE_FILL_SPLIT':
          side=str(ev.get('side')).upper();px=float(ev.get('price') or 0);sh=float(ev.get('fillInc') or 0)
          if sh<=EPS:continue
          if side=='UP':up+=sh
          else:dn+=sh
          cost+=px*sh;floor=min(up-cost,dn-cost);ups=max(up-cost,dn-cost);ss=abs(up-dn)
          he=(t,'MAKER',side,sh,ss,floor,ups,px);hist.append(he);f=feat(up,dn,cost,hist,prev_t,t,last_event=he);pact=prob(hz,f);y=int(any(t<tt<=t+5000 for tt in taker_times));rec={'marketId':mid,'t':t,'role':ev.get('role'),'side':side,'price':px,'qty':sh,'pActive5s':pact,'targetActiveWithin5s':y,'floor':floor,'upside':ups};hazard.append(rec);hm.append(rec);prev_t=t
        elif typ=='ROLE_SLOT_SUBMIT':
          f=feat(up,dn,cost,hist,prev_t,t);pc=prob(ch,f);nxt=next(((tt,rr) for tt,rr in te if tt>=t),None)
          if nxt is None:continue
          y=int(nxt[1]=='TAKER');rec={'marketId':mid,'t':t,'ms4Role':ev.get('role'),'side':ev.get('side'),'price':float(ev.get('price') or 0),'pChannelTaker':pc,'targetNextParentTaker':y,'targetNextParentDelayMs':int(nxt[0]-t),'floor':f['floor'],'upside':f['upside']};channel.append(rec);cm.append(rec)
      per.append({'marketId':mid,'hazard':metric([x['targetActiveWithin5s'] for x in hm],[x['pActive5s'] for x in hm]),'channel':metric([x['targetNextParentTaker'] for x in cm],[x['pChannelTaker'] for x in cm]),'ourFills':len(hm),'ourSubmitsScored':len(cm),'targetParents':len(te),'targetActiveParents':sum(role=='TAKER' for _,role in te)})
    out={'version':'MS4_R2_ETH_EXECUTION_TEACHER_ON_MS4_R1_FULL24_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,
      'hazardOverall':metric([x['targetActiveWithin5s'] for x in hazard],[x['pActive5s'] for x in hazard]),
      'channelOverall':metric([x['targetNextParentTaker'] for x in channel],[x['pChannelTaker'] for x in channel]),
      'hazardRows':hazard,'channelRows':channel,'perMarket':per,
      'boundary':['MS4 trajectory only; no strategy replay','Target future role used posthoc only as shadow label','Hazard scored after confirmed OUR fills','Channel scored at already-authorized MS4 ROLE_SLOT_SUBMIT opportunities','No Target/winner/settlement runtime input','No action authority']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'hazardOverall':out['hazardOverall'],'channelOverall':out['channelOverall'],'markets':len(mids),'hazardRows':len(hazard),'channelRows':len(channel)},ensure_ascii=False))
if __name__=='__main__':main()
