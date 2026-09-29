from __future__ import annotations

import importlib.util, json, math, statistics, sys
from pathlib import Path
from typing import Any

import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import average_precision_score, balanced_accuracy_score, roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
PAIR=ROOT/'tools'/'analyze_target_postfill_pair_economics_v1.py'
spec=importlib.util.spec_from_file_location('target_pair_v1_model',PAIR); p=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=p; spec.loader.exec_module(p)
m=p.m
REPORT=ROOT/'data'/'research'/'target_switch_pair_edge_ebm_v1_report.json'
VERSION='TARGET_SWITCH_PAIR_EDGE_EBM_V1'

FEATURE_SETS={
    'BASE_TIME_INV': ['seconds_left','post_abs_net','post_imbalance_ratio','post_paired_coverage','tailwind'],
    'PLUS_MARKOUT': ['seconds_left','post_abs_net','post_imbalance_ratio','post_paired_coverage','tailwind','markout1s_ticks'],
    'PLUS_PAIR_EDGE': ['seconds_left','post_abs_net','post_imbalance_ratio','post_paired_coverage','tailwind','opp_best_bid_locked_edge'],
    'PLUS_MARKOUT_AND_PAIR_EDGE': ['seconds_left','post_abs_net','post_imbalance_ratio','post_paired_coverage','tailwind','markout1s_ticks','opp_best_bid_locked_edge'],
}


def build_rows()->list[dict[str,Any]]:
    book=m.ro(m.BOOK_DB); target=m.ro(m.TARGET_DB); public_db=m.ro(m.PUBLIC_DB)
    try:
        public=m.load_public(public_db); markets=set(public); parents_by=m.load_parents(book,markets); fills_by=m.load_fills(target,markets)
        rows=[]
        for mid in sorted(markets):
            pub=public.get(mid,[]); ps=parents_by.get(mid,[]); fs=fills_by.get(mid,[])
            if not pub or not ps or not fs: continue
            for parent in ps:
                fill_ms=int(parent['last_target_ms']); side=str(parent['target_side']); opp='DOWN' if side=='UP' else 'UP'
                b0=m.public_before(pub,fill_ms,2000); a1=m.public_after(pub,fill_ms+1000,2000)
                if b0 is None or a1 is None: continue
                mid0=m.side_mid(b0[1],side); mid1=m.side_mid(a1[1],side); opp_bid=m.best_bid(a1[1],opp)
                if mid0 is None or mid1 is None or opp_bid is None: continue
                sec=m.tox.seconds_left(a1[1])
                if sec is None or sec<4: continue
                post=m.inventory_after(fs,fill_ms); dom=post['dominant']; mino=post['minority']
                role='DOMINANT' if dom and side==dom else 'MINORITY' if mino and side==mino else 'FLAT'
                if role!='DOMINANT': continue
                same=m.next_parent(ps,fill_ms+1000,fill_ms+5000,side)
                opposite=m.next_parent(ps,fill_ms+1000,fill_ms+5000,opp)
                anyp=m.next_parent(ps,fill_ms+1000,fill_ms+5000,None)
                switch=bool(opposite is not None and (anyp is None or int(opposite['placement_first_ms'])==int(anyp['placement_first_ms'])))
                direction,_=m.dirv.simple3(a1[1]); tailwind=1.0 if dom and direction and dom==direction else 0.0 if dom and direction else None
                fill_px=float(parent['target_price']); edge=1.0-fill_px-float(opp_bid)
                rows.append({
                    'market_id':mid,'fill_ms':fill_ms,'switch_opposite':1 if switch else 0,
                    'seconds_left':float(sec),'post_abs_net':float(post['absNet']),'post_imbalance_ratio':float(post['ratio']),
                    'post_paired_coverage':float(post['pairedCoverage']),'tailwind':tailwind,
                    'markout1s_ticks':(float(mid1)-float(mid0))/m.GRID,
                    'opp_best_bid_locked_edge':edge,
                })
        return rows
    finally:
        book.close();target.close();public_db.close()


