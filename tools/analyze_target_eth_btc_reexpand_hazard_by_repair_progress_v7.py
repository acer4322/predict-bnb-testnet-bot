from __future__ import annotations
import argparse,json,sqlite3,math,os
from collections import defaultdict
from pathlib import Path
EPS=1e-9
BINS=[('0%',0.0,1e-12),('(0,25%)',1e-12,.25),('[25,50%)',.25,.50),('[50,75%)',.50,.75),('[75,100%]',.75,1.0000001)]

def bin_name(x):
    for n,lo,hi in BINS:
        if lo<=x<hi:return n
    return '[75,100%]'

def run(rows,scope):
    by=defaultdict(list)
    for r in rows:
        if scope=='MAKER_ONLY' and r['role']!='MAKER':continue
        by[(r['asset'],int(r['market_id']))].append(r)
    agg={a:{b[0]:{'n':0,'expand':0,'repair':0,'sumPairCov':0.0,'sumGapRatio':0.0,'sumMsSinceExpand':0.0} for b in BINS} for a in ('BTC','ETH')}
    for (asset,mid),zz in by.items():
        zz=sorted(zz,key=lambda r:(int(r['first_event_ms']),str(r['parent_id'])))
        up=dn=0.0; debt=0.0;base=None;last_expand_t=None
        for r in zz:
            sh=float(r['shares']);side=str(r['side']);t=int(r['first_event_ms'])
            pre_abs=abs(up-dn);gross=up+dn;paircov=2*min(up,dn)/gross if gross>EPS else 0.0;gapratio=pre_abs/gross if gross>EPS else 0.0
            if side=='UP':up+=sh
            else:dn+=sh
            post_abs=abs(up-dn);dabs=post_abs-pre_abs
            if debt>EPS and base is not None and abs(dabs)>EPS:
                progress=max(0.0,min(1.0,(base-debt)/max(base,EPS)));bn=bin_name(progress);z=agg[asset][bn];z['n']+=1;z['sumPairCov']+=paircov;z['sumGapRatio']+=gapratio;z['sumMsSinceExpand']+=(t-last_expand_t if last_expand_t is not None else 0)
                if dabs>0:z['expand']+=1
                else:z['repair']+=1
            if dabs>EPS:
                if debt<=EPS:debt=dabs
                else:debt+=dabs
                base=debt;last_expand_t=t
            elif dabs<-EPS and debt>EPS:
                debt=max(0.0,debt-(-dabs))
                if debt<=EPS:base=None;last_expand_t=None
    out={}
    for a in ('BTC','ETH'):
        out[a]={}
        for n,_,_ in BINS:
            z=agg[a][n];nn=z['n'];out[a][n]={'n':nn,'expandRate':z['expand']/nn if nn else None,'repairRate':z['repair']/nn if nn else None,'meanPairCoverage':z['sumPairCov']/nn if nn else None,'meanGapRatio':z['sumGapRatio']/nn if nn else None,'meanMsSinceExpand':z['sumMsSinceExpand']/nn if nn else None}
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    con=sqlite3.connect(a.db);con.row_factory=sqlite3.Row
    rows=list(con.execute("select parent_id,asset,market_id,role,side,first_event_ms,shares from target_parent_orders where asset in ('BTC','ETH') order by asset,market_id,first_event_ms,parent_id"));con.close()
    scopes={s:run(rows,s) for s in ('ALL_PARENT','MAKER_ONLY')}
    delta={}
    for s in scopes:
        delta[s]={}
        for n,_,_ in BINS:
            b=scopes[s]['BTC'][n]['expandRate'];e=scopes[s]['ETH'][n]['expandRate'];delta[s][n]=None if b is None or e is None else e-b
    out={'version':'TARGET_ETH_BTC_REEXPAND_HAZARD_BY_REPAIR_PROGRESS_V7','researchOnly':True,'sourceDb':os.path.abspath(a.db),'rows':len(rows),'definition':{'progress':'fraction of debt outstanding immediately after the most recent expansion that has been repaid before current material parent action','label':'current material parent action expands abs-net vs repairs abs-net','scope':'actual Target fills only; no inferred placement quantities'},'scopes':scopes,'ETHminusBTCExpandRate':delta}
    p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'scopes':scopes,'delta':delta},ensure_ascii=False))
if __name__=='__main__':main()
