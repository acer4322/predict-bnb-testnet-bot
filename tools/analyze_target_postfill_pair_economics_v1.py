from __future__ import annotations

import importlib.util, json, math, sqlite3, statistics, sys
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'analyze_target_mature_mm_knob_projection_v0.py'
spec=importlib.util.spec_from_file_location('target_knob_v0_pair',P); m=importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=m; spec.loader.exec_module(m)
REPORT=ROOT/'data'/'research'/'target_postfill_pair_economics_v1_report.json'
VERSION='TARGET_POSTFILL_PAIR_ECONOMICS_V1'


def stats(xs:list[float])->dict[str,Any]:
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    if not ys:return {'n':0,'mean':None,'median':None,'p25':None,'p75':None,'p90':None}
    ys.sort()
    def q(p):
        pos=(len(ys)-1)*p; lo=int(math.floor(pos)); hi=int(math.ceil(pos)); w=pos-lo
        return ys[lo]*(1-w)+ys[hi]*w
    return {'n':len(ys),'mean':statistics.mean(ys),'median':statistics.median(ys),'p25':q(.25),'p75':q(.75),'p90':q(.90),'min':ys[0],'max':ys[-1]}


def summarize(rows:list[dict[str,Any]])->dict[str,Any]:
    edges=[r['oppBestBidLockedEdge'] for r in rows if r.get('oppBestBidLockedEdge') is not None]
    edges1=[r['oppMinus1LockedEdge'] for r in rows if r.get('oppMinus1LockedEdge') is not None]
    return {
        'n':len(rows),'markets':len({r['marketId'] for r in rows}),
        'postAbsNet':stats([r['postAbsNet'] for r in rows]),
        'postPairedCoverage':stats([r['postPairedCoverage'] for r in rows]),
        'markout1sTicks':stats([r['markout1sTicks'] for r in rows]),
        'oppBestBidLockedEdge':stats(edges),
        'oppMinus1LockedEdge':stats(edges1),
        'oppBestBidPositiveEdgeRate':sum(x>0 for x in edges)/len(edges) if edges else None,
        'oppBestBidEdgeAtLeast1cRate':sum(x>=.01-1e-12 for x in edges)/len(edges) if edges else None,
        'oppMinus1PositiveEdgeRate':sum(x>0 for x in edges1)/len(edges1) if edges1 else None,
        'nextSameDelayMs':stats([r['nextSameDelayMs'] for r in rows if r.get('nextSameDelayMs') is not None]),
    }


def main()->int:
    book=m.ro(m.BOOK_DB); target=m.ro(m.TARGET_DB); public_db=m.ro(m.PUBLIC_DB)
    try:
        public=m.load_public(public_db); markets=set(public); parents_by=m.load_parents(book,markets); fills_by=m.load_fills(target,markets)
        rows=[]
        for mid in sorted(markets):
            pub=public.get(mid,[]); ps=parents_by.get(mid,[]); fs=fills_by.get(mid,[])
            if not pub or not ps or not fs:continue
            for p in ps:
                fill_ms=int(p['last_target_ms']); side=str(p['target_side']); opp='DOWN' if side=='UP' else 'UP'
                b0=m.public_before(pub,fill_ms,2000); a1=m.public_after(pub,fill_ms+1000,2000)
                if b0 is None or a1 is None:continue
                mid0=m.side_mid(b0[1],side); mid1=m.side_mid(a1[1],side); opp_bid=m.best_bid(a1[1],opp)
                if mid0 is None or mid1 is None or opp_bid is None:continue
                sec=m.tox.seconds_left(a1[1])
                if sec is None or sec<4:continue
                post=m.inventory_after(fs,fill_ms); dom=post['dominant']; mino=post['minority']
                role='DOMINANT' if dom and side==dom else 'MINORITY' if mino and side==mino else 'FLAT'
                if role!='DOMINANT':continue
                same=m.next_parent(ps,fill_ms+1000,fill_ms+5000,side)
                opposite=m.next_parent(ps,fill_ms+1000,fill_ms+5000,opp)
                anyp=m.next_parent(ps,fill_ms+1000,fill_ms+5000,None)
                if same is not None and (anyp is None or int(same['placement_first_ms'])==int(anyp['placement_first_ms'])): reaction='CONTINUE_SAME_FIRST'
                elif opposite is not None and (anyp is None or int(opposite['placement_first_ms'])==int(anyp['placement_first_ms'])): reaction='SWITCH_OPPOSITE_FIRST'
                else: reaction='PAUSE_NO_PARENT_1_TO_5S'
                fill_px=float(p['target_price']); opp_bid=float(opp_bid); opp_minus1=max(.06,round(opp_bid-.01,2))
                move=(float(mid1)-float(mid0))/m.GRID
                direction,strength=m.dirv.simple3(a1[1]); align='TAILWIND' if dom and direction and dom==direction else 'HEADWIND' if dom and direction else 'UNKNOWN'
                rows.append({
                    'marketId':mid,'fillMs':fill_ms,'side':side,'reactionClass':reaction,'alignmentAt1s':align,
                    'simple3DirectionAt1s':direction,'secondsLeftAt1s':sec,'postAbsNet':post['absNet'],'postPairedCoverage':post['pairedCoverage'],
                    'markout1sTicks':move,'toxicity1s':m.tox.toxicity_label(move),'fillPrice':fill_px,'oppositeBestBidAt1s':opp_bid,
                    'oppBestBidPairCost':fill_px+opp_bid,'oppBestBidLockedEdge':1-fill_px-opp_bid,
                    'oppMinus1PairCost':fill_px+opp_minus1,'oppMinus1LockedEdge':1-fill_px-opp_minus1,
                    'nextSameDelayMs':int(same['placement_first_ms'])-fill_ms if same is not None else None,
                })
        groups={g:summarize([r for r in rows if r['reactionClass']==g]) for g in ('CONTINUE_SAME_FIRST','SWITCH_OPPOSITE_FIRST','PAUSE_NO_PARENT_1_TO_5S')}
        bytox={}
        for t in ('TOXIC_1T_PLUS','NEUTRAL_LT1T','FAVORABLE_1T_PLUS'):
            rr=[r for r in rows if r['toxicity1s']==t]
            bytox[t]={g:summarize([r for r in rr if r['reactionClass']==g]) for g in ('CONTINUE_SAME_FIRST','SWITCH_OPPOSITE_FIRST','PAUSE_NO_PARENT_1_TO_5S')}
        byalign={}
        for a in ('HEADWIND','TAILWIND'):
            rr=[r for r in rows if r['alignmentAt1s']==a]
            byalign[a]={g:summarize([r for r in rr if r['reactionClass']==g]) for g in ('CONTINUE_SAME_FIRST','SWITCH_OPPOSITE_FIRST','PAUSE_NO_PARENT_1_TO_5S')}
        report={'reportVersion':VERSION,'researchOnly':True,'coverage':{'rows':len(rows),'markets':len({r['marketId'] for r in rows})},
                'method':{'checkpoint':'Target dominant-side parent fill +1s, after 1s side-token markout is observable; classify first anchored parent in (1s,5s].',
                          'pairEconomicsProxy':'fill price of current dominant parent + opposite public best bid at +1s; also best-bid-minus-1 candidate. Positive 1-sum is gross complementary locked-edge opportunity if opposite Maker fill could occur there.',
                          'guard':'Descriptive only; public best bid is not Target private quote and anchored parent ownership remains inferred.'},
                'byReaction':groups,'by1sToxicity':bytox,'byAlignment':byalign}
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        book.close();target.close();public_db.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