def split(rows:list[dict[str,Any]])->dict[str,set[int]]:
    first={}
    for r in rows:first[int(r['market_id'])]=min(int(r['fill_ms']),first.get(int(r['market_id']),10**30))
    markets=sorted(first,key=lambda x:first[x]); n=len(markets); a=max(1,int(n*.70)); b=min(n,max(a+1,int(n*.85)))
    return {'train':set(markets[:a]),'validation':set(markets[a:b]),'test':set(markets[b:])}


def model(features:list[str])->ExplainableBoostingClassifier:
    return ExplainableBoostingClassifier(feature_names=features,max_bins=64,max_interaction_bins=32,interactions=3,
        outer_bags=4,learning_rate=.04,max_rounds=3000,early_stopping_rounds=80,min_samples_leaf=8,n_jobs=-2,random_state=20260819)


def frame(rows:list[dict[str,Any]],features:list[str])->pd.DataFrame:
    return pd.DataFrame([{f:r.get(f) for f in features} for r in rows],columns=features)


def eval_model(mod:ExplainableBoostingClassifier,rows:list[dict[str,Any]],features:list[str])->dict[str,Any]:
    y=[int(r['switch_opposite']) for r in rows]
    if not rows or len(set(y))<2:return {'n':len(rows)}
    prob=mod.predict_proba(frame(rows,features))[:,list(mod.classes_).index(1)]
    pred=[1 if x>=.5 else 0 for x in prob]
    return {'n':len(rows),'switchRate':sum(y)/len(y),'rocAuc':roc_auc_score(y,prob),'averagePrecision':average_precision_score(y,prob),
            'balancedAccuracyAt05':balanced_accuracy_score(y,pred),'predictedSwitchRateAt05':sum(pred)/len(pred)}


def top_terms(mod:ExplainableBoostingClassifier,n:int=12)->list[dict[str,Any]]:
    imps=list(mod.term_importances()); names=list(mod.term_names_); idx=sorted(range(len(imps)),key=lambda i:float(imps[i]),reverse=True)[:n]
    return [{'term':str(names[i]),'importance':float(imps[i])} for i in idx]


def main()->int:
    rows=build_rows(); sets=split(rows)
    def part(name):return [r for r in rows if int(r['market_id']) in sets[name]]
    results={}; models={}
    for name,features in FEATURE_SETS.items():
        mod=model(features); tr=part('train'); mod.fit(frame(tr,features),[int(r['switch_opposite']) for r in tr]); models[name]=mod
        results[name]={'features':features,'train':eval_model(mod,tr,features),'validation':eval_model(mod,part('validation'),features),'test':eval_model(mod,part('test'),features),'topTerms':top_terms(mod)}
    base=results['BASE_TIME_INV']['test'];
    lifts={name:{'rocAucDeltaVsBase':results[name]['test'].get('rocAuc',0)-base.get('rocAuc',0),
                 'averagePrecisionDeltaVsBase':results[name]['test'].get('averagePrecision',0)-base.get('averagePrecision',0)} for name in results if name!='BASE_TIME_INV'}
    report={'reportVersion':VERSION,'researchOnly':True,'purpose':'Explain Target dominant post-fill SWITCH_OPPOSITE decision with mature-MM state variables; research-only Target behavior model, never an OUR runtime input.',
            'coverage':{'rows':len(rows),'markets':len({r['market_id'] for r in rows}),'switchRate':sum(r['switch_opposite'] for r in rows)/len(rows)},
            'split':{k:sorted(v) for k,v in sets.items()},'models':results,'testLiftVsBase':lifts,
            'guard':['Target reaction label is future behavior and is used only to study Target, never as OUR runtime feature.','Chronological market split prevents same-market leakage across train/validation/test.','No hyperparameter sweep; all feature-set ablations use identical EBM capacity.','Pair-edge proxy uses current fill price + opposite public best bid at +1s; it is not Target private quote ground truth.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'coverage':report['coverage'],'test':{k:v['test'] for k,v in results.items()},'testLiftVsBase':lifts,'topTermsFull':results['PLUS_MARKOUT_AND_PAIR_EDGE']['topTerms'][:10],'report':str(REPORT)},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':raise SystemExit(main())
