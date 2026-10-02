from __future__ import annotations
from pathlib import Path
import bisect,datetime as dt,json,lzma,math

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_pathstate_v32_2_features_unlabeled.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_carrier_queuepath_v32_3_features_unlabeled.json'
EPS=1e-9
LOOKBACKS=(1000,3000)

def apply_book(book,chg):
    for k in ('bids','asks'):
        for x in (chg or {}).get(k,[]) or []:
            p=float(x[0]); a=float(x[2])
            if a<=EPS: book[k].pop(p,None)
            else: book[k][p]=a

def parse_match_ms(s):
    try:
        z=dt.datetime.fromisoformat(str(s).replace('Z','+00:00'))
        return int(z.timestamp()*1000)
    except Exception:return None

def maker_amount(x):
    try:return int(str(x))/1e18
    except Exception:
        try:return float(x)/1e18
        except Exception:return 0.0

def maker_price(x):
    try:return int(str(x))/1e18
    except Exception:
        try:return float(x)/1e18
        except Exception:return None

def queue_for(book,side,target_px):
    tp=round(float(target_px)+1e-12,2)
    if side=='UP': return float(book['bids'].get(tp,0.0)),tp,'bid'
    native=round(1.0-tp+1e-12,2)
    return float(book['asks'].get(native,0.0)),native,'ask'

def snapshots_for_updates(updates,query_times,side,prices):
    qs=sorted(set(int(x) for x in query_times))
    out={}
    book={'bids':{},'asks':{}}; i=0
    ups=sorted(updates or [],key=lambda r:(int(r[1]),int(r[0])))
    for qt in qs:
        while i<len(ups) and int(ups[i][1])<=qt:
            u=ups[i];cp=int(u[3]);chg=u[6] or {}
            if cp and u[4] is not None and u[5] is not None:
                book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
            else:apply_book(book,chg)
            i+=1
        out[qt]={name:queue_for(book,side,px) for name,px in prices.items()}
    return out

def match_depletion(matches,side,target_px,lo,hi):
    total=0.0;events=0
    side_name='UP' if side=='UP' else 'DOWN';tp=float(target_px)
    for m in matches or []:
        mt=parse_match_ms(m.get('executedAt'))
        # Conservative strict-past: same-timestamp/second match is excluded.
        if mt is None or mt<lo or mt>=hi:continue
        for mk in m.get('makers') or []:
            on=str(((mk.get('outcome') or {}).get('name') or '')).upper()
            px=maker_price(mk.get('price'))
            if on==side_name and px is not None and abs(px-tp)<.005:
                q=maker_amount(mk.get('amount'));total+=q;events+=1
    return total,events

def enrich_one(x):
    mid=int(x['marketId']);t=int(x['t']);side=str(x['side']).upper()
    prices={'original':float(x['originalPx']),'planned':float(x['plannedSubmitPx'])}
    p=ROOT/'data/execution_tape_v1/markets'/f'{mid}.json.xz'
    with lzma.open(p,'rt',encoding='utf-8') as f:d=json.load(f)
    qtimes=[t]+[t-lb for lb in LOOKBACKS]
    snaps=snapshots_for_updates(d.get('updates') or [],qtimes,side,prices)
    feat={}
    for name,px in prices.items():
        per={}
        seamq,native,bookside=snaps[t][name]
        for lb in LOOKBACKS:
            startq,_,_=snaps[t-lb][name]
            dep,dep_events=match_depletion(d.get('matches') or [],side,px,t-lb,t)
            change=seamq-startq; growth=max(0.0,change);net=dep-growth
            denom=startq if startq>EPS else None
            per[str(lb)]={
                'targetPx':px,'nativeBookPx':native,'nativeBookSide':bookside,
                'queueStartShares':startq,'queueSeamShares':seamq,'queueChangeShares':change,
                'queueChangeNorm':None if denom is None else change/denom,
                'startQueuePresent':bool(startq>EPS),
                'matchDepletionShares':dep,'matchDepletionEvents':dep_events,
                'matchDepletionNorm':None if denom is None else dep/denom,
                'positiveQueueGrowthShares':growth,
                'netProgressShares':net,
                'netProgressNorm':None if denom is None else net/denom,
            }
        feat[name]=per
    y={k:v for k,v in x.items() if k not in {'history'}}
    y['queuePath']=feat
    return y

def main():
    src=json.loads(SRC.read_text(encoding='utf-8'));rows=[];errors=[]
    for x in src.get('rows') or []:
        try:rows.append(enrich_one(x))
        except Exception as e:errors.append({'marketId':x.get('marketId'),'error':f'{type(e).__name__}:{e}'})
    rep={'version':'R4_CARRIER_QUEUEPATH_V32_3_FEATURES_UNLABELED','researchOnly':True,'unlabeled':True,'strictPast':True,'winnerUsed':False,'settlementUsed':False,'futureTargetActionUsed':False,'sameTimestampMatchExcluded':True,'source':str(SRC.relative_to(ROOT)),'marketCount':len(set(int(x['marketId']) for x in rows)),'seamCount':len(rows),'errors':errors,'rows':rows}
    OUT.write_text(json.dumps(rep,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT),'marketCount':rep['marketCount'],'seamCount':rep['seamCount'],'errors':len(errors)},ensure_ascii=False))
if __name__=='__main__':main()
