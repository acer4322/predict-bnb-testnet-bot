from __future__ import annotations
import bisect,json,lzma,math,sys
from collections import deque
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_preposition_responsibility_prune_v2 as v2
from tools import test_r4_queue_rest_latency_normalized_v1 as norm
from src.predict_bot.execution_tape_archive_v1 import load_archive

ENTRY=1092; REST=500; LEAD=ENTRY+REST; OUT=ROOT/'data/research/r4_v0/hourly'
ORIG_PREP=v2.prep

def apply(book,chg):
    for k in ('bids','asks'):
        for x in (chg or {}).get(k,[]) or []:
            p=float(x[0]); a=float(x[2])
            if a<=1e-12: book[k].pop(p,None)
            else: book[k][p]=a

def replay_features(mid:int):
    p=ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz'; d=load_archive(p)
    ups=sorted(d.get('updates') or [],key=lambda r:(int(r[0]),int(r[1])))
    book={'bids':{},'asks':{}}; flow=deque(); rows=[]
    for u in ups:
        # Runtime/HFT decisions are receipt-clock aligned. Do not expose exchange-source updates before receivedAtMs.
        t=int(u[1]); cp=int(u[3]); chg=u[6] or {}
        if cp and u[4] is not None and u[5] is not None:
            book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
        else:
            ba=aa=br=ar=0.0
            for x in chg.get('bids',[]) or []:
                dv=float(x[3]); ba+=max(0.0,dv); br+=max(0.0,-dv)
            for x in chg.get('asks',[]) or []:
                dv=float(x[3]); aa+=max(0.0,dv); ar+=max(0.0,-dv)
            apply(book,chg); flow.append((t,ba,aa,br,ar))
        while flow and flow[0][0]<t-1000: flow.popleft()
        if not book['bids'] or not book['asks']: continue
        bp=sorted(book['bids'],reverse=True); ap=sorted(book['asks'])
        top5=sum(book['bids'][x] for x in bp[:5])+sum(book['asks'][x] for x in ap[:5])
        churn=sum(x[1]+x[2]+x[3]+x[4] for x in flow)
        rows.append((t,float(top5),float(churn)))
    return rows

def q(vals,p):
    vals=sorted(vals)
    if not vals:return None
    z=(len(vals)-1)*p; lo=int(z); hi=min(lo+1,len(vals)-1); w=z-lo
    return vals[lo]*(1-w)+vals[hi]*w

def gate_for_time(rows,t,mode):
    ts=[r[0] for r in rows]; j=bisect.bisect_right(ts,t)-1
    if j<0:return False,{'reason':'NO_BOOK'}
    cur=rows[j]; prior=[r for r in rows if t-30000<=r[0]<cur[0]]
    if len(prior)<20:return False,{'reason':'INSUFFICIENT_30S_HISTORY','nPrior':len(prior)}
    q25=q([r[1] for r in prior],.25); med_ch=q([r[2] for r in prior],.50)
    thin=cur[1]<=q25+1e-9; active=cur[2]>=med_ch-1e-9
    ok=thin if mode=='THIN_ONLY' else (thin and active)
    return ok,{'top5Total':cur[1],'priorQ25Top5':q25,'churn1s':cur[2],'priorMedianChurn1s':med_ch,'thin':thin,'active':active,'nPrior':len(prior)}

def simulate_gated(d,mode):
    mid=int(d['marketId']); rows=replay_features(mid); gate_meta=[]
    def patched_prep(dd,meta,lead):
        orders,takers,dec=ORIG_PREP(dd,meta,lead)
        for o in orders:
            original=bool(o.get('modelSupported'))
            ok,g=gate_for_time(rows,int(o['start']),mode)
            o['modelSupported']=bool(original and ok)
            gate_meta.append({'logical':o['logical'],'start':o['start'],'side':o['side'],'originalModelSupported':original,'queueGate':ok,**g})
        return orders,takers,dec
    v2.prep=patched_prep
    try:r=v2.simulate(d,LEAD,'RESP_RELATION')
    finally:v2.prep=ORIG_PREP
    r['queueGateMode']=mode;r['gateCandidates']=len(gate_meta);r['gatePass']=sum(x['queueGate'] for x in gate_meta);r['gateMeta']=gate_meta
    return r

def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--max-markets',type=int,default=10);ap.add_argument('--market-offset',type=int,default=0);a=ap.parse_args();ds=v2.choose_files(a.max_markets+a.market_offset)[a.market_offset:a.market_offset+a.max_markets]
    rows=[];errors=[]
    for d in ds:
        mid=int(d['marketId'])
        configs=[('REACTIVE_0',lambda:v2.simulate(d,0,'REACTIVE')),('RESP_REST500',lambda:v2.simulate(d,LEAD,'RESP_RELATION')),('QUEUE_THIN_REST500',lambda:simulate_gated(d,'THIN_ONLY')),('QUEUE_THIN_CHURN_REST500',lambda:simulate_gated(d,'THIN_CHURN'))]
        for name,fn in configs:
            try:r=fn();r['config']=name;rows.append(r)
            except Exception as e:errors.append({'marketId':mid,'config':name,'error':f'{type(e).__name__}: {e}'})
        print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errors)},ensure_ascii=False),flush=True)
    names=['REACTIVE_0','RESP_REST500','QUEUE_THIN_REST500','QUEUE_THIN_CHURN_REST500']
    agg={n:norm.agg_rows([r for r in rows if r['config']==n]) for n in names}
    paired={n:norm.paired(rows,n) for n in names if n!='REACTIVE_0'}
    gates={n:{'candidates':sum(r.get('gateCandidates',0) for r in rows if r['config']==n),'pass':sum(r.get('gatePass',0) for r in rows if r['config']==n)} for n in names if n.startswith('QUEUE_')}
    rep={'version':'R4_QUEUE_OPPORTUNITY_PREARM_GATE_V1','researchOnly':True,'hypothesis':'Target-like prearm should be allowed preferentially when strict-past public queue geometry indicates short-horizon queue opportunity, rather than on every future Maker need.','preRegisteredGates':{'THIN_ONLY':'current two-sided top5 total depth <= 25th percentile of same-market strict-past 30s archive depth','THIN_CHURN':'THIN_ONLY and current 1s total add+remove churn >= same-market strict-past 30s median'},'guards':{'entryLatencyMs':ENTRY,'trueExchangeRestMs':REST,'noDreamFill':True,'executionTapeV1':True,'trueMatches':True,'queueModel':'risk','winnerUsed':False,'liveTradingChanges':False,'gateUsesOnlyStrictPastL2':True,'gateClock':'receivedAtMs aligned to controller/HFT replay; sourceTimestampMs forbidden for runtime gate','gateThresholdsDerivedFromTargetHazardResearchNotHftOutcome':True,'futureFrozenOrderTimingPriceSideStillUsedAsExecutionAnchor':True,'runtimePromotionEvidence':False},'aggregate':agg,'pairedVsReactive':paired,'gateCounts':gates,'errors':errors,'rows':rows}
    OUT.mkdir(parents=True,exist_ok=True);p=OUT/f'r4_queue_opportunity_prearm_gate_v1_o{a.market_offset}_m{a.max_markets}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':agg,'pairedVsReactive':paired,'gateCounts':gates,'errors':errors[:5]},ensure_ascii=False))
if __name__=='__main__':main()
