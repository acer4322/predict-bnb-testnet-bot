from __future__ import annotations
import argparse, json, lzma, math, os, tempfile, zipfile
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
H3D=BASE/'GPT6_H3D_RECOGNITION_ACTION_VALUE_SELECTOR_DEV41_RESULT_20260907.json'
BUNDLE=BASE/'v16_consumed_holdout100_bundle.zip'
OUT=BASE/'GPT6_H3E_STRICTPAST_QUEUE_MICROSTATE_SELECTOR_DEV41_RESULT_20260907.json'
EPS=1e-9
QUEUE=['candidateLevelPresent','candidateLevelDepth','candidateDepthToOrderQty','nativeGapFromBestTicks','levelsAheadCount','levelsAheadDepth','sameSideBestDepth','sameSideSecondDepth','oppositeBestDepth','topDepthImbalance','latestOrderCount']
BOOK=['imbalance','spread']
LIFE=['repairProgressFrac','remainingDebtQty','initialDebtQty','liveSlots','freeSlots','liveRepairSlots','liveExpandSlots','repairQtyOverDebt']
ECON=['floor','best','gap','repairPrice','expandPrice','pairPriceSum','inventoryGap','cost']
FAMILIES={
 'QUEUE_ONLY':QUEUE,
 'BOOK_PLUS_QUEUE':BOOK+QUEUE,
 'LIFECYCLE_PLUS_QUEUE':LIFE+QUEUE,
 'ALL_PLUS_QUEUE':LIFE+ECON+BOOK+QUEUE,
}

def f(x,d=0.0):
    try:return float(x)
    except:return float(d)

def k(x):return round(float(x),10)

def apply(book,u):
    if int(u[3]):
        book['bids']={k(a):float(b) for a,b in (u[4] or {}).items()}
        book['asks']={k(a):float(b) for a,b in (u[5] or {}).items()}
        return
    for side in ('bids','asks'):
        for r in (u[6] or {}).get(side,[]) or []:
            p=k(r[0]); after=float(r[2])
            if after<=EPS:book[side].pop(p,None)
            else:book[side][p]=after

def qfeat(payload,state):
    t=int(state['t'])
    ups=sorted(payload['updates'],key=lambda u:(int(u[1]),int(u[0])))
    book={'bids':{},'asks':{}}; latest_oc=0.0; seen=0
    for u in ups:
        if int(u[1])>t: break
        apply(book,u); latest_oc=f(u[2]); seen+=1
    if not seen or not book['bids'] or not book['asks']:
        raise RuntimeError(f"book reconstruction missing at market={state['marketId']} t={t}")
    rc=state.get('repairCandidate') or {}
    side=str(rc.get('side') or state.get('weakSide') or '').upper(); price=f(rc.get('price')); qty=f(rc.get('qty'))
    if side=='UP': native_side='bids'; native_price=k(price); opposite='asks'; ordered=sorted(book['bids'],reverse=True); best=minmax=max
    elif side=='DOWN': native_side='asks'; native_price=k(1.0-price); opposite='bids'; ordered=sorted(book['asks']); best=min
    else: raise RuntimeError(f"bad side {side}")
    same=book[native_side]; opp=book[opposite]
    same_best=(max(same) if native_side=='bids' else min(same)); same_best_depth=f(same.get(k(same_best)))
    same_second_depth=0.0
    if len(ordered)>1:same_second_depth=f(same.get(k(ordered[1])))
    opp_best=(min(opp) if opposite=='asks' else max(opp)); opp_best_depth=f(opp.get(k(opp_best)))
    depth=f(same.get(native_price,0.0)); present=1.0 if native_price in same and depth>EPS else 0.0
    if native_side=='bids':
        ahead=[p for p in same if p>native_price+EPS]
        gap=(same_best-native_price)/0.01
    else:
        ahead=[p for p in same if p<native_price-EPS]
        gap=(native_price-same_best)/0.01
    den=same_best_depth+opp_best_depth
    return {
      'candidateLevelPresent':present,
      'candidateLevelDepth':depth,
      'candidateDepthToOrderQty':depth/max(qty,EPS),
      'nativeGapFromBestTicks':float(gap),
      'levelsAheadCount':float(len(ahead)),
      'levelsAheadDepth':sum(f(same[p]) for p in ahead),
      'sameSideBestDepth':same_best_depth,
      'sameSideSecondDepth':same_second_depth,
      'oppositeBestDepth':opp_best_depth,
      'topDepthImbalance':(same_best_depth-opp_best_depth)/den if den>EPS else 0.0,
      'latestOrderCount':latest_oc,
      '_nativeSide':native_side,'_nativePrice':native_price,'_sameBest':same_best,'_oppositeBest':opp_best,'_updatesSeen':seen,
    }

