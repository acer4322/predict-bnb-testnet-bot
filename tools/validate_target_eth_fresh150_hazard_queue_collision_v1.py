from __future__ import annotations
import argparse,json,math,statistics,sqlite3,importlib.util
from pathlib import Path
from collections import defaultdict
import numpy as np, joblib

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('hzv2',HERE/'train_target_eth_first_leg_live_path_break_hazard_v2.py'); hz=importlib.util.module_from_spec(spec);spec.loader.exec_module(hz)
base=hz.base; EPS=hz.EPS

def stats(xs):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return {'n':0,'mean':None,'median':None,'p25':None,'p75':None,'p90':None}
    def q(q):
        z=(len(ys)-1)*q;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
        return ys[lo]*(1-w)+ys[hi]*w
    return {'n':len(ys),'mean':statistics.mean(ys),'median':statistics.median(ys),'p25':q(.25),'p75':q(.75),'p90':q(.9)}

def rate(rows,key):return sum(bool(r.get(key)) for r in rows)/len(rows) if rows else None

def load_first_parent_placements(path):
    d=json.load(open(path,encoding='utf-8')); out={}
    for r in d.get('rows',[]):
        if not r.get('highConfidencePlacement'):continue
        oh=str(r.get('orderHash') or '').lower()
        if not oh:continue
        out[oh]=r
    return out,d

def build_cohort(db_path,placement_path):
    placements,pmeta=load_first_parent_placements(placement_path)
    c=sqlite3.connect(f'file:{Path(db_path).resolve().as_posix()}?mode=ro',uri=True);c.row_factory=sqlite3.Row
    rows=[]
    try:
        mids=[int(r[0]) for r in c.execute("select distinct market_id from maker_book_inference_wallet_events where role='MAKER' and quote_type='BID' order by market_id")]
        for mid in mids:
            ev=[dict(r) for r in c.execute("select rowid rid,market_id,order_hash,side,event_ms,price,shares from maker_book_inference_wallet_events where market_id=? and role='MAKER' and quote_type='BID' and side in ('UP','DOWN') order by event_ms,rowid",(mid,))]
            if not ev:continue
            first_by_hash={}
            for i,e in enumerate(ev):
                oh=str(e.get('order_hash') or '').lower()
                if oh and oh not in first_by_hash:first_by_hash[oh]=i
            up=down=cost=0.0
            for i,e in enumerate(ev):
                oh=str(e.get('order_hash') or '').lower(); sh=float(e['shares']); px=float(e['price']); side=str(e['side']); t=int(e['event_ms'])
                gross=up+down; floor=min(up,down)-cost; absr=abs(up-down)/gross if gross>EPS else 0.0; fr=floor/max(cost,1.0)
                is_parent_first=bool(oh and first_by_hash.get(oh)==i and oh in placements)
                eligible=is_parent_first and gross>EPS and floor>=-EPS and absr<=.02+EPS and fr<.05-EPS
                if eligible:
                    pl=placements[oh]; ready=pl.get('placementCarrierReadyMs')
                    if ready is not None and int(ready)<t:
                        opp='DOWN' if side=='UP' else 'UP'; second=None
                        for g in ev[i+1:]:
                            dt=int(g['event_ms'])-t
                            if dt>30000:break
                            if str(g['side'])==opp and px+float(g['price'])<1.0-EPS:
                                second=g;break
                        rows.append({'marketId':mid,'orderHash':oh,'placementReadyMs':int(ready),'firstEventMs':t,'firstSide':side,'firstPrice':px,'firstShares':sh,'preGross':gross,'preFloor':floor,'preAbsNetRatio':absr,'preFloorRatio':fr,'cheapCompletion30s':second is not None,'secondEventMs':int(second['event_ms']) if second else None,'secondPrice':float(second['price']) if second else None})
                if side=='UP':up+=sh
                else:down+=sh
                cost+=sh*px
    finally:c.close()
    return rows,pmeta

