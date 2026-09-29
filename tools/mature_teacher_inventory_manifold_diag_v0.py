from __future__ import annotations
import json, sqlite3, statistics
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OUR=OUT/'r2_candidate_v2_ourloss_targetwin21_replay_v1.json'
TDB=ROOT/'data/target_wallet_official_v1.db'
EPS=1e-9


def bal(u,d):
    s=u+d
    return 0.0 if s<=EPS else 2.0*min(u,d)/s


def classify(prev_u,prev_d,side,qty):
    b0=bal(prev_u,prev_d)
    u=prev_u+(qty if side=='UP' else 0.0)
    d=prev_d+(qty if side=='DOWN' else 0.0)
    b1=bal(u,d)
    if b1>b0+1e-12:return 'IMPROVE',b0,b1,u,d
    if b1<b0-1e-12:return 'WORSEN',b0,b1,u,d
    return 'FLAT',b0,b1,u,d


def summarize(seq):
    u=d=0.0; rows=[]
    for ev in seq:
        c,b0,b1,u,d=classify(u,d,ev['side'],float(ev['shares']))
        rows.append({**ev,'balanceBefore':b0,'balanceAfter':b1,'effect':c,'upAfter':u,'downAfter':d})
    bands={}
    for thr in (0.5,0.7,0.8,0.9):
        xs=[r for r in rows if r['balanceBefore']>=thr-1e-12]
        n=len(xs); imp=sum(r['effect']=='IMPROVE' for r in xs); wor=sum(r['effect']=='WORSEN' for r in xs)
        bands[str(thr)]={'events':n,'improve':imp,'worsen':wor,'improveRate':imp/n if n else None,'worsenRate':wor/n if n else None,'meanDelta':sum(r['balanceAfter']-r['balanceBefore'] for r in xs)/n if n else None}
    return {'finalBalance':bal(u,d),'rows':rows,'bands':bands}


def main():
    rep=json.loads(OUR.read_text(encoding='utf-8'))
    mids=[int(r['marketId']) for r in rep['rows']]
    our={}
    for r in rep['rows']:
        seq=[{'atMs':int(x['atMs']),'side':x['side'],'shares':float(x['deltaShares']),'price':float(x['price'])} for x in r.get('makerFills',[])]
        seq.sort(key=lambda x:(x['atMs'],x['side']))
        our[int(r['marketId'])]=summarize(seq)
    con=sqlite3.connect(TDB)
    target={}
    for mid in mids:
        rs=con.execute("select first_event_ms,side,shares,average_price,parent_id from target_parent_orders where market_id=? and role='MAKER' order by first_event_ms,parent_id",(mid,)).fetchall()
        seq=[{'atMs':int(t),'side':str(s),'shares':float(q),'price':float(p),'parentId':pid} for t,s,q,p,pid in rs]
        target[mid]=summarize(seq)
    con.close()
    def agg(src):
        out={}
        for thr in (0.5,0.7,0.8,0.9):
            k=str(thr); n=imp=wor=0; ds=[]
            for m in mids:
                b=src[m]['bands'][k]; n+=b['events']; imp+=b['improve']; wor+=b['worsen']
                if b['events']:
                    ds.extend([r['balanceAfter']-r['balanceBefore'] for r in src[m]['rows'] if r['balanceBefore']>=thr-1e-12])
            out[k]={'events':n,'improve':imp,'worsen':wor,'improveRate':imp/n if n else None,'worsenRate':wor/n if n else None,'meanDelta':sum(ds)/len(ds) if ds else None}
        out['finalBalanceMedian']=statistics.median(src[m]['finalBalance'] for m in mids)
        return out
    result={'version':'MATURE_TEACHER_INVENTORY_MANIFOLD_DIAG_V0','researchOnly':True,'dreamFillUsed':False,'targetUsedAsRuntimeInput':False,'markets':mids,'ourAggregate':agg(our),'targetAggregate':agg(target),'perMarket':[{ 'marketId':m,'ourFinalBalance':our[m]['finalBalance'],'targetFinalBalance':target[m]['finalBalance'],'ourBands':our[m]['bands'],'targetBands':target[m]['bands']} for m in mids]}
    out=OUT/'mature_teacher_inventory_manifold_diag_v0.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(out),'ourAggregate':result['ourAggregate'],'targetAggregate':result['targetAggregate']},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