def model():return make_pipeline(StandardScaler(),LogisticRegression(C=1.0,solver='liblinear',max_iter=2000))
def auc(y,p):return float(roc_auc_score(y,p)) if len(set(y))>1 else None

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--h3d',default=str(H3D));ap.add_argument('--bundle',default=str(BUNDLE));ap.add_argument('--output',default=str(OUT));a=ap.parse_args()
    h3d=json.loads(Path(a.h3d).read_text(encoding='utf-8')); rows=h3d['rows']
    mids={int(r['marketId']) for r in rows}
    qmap={}
    with zipfile.ZipFile(a.bundle) as z:
        for r in rows:
            mid=int(r['marketId']); payload=json.loads(lzma.decompress(z.read(f'tapes/{mid}.json.xz')).decode('utf-8'))
            # H3D rows do not duplicate the raw state; recover from the fixed state files.
            qmap[mid]=payload
    state_names=['GPT6_H3B_CORE_ROLE_RESERVATION_SMOKE5_STATES_20260907.json','GPT6_H3B_CORE_ROLE_RESERVATION_STAGEB16_STATES_20260907.json','GPT6_H3C_RECOGNITION_ACTION_VALUE_DEV20_STATES_20260907.json']
    state_files=[(HERE/n if (HERE/n).exists() else BASE/n) for n in state_names]
    states={}
    for p in state_files:
        for s in json.loads(p.read_text(encoding='utf-8'))['states']:states[int(s['marketId'])]=s
    enriched=[]
    for r in rows:
        mid=int(r['marketId']); base=dict(r['features']); q=qfeat(qmap[mid],states[mid]); feat={**base,**{kk:vv for kk,vv in q.items() if not kk.startswith('_')}}
        enriched.append({**r,'queueFeatures':q,'allFeatures':feat})
    material=[r for r in enriched if bool(r['material'])]
    y=np.array([1 if r['onFillBetter'] else 0 for r in material],dtype=int)
    out={'version':'GPT6_H3E_STRICTPAST_QUEUE_MICROSTATE_SELECTOR_DEV41_V1_20260907','researchOnly':True,'runtimeAuthority':False,'n':len(enriched),'materialN':len(material),'families':{},'rows':enriched,
         'invariants':{'all41Present':len(enriched)==41,'material20':len(material)==20,'allStatePresent':all(int(r['marketId']) in states for r in enriched)},
         'boundary':['strict-past tape updates through exact decision t only','no future fill/depletion','no age or fixed-time-window feature','winner only inherited as H3D post-hoc scoring label, never feature','fixed L2 logistic C=1 threshold=.5 LOMO','sealed Holdout25 unopened','no 8781/no NEW24-B/no dream fill']}
    for fam,cols in FAMILIES.items():
        probs=[]
        for i,r in enumerate(material):
            tr=[j for j in range(len(material)) if j!=i]
            X=np.array([[material[j]['allFeatures'][c] for c in cols] for j in tr],dtype=float); yy=y[tr]
            clf=model();clf.fit(X,yy)
            probs.append(float(clf.predict_proba(np.array([[r['allFeatures'][c] for c in cols]],dtype=float))[0,1]))
        correct=sum((p>=.5)==bool(lbl) for p,lbl in zip(probs,y))
        # Scores for equal rows are diagnostic only; fit on all material development rows.
        clf=model();clf.fit(np.array([[r['allFeatures'][c] for c in cols] for r in material],dtype=float),y)
        pmap={int(r['marketId']):p for r,p in zip(material,probs)}
        for r in enriched:
            if int(r['marketId']) not in pmap:pmap[int(r['marketId'])]=float(clf.predict_proba(np.array([[r['allFeatures'][c] for c in cols]],dtype=float))[0,1])
        selected=[]
        for r in enriched:
            choose=pmap[int(r['marketId'])]>=.5
            term=r['onFillTerminal'] if choose else r['immediateTerminal']; pnl=r['pnlOnFill'] if choose else r['pnlImmediate']
            selected.append({'marketId':int(r['marketId']),'chooseOnFill':bool(choose),'pOnFillBetter':pmap[int(r['marketId'])],'selectedPnl':pnl,'baselinePnl':r['pnlImmediate'],'deltaVsImmediate':pnl-r['pnlImmediate'],'floor':f(term['floor']),'best':f(term['best']),'shape':f(term['best'])>2 and f(term['floor'])>-1})
        biggest=max(enriched,key=lambda r:r['deltaPnlOnFillMinusImmediate'])['marketId']
        sel_delta=sum(x['deltaVsImmediate'] for x in selected); without=sum(x['deltaVsImmediate'] for x in selected if x['marketId']!=biggest)
        out['families'][fam]={'features':cols,'valueDirection':{'correct':int(correct),'n':len(material),'accuracy':correct/len(material),'auc':auc(y,probs),'probabilities':[{'marketId':int(r['marketId']),'pOnFillBetterOOF':p,'labelOnFillBetter':bool(lbl)} for r,p,lbl in zip(material,probs,y)]},
          'selector':{'deltaTotalPnl':sel_delta,'improvedMarkets':sum(x['deltaVsImmediate']>EPS for x in selected),'equalMarkets':sum(abs(x['deltaVsImmediate'])<=EPS for x in selected),'worseMarkets':sum(x['deltaVsImmediate']<-EPS for x in selected),'worstMarketDelta':min(x['deltaVsImmediate'] for x in selected),'bestMarketDelta':max(x['deltaVsImmediate'] for x in selected),'selectedShapeCount':sum(x['shape'] for x in selected),'selectedFloorSafeCount':sum(x['floor']>-1 for x in selected),'selectedBestGt2Count':sum(x['best']>2 for x in selected),'largestPositiveDeltaMarket':int(biggest),'deltaPnlWithoutLargestPositiveMarket':without,'rows':selected}}
    passing=[]
    for fam,v in out['families'].items():
        if v['valueDirection']['correct']>=14 and (v['valueDirection']['auc'] or 0)>=.65 and v['selector']['deltaPnlWithoutLargestPositiveMarket']>0:passing.append(fam)
    out['developmentPassingFamilies']=passing
    out['decision']='OPEN_SEALED_HOLDOUT25_FOR_FIXED_QUEUE_FAMILY_CONFIRM' if passing else 'STOP_RECOGNITION_TIMING_SELECTOR_HOLDOUT_REMAINS_SEALED'
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'decision':out['decision'],'passing':passing,'invariants':out['invariants'],'summary':{k:{'valueDirection':{kk:vv for kk,vv in v['valueDirection'].items() if kk!='probabilities'},'selector':{kk:vv for kk,vv in v['selector'].items() if kk!='rows'}} for k,v in out['families'].items()}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
