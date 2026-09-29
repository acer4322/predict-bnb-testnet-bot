from __future__ import annotations
import json, importlib.util, sys
from collections import Counter,defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_marginal_pair_quality_replication_v2.py'
spec=importlib.util.spec_from_file_location('r4_mpq_rep_v2_h',P); m=importlib.util.module_from_spec(spec); assert spec and spec.loader
sys.modules[spec.name]=m; spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei'); VERSION='R4_MARGINAL_PAIR_QUALITY_HORIZON_V1'
HORIZONS=[5000,15000,30000,60000]


def apply(state,z):
    up,down,cu,cd=state; sh=float(z['sh']); px=float(z['px'])
    if z['side']=='UP': up+=sh; cu+=sh*px
    else: down+=sh; cd+=sh*px
    return up,down,cu,cd


def g(state): return m.geom(*state)


def path_from(pre, events, start_i, include_flag, horizon_ms=None):
    s=pre; t0=int(events[start_i]['t'])
    for j in range(start_i,len(events)):
        z=events[j]
        if horizon_ms is not None and int(z['t'])>t0+horizon_ms: break
        if j==start_i and not include_flag: continue
        s=apply(s,z)
    return g(s)


def main():
    meta,ev=m.load(m.N_MARKETS); winner={mid:w for mid,_,w in meta}; recs=[]; roles=Counter(); relations=Counter()
    for mid,wend,w in meta:
        events=ev.get(mid,[]); state=(0.,0.,0.,0.)
        for i,z in enumerate(events):
            pre=g(state); post_state=apply(state,z); post=g(post_state)
            reserve=max(0.,pre['floor']-post['floor'])
            flag=pre['floor']>0 and reserve>1e-12 and post['edge']<0
            if flag:
                if pre['up']>pre['down']: surplus='UP'
                elif pre['down']>pre['up']: surplus='DOWN'
                else: surplus='FLAT'
                relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
                roles[z['role']]+=1; relations[relation]+=1
                r={'marketId':mid,'eventIndex':i,'role':z['role'],'side':z['side'],'relation':relation,'price':z['px'],'shares':z['sh'],'preFloor':pre['floor'],'postFloor':post['floor'],'reserveSpent':reserve,'preEdge':pre['edge'],'postEdge':post['edge']}
                for h in HORIZONS:
                    b=path_from(state,events,i,True,h); cf=path_from(state,events,i,False,h)
                    r[f'deltaFloor{h//1000}s']=cf['floor']-b['floor']; r[f'deltaCoverage{h//1000}s']=cf['coverage']-b['coverage']; r[f'deltaAbsNet{h//1000}s']=cf['absnet']-b['absnet']
                b=path_from(state,events,i,True,None); cf=path_from(state,events,i,False,None)
                r['deltaFloorFinal']=cf['floor']-b['floor']; r['deltaPnlFinal']=( (cf['up'] if w=='UP' else cf['down'])-cf['cost']) - ((b['up'] if w=='UP' else b['down'])-b['cost'])
                recs.append(r)
            state=post_state
    def sm(k,rs=recs):
        a=[r[k] for r in rs]; return {'n':len(a),'median':median(a) if a else None,'mean':sum(a)/len(a) if a else None,'positive':sum(x>1e-9 for x in a),'negative':sum(x<-1e-9 for x in a),'zero':sum(abs(x)<=1e-9 for x in a)}
    byrel={rel:{f'floor{h//1000}s':sm(f'deltaFloor{h//1000}s',[r for r in recs if r['relation']==rel]) for h in HORIZONS} | {'floorFinal':sm('deltaFloorFinal',[r for r in recs if r['relation']==rel]),'pnlFinal':sm('deltaPnlFinal',[r for r in recs if r['relation']==rel])} for rel in relations}
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'cohort':{'ordinaryMarkets':len(meta),'flaggedEvents':len(recs),'sealed20260816':True},'roles':dict(roles),'relations':dict(relations),'horizons':{f'floor{h//1000}s':sm(f'deltaFloor{h//1000}s') for h in HORIZONS},'finalFloor':sm('deltaFloorFinal'),'finalPnl':sm('deltaPnlFinal'),'byRelation':byrel,'records':recs,'guards':{'singleFlagRemovalOnly':True,'subsequentTargetPathFixed':True,'noEchtgeldTraining':True,'winnerEvaluationOnly':True}}
    out=ROOT/'data'/'research'/'r4_v0'/'hourly'/f"r4_marginal_pair_quality_horizon_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json"; out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'roles':dict(roles),'relations':dict(relations),'horizons':report['horizons'],'finalFloor':report['finalFloor'],'finalPnl':report['finalPnl']},ensure_ascii=False))

if __name__=='__main__': main()
