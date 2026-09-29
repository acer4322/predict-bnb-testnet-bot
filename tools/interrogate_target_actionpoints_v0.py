from __future__ import annotations

import bisect, importlib.util, json, math, sqlite3, statistics, sys, os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import joblib, numpy as np, pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
BOOK_DB=ROOT/'data'/'wallet_maker_book_inference.db'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
PUB_DB=ROOT/'data'/'strategy_target_compare_v1.db'
SUFFIX=os.environ.get('INTERROGATE_SUFFIX','')
REPORT=OUT/f'target_actionpoint_interrogation_v0{SUFFIX}_report.json'
MAKER_CSV=OUT/f'target_actionpoint_interrogation_v0{SUFFIX}_maker.csv'
TAKER_CSV=OUT/f'target_actionpoint_interrogation_v0{SUFFIX}_taker.csv'
CONTROL_CSV=OUT/f'target_actionpoint_interrogation_v0{SUFFIX}_controls.csv'
GRID=.01; EPS=1e-9; BASE='UNIFIED_CONTROLLER_PAPER_V1%'

P=ROOT/'tools'/'train_target_maker_taker_coordination_big_v1.py'
spec=importlib.util.spec_from_file_location('coord_interrogate',P); coord=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=coord; spec.loader.exec_module(coord)