def score_episode(c,model,e):
    ready=int(e['placementReadyMs']);fill=int(e['firstEventMs']);side=str(e['firstSide']);price=float(e['firstPrice'])
    states=base.load_states(c,int(e['marketId']),ready-3500,fill+100,'source_timestamp_ms')
    rr=hz.sample_episode(states,{'marketId':int(e['marketId']),'placementReadyMs':ready,'firstEventMs':fill,'firstSide':side,'firstPrice':price})
    if not rr:return None
    X=np.asarray([[np.nan if v is None else float(v) for v in r['x']] for r in rr],np.float32); pp=model.predict_proba(X)[:,1]
    # Reconstruct same-receipt same-price level decreases independently of the frozen feature rows.
    path=[s for s in states if ready<=s[0]<=fill]; prev=None;drop_times=set();depth_by_t={}
    for t,b,a in path:
        sb=base.side_book(b,a,side,price); lm=hz.path_metric(b,a,side,price)
        if sb is None or lm is None:continue
        cur=float(sb['levelDepth'] or 0.0); depth_by_t[int(t)]=cur
        if prev is not None and cur<prev-EPS:drop_times.add(int(t))
        prev=cur
    hz_warn=[int(r['t']) for r,p in zip(rr,pp) if p>=.5]
    collisions=[int(r['t']) for r,p in zip(rr,pp) if p>=.5 and int(r['t']) in drop_times]
    first_break=rr[0].get('firstBreakAt')
    collision=min(collisions) if collisions else None; warn=min(hz_warn) if hz_warn else None
    return {**e,'stateRows':len(rr),'maxHazard':float(max(pp)),'meanHazard':float(np.mean(pp)),'hazardWarning':warn is not None,'firstHazardWarningAt':warn,'hazardWarningToFirstFillMs':None if warn is None else fill-warn,'collision':collision is not None,'firstCollisionAt':collision,'collisionToFirstFillMs':None if collision is None else fill-collision,'firstPathBreakAt':first_break,'collisionToPathBreakMs':None if collision is None or first_break is None else int(first_break)-collision,'sameReceiptDepthDropRows':len(drop_times)}

def block(rr):
    return {'episodes':len(rr),'markets':len({r['marketId'] for r in rr}),'collisionRate':rate(rr,'collision'),'hazardWarningRate':rate(rr,'hazardWarning'),'maxHazard':stats([r.get('maxHazard') for r in rr]),'collisionToFirstFillMs':stats([r.get('collisionToFirstFillMs') for r in rr]),'collisionToPathBreakMs':stats([r.get('collisionToPathBreakMs') for r in rr]),'placementToFillMs':stats([r['firstEventMs']-r['placementReadyMs'] for r in rr])}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--placements',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    cohort,pmeta=build_cohort(a.db,a.placements); art=joblib.load(a.model); model=art['model'] if isinstance(art,dict) and 'model' in art else art
    c=sqlite3.connect(f'file:{Path(a.db).resolve().as_posix()}?mode=ro',uri=True);sc=[]
    try:
        for i,e in enumerate(cohort,1):
            z=score_episode(c,model,e)
            if z:sc.append(z)
            if i%50==0:print(json.dumps({'scored':i,'of':len(cohort),'resolved':len(sc)}),flush=True)
    finally:c.close()
    pos=[r for r in sc if r['cheapCompletion30s']];neg=[r for r in sc if not r['cheapCompletion30s']]
    cp=sum(r['collision'] for r in pos);cn=sum(r['collision'] for r in neg);np0=len(pos);nn0=len(neg)
    risk_coll=cn/(cp+cn) if cp+cn else None;risk_no=(nn0-cn)/((np0-cp)+(nn0-cn)) if ((np0-cp)+(nn0-cn)) else None
    rr=None if risk_coll is None or risk_no in (None,0) else risk_coll/risk_no
    # Practical collision = at least one full 500ms control cycle before first fill.
    practical=[r for r in sc if r['collision'] and r['collisionToFirstFillMs'] is not None and r['collisionToFirstFillMs']>=500]
    practical_pos=[r for r in practical if r['cheapCompletion30s']];practical_neg=[r for r in practical if not r['cheapCompletion30s']]
    out={'version':'TARGET_ETH_FRESH150_HAZARD_QUEUE_COLLISION_VALIDATION_V1','researchOnly':True,'freshRange':pmeta.get('marketRange'),'placementParents':pmeta.get('parents'),'highConfidencePlacements':pmeta.get('highConfidencePlacements'),'cohortCandidates':len(cohort),'resolvedEpisodes':len(sc),'summary':{'cheapCompletion30s':block(pos),'noCheapCompletion30s':block(neg),'all':block(sc),'collisionNegativeRisk':risk_coll,'noCollisionNegativeRisk':risk_no,'collisionNegativeRiskRatio':rr,'practicalCollisionEpisodes':len(practical),'practicalCollisionCheapCompletion':len(practical_pos),'practicalCollisionNoCheapCompletion':len(practical_neg)},'rows':sc,'boundary':['Fresh chronology-forward Target ETH only; frozen V2 hazard model is never refit.','Collision definition frozen before this run: pBreak>=0.5 AND same scored receipt has first-leg same-price public depth decrease.','Completion label is opposite cheap Maker fill within 30s; it is offline evaluation only.','Public level depth is anonymous and not private queue-rank ground truth.','No OUR outcome/PnL and no Taker action authority.']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,**{k:v for k,v in out.items() if k!='rows'}},ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