def ro(p):
    c=sqlite3.connect(f'file:{p.resolve().as_posix()}?mode=ro',uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def num(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception:return None

def q(xs,p):
    ys=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:return None
    if len(ys)==1:return ys[0]
    z=(len(ys)-1)*p; lo=int(math.floor(z)); hi=int(math.ceil(z)); w=z-lo; return ys[lo]*(1-w)+ys[hi]*w

def stats(xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {'n':len(ys),'mean':statistics.mean(ys) if ys else None,'median':statistics.median(ys) if ys else None,'p25':q(ys,.25),'p75':q(ys,.75),'p90':q(ys,.9)}
def percentile(v,xs):
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not ys or v is None:return None
    return sum(x<=float(v) for x in ys)/len(ys)
def fast_prob(art):
    m=art['model']; fs=list(art['features'])
    if hasattr(m,'_bin_mapper') and hasattr(m,'_predictors'):
        kb,fm=m._bin_mapper.make_known_categories_bitsets(); trees=[it[0] for it in m._predictors]; b0=float(m._baseline_prediction[0,0])
        def pred(raw):
            x=np.asarray([[float(raw.get(f,math.nan)) if raw.get(f) is not None else math.nan for f in fs]],dtype=float); z=b0
            for tr in trees:z+=float(tr.predict(x,known_cat_bitsets=kb,f_idx_map=fm,n_threads=1)[0])
            if z>=0:return 1/(1+math.exp(-z))
            ez=math.exp(z); return ez/(1+ez)
        return pred,fs
    def pred(raw):
        x=pd.DataFrame([{f:raw.get(f,math.nan) for f in fs}]).apply(pd.to_numeric,errors='coerce')
        return float(m.predict_proba(x)[0,1])
    return pred,fs

def multi_probs(art,raw):
    fs=list(art['features']); m=art['model']; x=pd.DataFrame([{f:raw.get(f,math.nan) for f in fs}]).apply(pd.to_numeric,errors='coerce')
    probs=m.predict_proba(x)[0]; return {str(c):float(p) for c,p in zip(m.classes_,probs)},str(m.predict(x)[0])
def load_public(c):
    out=defaultdict(list)
    for r in c.execute('select market_id,coalesce(source_snapshot_ms,decision_ms) ms,public_state_json from our_decisions where strategy_version like ? and public_state_json is not null order by market_id,ms',(BASE,)):
        try:s=json.loads(str(r['public_state_json']))
        except Exception:continue
        if isinstance(s,dict):out[int(r['market_id'])].append((int(r['ms']),s))
    return out
def public_before(rows,ms,max_age=2000):
    if not rows:return None
    ts=[t for t,_ in rows]; i=bisect.bisect_right(ts,ms)-1
    if i<0:return None
    t,s=rows[i]; return (t,s) if 0<=ms-t<=max_age else None


def load_maker_parents_full(c,markets):
    out=defaultdict(list); ids=sorted(markets)
    for st in range(0,len(ids),300):
        b=ids[st:st+300]; qs=','.join('?'*len(b))
        sql=f'''select parent_id,market_id,target_side,target_price,placement_first_ms,placement_last_ms,last_target_ms from maker_book_inference_v21_parent_lifecycles where market_id in ({qs}) and placement_first_ms is not null and placement_supports_18=1 and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75 order by market_id,placement_first_ms,parent_id'''
        for r in c.execute(sql,b):out[int(r['market_id'])].append(dict(r))
    return out

def active_geom(ps,cp,side,public_snap):
    same=[p for p in ps if p['target_side']==side and int(p['placement_first_ms'])<=cp<int(p['last_target_ms'])]
    opp='DOWN' if side=='UP' else 'UP'; other=[p for p in ps if p['target_side']==opp and int(p['placement_first_ms'])<=cp<int(p['last_target_ms'])]
    same.sort(key=lambda p:(int(p['placement_first_ms']),str(p['parent_id'])))
    age=off=None
    if same:
        cur=same[-1]; age=cp-int(cur['placement_first_ms']); bid=num(public_snap.get('predictUpBid') if side=='UP' else public_snap.get('predictDownBid'))
        if bid is None:bid=num(public_snap.get('predict_up_bid') if side=='UP' else public_snap.get('predict_down_bid'))
        if bid is not None:off=(bid-float(cur['target_price']))/GRID
    return len(same),len(other),age,off

def pc_bin(x):
    if x<.4:return 'PC_LT40'
    if x<.65:return 'PC_40_65'
    if x<.8:return 'PC_65_80'
    if x<.9:return 'PC_80_90'
    return 'PC_90_100'
def time_bin(s):
    if s>240:return 'T300_240'
    if s>180:return 'T240_180'
    if s>120:return 'T180_120'
    if s>60:return 'T120_60'
    if s>30:return 'T60_30'
    if s>15:return 'T30_15'
    return 'T15_0'
def rules(side,sec,vol,bias,maker_net,maker_pc,active_same,active_opp,age):
    occupied=active_same>0; context=(age is not None and age<1500) or (15<sec<=60) or vol in ('WATCH','HIGH')
    dom='UP' if maker_net>EPS else 'DOWN' if maker_net<-EPS else None
    headwind=bias in ('UP','DOWN') and bias!=side
    pair80=(not occupied) or (context and not(dom==side and maker_pc<.80))
    pair80_head=(not occupied) or (context and (not(dom==side and maker_pc<.80) or headwind))
    opplive=(not occupied) or (context and active_opp>0)
    return {'oneLiveAllows':int(not occupied),'contextAllows':int((not occupied) or context),'pair80Allows':int(pair80),'pair80HeadwindAllows':int(pair80_head),'oppLiveAllows':int(opplive),'headwind':int(headwind),'contextOn':int(context),'makerDominant':dom}
def summary_by(rows,key,min_n=20):
    d=defaultdict(list)
    for r in rows:d[str(r.get(key))].append(r)
    out={}
    for k,v in sorted(d.items()):
        if len(v)<min_n:continue
        out[k]={'n':len(v),'pMakerChosen':stats([r.get('pMakerChosen') for r in v]),'pTaker1s':stats([r.get('pTaker1s') for r in v]),'scorePercentile':stats([r.get('scorePercentile') for r in v]),
                'pair80HeadwindAllowRate':sum(int(r.get('pair80HeadwindAllows',0)) for r in v)/len(v) if 'pair80HeadwindAllows' in v[0] else None}
    return out

def main():
    up_art=joblib.load(OUT/'target_general_maker_up_core_book_v1.joblib'); dn_art=joblib.load(OUT/'target_general_maker_down_core_book_v1.joblib')
    p_up,_=fast_prob(up_art); p_dn,_=fast_prob(dn_art)
    contract=json.loads((OUT/'forward_contract_v1.json').read_text(encoding='utf-8'))
    th=joblib.load(contract['artifacts']['hazard_1s']); ts=joblib.load(contract['artifacts']['side']); te=joblib.load(contract['artifacts']['effect'])
    p_taker,_=fast_prob(th)
    score_features=set(list(up_art['features'])+list(dn_art['features'])+list(th['features'])+list(ts['features'])+list(te['features']))
    bc=ro(BOOK_DB); tc=ro(TARGET_DB); pc=ro(PUB_DB)
    try:
        public=load_public(pc); meta=coord.load_market_meta(bc); update_markets={int(r[0]) for r in bc.execute('select distinct market_id from maker_book_inference_updates')}; target_markets={int(r[0]) for r in tc.execute("select distinct market_id from wallet_shadow_target_events where asset='BTC' and quote_type='BID'")}
        markets=set(public)&set(meta)&update_markets&target_markets
        ordered_markets=sorted(markets,key=lambda x:int(meta[x]['window_end_ms']))
        st=int(os.environ.get('INTERROGATE_START','0') or 0); ct=int(os.environ.get('INTERROGATE_COUNT','0') or 0)
        selected=ordered_markets[st:(st+ct if ct>0 else None)]; markets=set(selected)
        events=coord.load_events(tc,markets); maker_parents=load_maker_parents_full(bc,markets); takers=coord.load_taker_parents(tc,markets)
        maker_rows=[]; taker_rows=[]; controls=[]; dropped=Counter()
        for mi,m in enumerate(selected,1):
            mend=int(meta[m]['window_end_ms']); mstart=mend-300000; ev=events.get(m,[]); mp=maker_parents.get(m,[]); tp=takers.get(m,[]); pubs=public.get(m,[])
            if not ev or not pubs:continue
            candidates=[]
            for p in mp:
                t=int(p['placement_first_ms']);
                if mstart+1000<=t<=mend-1000:candidates.append({'kind':'MAKER','cp':t-1,'p':p})
            for p in tp:
                t=int(p['first_event_ms']);
                if mstart+1000<=t<=mend-1000:candidates.append({'kind':'TAKER','cp':t-1,'p':p})
            for cp0 in range(mstart+500,mend-4500,1000):candidates.append({'kind':'CONTROL','cp':cp0})
            candidates.sort(key=lambda x:(int(x['cp']),x['kind']))
            inv=coord.Inventory(); ei=0; ci=0; state={'bids':{},'asks':{}}; last_update=None
            for u in bc.execute('select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id',(m,)):
                ut=int(u['source_timestamp_ms'])
                while ci<len(candidates) and int(candidates[ci]['cp'])<ut:
                    c=candidates[ci]; cpms=int(c['cp'])
                    while ei<len(ev) and int(ev[ei]['event_ms'])<=cpms:inv.apply(ev[ei]);ei+=1
                    bage=cpms-last_update if last_update is not None else 10**9
                    psnap=public_before(pubs,cpms,2000)
                    if not(0<=bage<=2000) or psnap is None:
                        dropped['stale_book_or_public']+=1;ci+=1;continue
                    f=inv.features(cpms); cn=float(f.pop('_combined_net')); dom='UP' if cn>EPS else 'DOWN' if cn<-EPS else None; bf=coord.outcome_book(state,dom)
                    if bf is None:dropped['empty_book']+=1;ci+=1;continue
                    raw={'seconds_left':(mend-cpms)/1000.0,**f,**bf}; _,snap=psnap
                    common={'marketId':m,'marketEndMs':mend,'checkpointMs':cpms,'secondsLeft':raw['seconds_left'],'timeBin':time_bin(raw['seconds_left']),'bookAgeMs':bage,
                            'makerNet':float(raw['maker_net']),'makerAbsNet':float(raw['maker_abs_net']),'makerPairedCoverage':float(raw['maker_paired_coverage']),
                            'combinedNet':float(raw['combined_net']),'combinedAbsNet':float(raw['combined_abs_net']),'combinedPairedCoverage':float(raw['combined_paired_coverage']),'lastMakerAgeMs':raw.get('last_maker_age_ms'),'lastTakerAgeMs':raw.get('last_taker_age_ms'),
                            'makerFills5s':raw.get('maker_fills_5s'),'takerFills5s':raw.get('taker_fills_5s'),'directionBias':str(snap.get('directionBias') or 'NEUTRAL').upper(),'volatilityAlert':str(snap.get('volatilityAlert') or 'UNKNOWN').upper(),'directionScore':num(snap.get('directionScore'))}
                    for ff in score_features: common.setdefault(ff,raw.get(ff,math.nan))
                    if c['kind']=='CONTROL':controls.append(common)
                    elif c['kind']=='MAKER':
                        p=c['p']; side=str(p['target_side']); opp='DOWN' if side=='UP' else 'UP'; a_same,a_opp,age,off=active_geom(mp,cpms,side,snap); rr=rules(side,raw['seconds_left'],common['volatilityAlert'],common['directionBias'],float(raw['maker_net']),float(raw['maker_paired_coverage']),a_same,a_opp,age)
                        common.update({'parentId':str(p['parent_id']),'actionSide':side,'activeSameBefore':a_same,'activeOppBefore':a_opp,'activeSameAgeMs':age,'activeSameOffsetTicks':off,
                                       'makerActionType':'SAME_OVERLAP' if a_same>0 else ('FREE_SIDE_WITH_OPP_LIVE' if a_opp>0 else 'FREE_SIDE_EMPTY'),'directionAlignment':'NEUTRAL' if common['directionBias'] not in ('UP','DOWN') else ('TAILWIND' if common['directionBias']==side else 'HEADWIND'),'pairedCoverageBin':pc_bin(float(raw['maker_paired_coverage'])),**rr})
                        maker_rows.append(common)
                    else:
                        p=c['p']; side=str(p['side']); sh=float(p['shares']); effect=coord.effect_label(cn,side,sh); a_same,a_opp,age,off=active_geom(mp,cpms,side,snap); rr=rules(side,raw['seconds_left'],common['volatilityAlert'],common['directionBias'],float(raw['maker_net']),float(raw['maker_paired_coverage']),a_same,a_opp,age)
                        for ff in set(list(ts['features'])+list(te['features'])): common.setdefault(ff,raw.get(ff,math.nan))
                        common.update({'parentId':str(p['parent_id']),'actionSide':side,'actualEffect':effect,'interventionShares':sh,'interventionAvgPrice':float(p['average_price']),
                                       'activeSameBefore':a_same,'activeOppBefore':a_opp,'activeSameAgeMs':age,'activeSameOffsetTicks':off,'directionAlignment':'NEUTRAL' if common['directionBias'] not in ('UP','DOWN') else ('TAILWIND' if common['directionBias']==side else 'HEADWIND'),'pairedCoverageBin':pc_bin(float(raw['maker_paired_coverage'])),**rr})
                        taker_rows.append(common)
                    ci+=1
                if int(u['is_checkpoint']):state={'bids':{float(k):float(v) for k,v in (coord.dec(u['native_bids_z']) or {}).items()},'asks':{float(k):float(v) for k,v in (coord.dec(u['native_asks_z']) or {}).items()}}
                else:coord.apply_changes(state,coord.dec(u['changes_z']) or {})
                last_update=ut
            if mi%50==0:print(json.dumps({'progressMarkets':mi,'makerActions':len(maker_rows),'takerActions':len(taker_rows),'controls':len(controls)}),flush=True)
        # Batch all frozen hazard scores after replay; avoids per-checkpoint tree traversal overhead.
        def batch_binary(rr, art, out_key):
            if not rr:return
            fs=list(art['features']); df=pd.DataFrame(rr); x=df[fs].apply(pd.to_numeric,errors='coerce'); pp=art['model'].predict_proba(x)[:,1]
            for i,v in enumerate(pp): rr[i][out_key]=float(v)
        for rr in (maker_rows,taker_rows,controls):
            batch_binary(rr,up_art,'pMakerUp'); batch_binary(rr,dn_art,'pMakerDown'); batch_binary(rr,th,'pTaker1s')
            for r in rr:r['pMakerMax']=max(float(r['pMakerUp']),float(r['pMakerDown']))
        for r in maker_rows:
            side=str(r['actionSide']); r['pMakerChosen']=r['pMakerUp'] if side=='UP' else r['pMakerDown']; r['pMakerOpp']=r['pMakerDown'] if side=='UP' else r['pMakerUp']
        for r in taker_rows:
            side=str(r['actionSide']); r['pMakerChosen']=r['pMakerUp'] if side=='UP' else r['pMakerDown']; r['pMakerOpp']=r['pMakerDown'] if side=='UP' else r['pMakerUp']
        # Batch SIDE/EFFECT EBM scoring after collecting action states.
        if taker_rows:
            tdf=pd.DataFrame(taker_rows)
            xs=tdf[list(ts['features'])].apply(pd.to_numeric,errors='coerce'); xe=tdf[list(te['features'])].apply(pd.to_numeric,errors='coerce')
            sprob=ts['model'].predict_proba(xs); spred=ts['model'].predict(xs); eprob=te['model'].predict_proba(xe); epred=te['model'].predict(xe)
            sclasses=[str(x) for x in ts['model'].classes_]; eclasses=[str(x) for x in te['model'].classes_]
            for i,r in enumerate(taker_rows):
                side=str(r['actionSide']); eff=str(r['actualEffect']); r['predSide']=str(spred[i]); r['sideCorrect']=int(str(spred[i])==side); r['actualSideProb']=float(sprob[i,sclasses.index(side)]) if side in sclasses else None
                r['predEffect']=str(epred[i]); r['effectCorrect']=int(str(epred[i])==eff); r['actualEffectProb']=float(eprob[i,eclasses.index(eff)]) if eff in eclasses else None
        # market-relative percentiles against non-action control checkpoints.
        c_by=defaultdict(list)
        for r in controls:c_by[int(r['marketId'])].append(r)
        for r in maker_rows:
            cs=c_by.get(int(r['marketId']),[]); side=str(r['actionSide']); vals=[c['pMakerUp'] if side=='UP' else c['pMakerDown'] for c in cs]; r['scorePercentile']=percentile(r['pMakerChosen'],vals)
        for r in taker_rows:
            cs=c_by.get(int(r['marketId']),[]); r['scorePercentile']=percentile(r['pTaker1s'],[c['pTaker1s'] for c in cs])
        occupied=[r for r in maker_rows if int(r['activeSameBefore'])>0]
        def rate(rr,key):return sum(int(x.get(key,0)) for x in rr)/len(rr) if rr else None
        maker_summary={'n':len(maker_rows),'markets':len({r['marketId'] for r in maker_rows}),'occupiedActions':len(occupied),'occupiedRate':len(occupied)/len(maker_rows) if maker_rows else None,
                       'pMakerChosen':stats([r['pMakerChosen'] for r in maker_rows]),'scorePercentile':stats([r['scorePercentile'] for r in maker_rows]),'topQuartileRate':sum((r['scorePercentile'] or 0)>=.75 for r in maker_rows)/len(maker_rows) if maker_rows else None,'topDecileRate':sum((r['scorePercentile'] or 0)>=.90 for r in maker_rows)/len(maker_rows) if maker_rows else None,
                       'occupiedGateRecall':{'ONE_LIVE':rate(occupied,'oneLiveAllows'),'CONTEXT':rate(occupied,'contextAllows'),'PAIR80':rate(occupied,'pair80Allows'),'PAIR80_HEADWIND':rate(occupied,'pair80HeadwindAllows'),'OPP_LIVE':rate(occupied,'oppLiveAllows')},
                       'byActionType':summary_by(maker_rows,'makerActionType'),'occupiedByDirection':summary_by(occupied,'directionAlignment'),'occupiedByPairedCoverage':summary_by(occupied,'pairedCoverageBin')}
        taker_summary={'n':len(taker_rows),'markets':len({r['marketId'] for r in taker_rows}),'pTaker1s':stats([r['pTaker1s'] for r in taker_rows]),'scorePercentile':stats([r['scorePercentile'] for r in taker_rows]),'topQuartileRate':sum((r['scorePercentile'] or 0)>=.75 for r in taker_rows)/len(taker_rows) if taker_rows else None,'topDecileRate':sum((r['scorePercentile'] or 0)>=.90 for r in taker_rows)/len(taker_rows) if taker_rows else None,
                       'sideAccuracy':sum(r['sideCorrect'] for r in taker_rows)/len(taker_rows) if taker_rows else None,'actualSideProb':stats([r['actualSideProb'] for r in taker_rows]),'effectAccuracy':sum(r['effectCorrect'] for r in taker_rows)/len(taker_rows) if taker_rows else None,'actualEffectProb':stats([r['actualEffectProb'] for r in taker_rows]),'byEffect':summary_by(taker_rows,'actualEffect'),'byDirection':summary_by(taker_rows,'directionAlignment')}
        control_summary={'n':len(controls),'markets':len({r['marketId'] for r in controls}),'pMakerMax':stats([r['pMakerMax'] for r in controls]),'pTaker1s':stats([r['pTaker1s'] for r in controls])}
        rep={'reportVersion':'TARGET_ACTIONPOINT_INTERROGATION_V0','researchOnly':True,'liveTradingChanges':False,'runtimeTargetDataAllowed':False,
             'question':'On the real Target historical path, immediately before each Target Maker placement or Taker parent begins, what values do the frozen deployable students and current rule-based controller see?',
             'method':{'actionCheckpoint':'1 ms before inferred high-confidence Maker placement_first_ms or official Target Taker first_event_ms','state':'strict-past Target own MAKER/TAKER fills + reconstructed public 8778 book; nearest <=2s public recorder snapshot only for direction/volatility diagnostics','controls':'1s same-market non-action checkpoints; action score percentile is relative to that market control distribution','models':'frozen General Maker CORE+BOOK UP/DOWN hazards + frozen coordination Taker 1s hazard/SIDE/EFFECT','ruleInterrogation':'ONE_LIVE, CONTEXT, PAIR80, PAIR80_HEADWIND, OPP_LIVE are evaluated as diagnostics only; no action is generated'},
             'chunk':{'start':st,'countRequested':ct,'selectedMarkets':len(selected),'suffix':SUFFIX},'coverage':{'markets':len(markets),'makerActions':len(maker_rows),'takerActions':len(taker_rows),'controls':len(controls),'dropped':dict(dropped)},'makerActions':maker_summary,'takerActions':taker_summary,'controls':control_summary,
             'guards':['This is teacher-forced Target actual-path interrogation, not deployable performance: Target own inventory is intentionally used to understand Target decisions.','No winner or future Target action enters a score; future action is only the event being interrogated.','Maker placement ownership remains inferred/probabilistic.','No threshold sweep or EBM training in this V0.']}
        pd.DataFrame(maker_rows).to_csv(MAKER_CSV,index=False); pd.DataFrame(taker_rows).to_csv(TAKER_CSV,index=False); pd.DataFrame(controls).to_csv(CONTROL_CSV,index=False); REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(rep,ensure_ascii=False,indent=2))
    finally:bc.close();tc.close();pc.close()
if __name__=='__main__':main()
