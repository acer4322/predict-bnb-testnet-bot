from __future__ import annotations

import bisect
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
OUT=ROOT/'data'/'research'/'supervisor_curriculum_v0'
GENERAL=SRC/'target_general_maker_side_hazard_v1.csv'
TARGET_DB=ROOT/'data'/'target_wallet_official_v1.db'
CATALOG=OUT/'ordinary_market_curriculum_catalog_v0.csv'
REPORT=OUT/'ordinary_market_curriculum_catalog_v0_report.json'
EVENTS=OUT/'ordinary_market_curriculum_events_v0.csv'
EPS=1.0

SKILLS=['MAKER_BUILD','MAKER_ADD','MAKER_REPAIR','TAKER_BUILD','TAKER_ADD','TAKER_REPAIR']


def effect(pre_net: float, side: str, shares: float, maker: bool=False) -> str:
    post=pre_net+(shares if side=='UP' else -shares)
    prefix='MAKER_' if maker else 'TAKER_'
    if abs(pre_net)<=EPS:return prefix+'BUILD'
    if abs(post)<abs(pre_net)-1e-9:return prefix+'REPAIR'
    return prefix+'ADD'


def norm_entropy(counts: dict[str,int]) -> float:
    vals=np.asarray([counts.get(k,0) for k in SKILLS],float)
    s=vals.sum()
    if s<=0:return 0.0
    p=vals[vals>0]/s
    h=float(-(p*np.log(p)).sum())
    return h/math.log(len(SKILLS)) if len(SKILLS)>1 else 0.0


def pct_rank(s: pd.Series) -> pd.Series:
    return s.rank(method='average',pct=True).fillna(.5)


def main() -> int:
    OUT.mkdir(parents=True,exist_ok=True)
    d=pd.read_csv(GENERAL).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    # This source is already ordinary-only: 2026-08-17 02:05 through 2026-08-19 17:40 Asia/Taipei.
    mids=sorted(map(int,d.market_id.unique()))
    conn=sqlite3.connect(f'file:{TARGET_DB.resolve().as_posix()}?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    q='select market_id,parent_id,side,first_event_ms,shares,average_price from target_parent_orders where asset=\'BTC\' and role=\'TAKER\' and quote_type=\'BID\' and market_id in (%s) order by market_id,first_event_ms,parent_id'%(','.join('?'*len(mids)))
    takers={}
    for r in conn.execute(q,mids):takers.setdefault(int(r['market_id']),[]).append(dict(r))
    conn.close()

    market_rows=[];event_rows=[]
    for mid,x in d.groupby('market_id',sort=False):
        x=x.sort_values('checkpoint_ms').copy();mid=int(mid); seq=[]
        # Maker teacher labels are high-confidence anchored placements in (checkpoint, checkpoint+1s].
        for r in x.itertuples(index=False):
            net=float(getattr(r,'maker_net')) if pd.notna(getattr(r,'maker_net')) else 0.0
            t=int(getattr(r,'checkpoint_ms'))
            for side,col in [('UP','label_up_next1s'),('DOWN','label_down_next1s')]:
                if int(getattr(r,col) or 0)>0:
                    skill=effect(net,side,18.0,maker=True)
                    seq.append((t+1,skill,'MAKER',side))
                    event_rows.append({'marketId':mid,'marketEndMs':int(getattr(r,'market_end_ms')),'atMs':t+1,'channel':'MAKER','side':side,'skill':skill,'teacherSource':'ANCHORED_PLACEMENT_NEXT1S'})
        # Taker exact parents; use nearest strict-past Target combined_net checkpoint only to classify effect semantics.
        times=pd.to_numeric(x.checkpoint_ms,errors='coerce').astype('int64').tolist()
        nets=pd.to_numeric(x.combined_net,errors='coerce').fillna(0.0).tolist()
        for tr in takers.get(mid,[]):
            t=int(tr['first_event_ms']);j=bisect.bisect_left(times,t)-1
            pre=float(nets[j]) if j>=0 else 0.0
            shares=float(tr['shares'] or 0.0);side=str(tr['side'])
            skill=effect(pre,side,shares,maker=False)
            seq.append((t,skill,'TAKER',side))
            event_rows.append({'marketId':mid,'marketEndMs':int(x.market_end_ms.iloc[0]),'atMs':t,'channel':'TAKER','side':side,'skill':skill,'teacherSource':'EXACT_TARGET_PARENT','parentId':tr['parent_id'],'shares':shares})
        seq.sort(key=lambda z:(z[0],0 if z[2]=='MAKER' else 1))
        counts=Counter(z[1] for z in seq); total=sum(counts.values()); maker=sum(v for k,v in counts.items() if k.startswith('MAKER_')); taker=total-maker
        channel_switches=sum(a[2]!=b[2] for a,b in zip(seq,seq[1:]))
        skill_switches=sum(a[1]!=b[1] for a,b in zip(seq,seq[1:]))
        purity=(max(counts.values())/total) if total else 0.0
        entropy=norm_entropy(counts)
        maker_rep=counts['MAKER_REPAIR'];taker_rep=counts['TAKER_REPAIR'];
        maker_nonrep=counts['MAKER_BUILD']+counts['MAKER_ADD'];taker_nonrep=counts['TAKER_BUILD']+counts['TAKER_ADD']
        # Public/portfolio stress: no winner or PnL.
        maker_abs=pd.to_numeric(x.maker_abs_net,errors='coerce')
        coverage=pd.to_numeric(x.maker_paired_coverage,errors='coerce')
        absvel=pd.to_numeric(x.maker_absnet_change_10s,errors='coerce').abs()
        spreads=pd.concat([pd.to_numeric(x.up_spread_ticks,errors='coerce'),pd.to_numeric(x.down_spread_ticks,errors='coerce')],ignore_index=True)
        lesson_tags=[]
        if total>=3: lesson_tags.append('FOUNDATION_ACT_HOLD')
        if maker>=6 and taker<=1 and maker_nonrep/max(maker,1)>=.70: lesson_tags.append('PURE_MAKER_BUILD_ADD')
        if maker_rep>=4 and maker_rep/max(maker,1)>=.30: lesson_tags.append('PASSIVE_MAKER_REPAIR')
        if taker_rep>=2 and taker_rep/max(taker,1)>=.45: lesson_tags.append('ACTIVE_TAKER_REPAIR')
        if taker_nonrep>=2 and taker_nonrep/max(taker,1)>=.45: lesson_tags.append('TAKER_BUILD_ADD')
        if maker>=4 and taker>=2 and channel_switches>=2: lesson_tags.append('MAKER_TAKER_HANDOFF')
        if entropy>=.55 or (maker>=4 and taker>=3 and skill_switches>=5): lesson_tags.append('MIXED_COORDINATION')
        if total<3: lesson_tags.append('SPARSE_SKIP_OR_HOLD_ONLY')
        market_rows.append({
            'marketId':mid,'marketEndMs':int(x.market_end_ms.iloc[0]),'checkpointRows':len(x),'totalTeacherActions':total,
            **{k:counts.get(k,0) for k in SKILLS},
            'makerActions':maker,'takerActions':taker,'makerRepairRate':maker_rep/max(maker,1),'takerRepairRate':taker_rep/max(taker,1),
            'channelSwitches':channel_switches,'skillSwitches':skill_switches,'transitionDensity':skill_switches/max(total-1,1),
            'lessonPurity':purity,'skillEntropy':entropy,'distinctSkills':sum(counts.get(k,0)>0 for k in SKILLS),
            'maxMakerAbsNet':float(maker_abs.max()) if maker_abs.notna().any() else np.nan,
            'minMakerPairedCoverage':float(coverage.min()) if coverage.notna().any() else np.nan,
            'p90MakerAbsNetVelocity10s':float(absvel.quantile(.90)) if absvel.notna().any() else np.nan,
            'p90SpreadTicks':float(spreads.quantile(.90)) if spreads.notna().any() else np.nan,
            'lessonTags':'|'.join(lesson_tags),
        })
    m=pd.DataFrame(market_rows).sort_values(['marketEndMs','marketId']).reset_index(drop=True)
    # Structural difficulty only; never PnL/winner. Percentile-based so units do not dominate.
    m['stressScore']=(pct_rank(m.maxMakerAbsNet)+pct_rank(1-m.minMakerPairedCoverage)+pct_rank(m.p90MakerAbsNetVelocity10s)+pct_rank(m.p90SpreadTicks))/4
    m['switchScore']=pct_rank(m.transitionDensity)
    m['difficultyScore']=.40*m.skillEntropy+.25*m.switchScore+.35*m.stressScore
    # Difficulty tier is independent from lesson tags.
    def tier(r):
        if r.totalTeacherActions<3:return 'SKIP_SPARSE'
        if r.lessonPurity>=.72 and r.channelSwitches<=1 and r.difficultyScore<.55:return 'L1_SINGLE_SKILL'
        if r.difficultyScore<.48:return 'L1_FOUNDATION'
        if r.difficultyScore<.62:return 'L2_CONTROLLED_VARIATION'
        if r.difficultyScore<.74:return 'L3_COORDINATION'
        return 'L4_MIXED_HARD'
    m['difficultyTier']=m.apply(tier,axis=1)
    # Primary lesson is a convenience; multi-label tags remain canonical.
    def primary(r):
        tags=set(str(r.lessonTags).split('|'))
        if 'SPARSE_SKIP_OR_HOLD_ONLY' in tags:return 'SPARSE_SKIP_OR_HOLD_ONLY'
        if 'PURE_MAKER_BUILD_ADD' in tags and r.lessonPurity>=.55:return 'PURE_MAKER_BUILD_ADD'
        if 'PASSIVE_MAKER_REPAIR' in tags and r.makerRepairRate>=.45:return 'PASSIVE_MAKER_REPAIR'
        if 'ACTIVE_TAKER_REPAIR' in tags and r.takerRepairRate>=.55:return 'ACTIVE_TAKER_REPAIR'
        if 'TAKER_BUILD_ADD' in tags and r.takerRepairRate<.45:return 'TAKER_BUILD_ADD'
        if 'MAKER_TAKER_HANDOFF' in tags:return 'MAKER_TAKER_HANDOFF'
        if 'MIXED_COORDINATION' in tags:return 'MIXED_COORDINATION'
        return 'FOUNDATION_ACT_HOLD'
    m['primaryLesson']=m.apply(primary,axis=1)
    m.to_csv(CATALOG,index=False);pd.DataFrame(event_rows).sort_values(['marketEndMs','marketId','atMs']).to_csv(EVENTS,index=False)

    tag_counts=Counter()
    for s in m.lessonTags:
        for t in str(s).split('|'):
            if t:tag_counts[t]+=1
    rep={
        'reportVersion':'SUPERVISOR_CURRICULUM_CATALOG_V0','researchOnly':True,
        'question':'Can ordinary Target markets be catalogued into homogeneous skill lessons and independent difficulty tiers before Supervisor training?',
        'source':{'markets':int(m.marketId.nunique()),'checkpointRows':int(len(d)),'minEndMs':int(m.marketEndMs.min()),'maxEndMs':int(m.marketEndMs.max()),'special20260816':'ABSENT','targetTakerMarkets':int(sum(m.takerActions>0))},
        'principle':'Multi-label lesson tags are canonical; difficulty is a separate structural score. Winner/PnL never used. A market may teach multiple skills; later episode slicing should be preferred for mixed markets.',
        'lessonTagCounts':dict(tag_counts),'primaryLessonCounts':m.primaryLesson.value_counts().to_dict(),'difficultyTierCounts':m.difficultyTier.value_counts().to_dict(),
        'purity':{'mean':float(m.lessonPurity.mean()),'median':float(m.lessonPurity.median()),'p25':float(m.lessonPurity.quantile(.25)),'p75':float(m.lessonPurity.quantile(.75))},
        'mixedEvidence':{'multiSkill3plusMarkets':int((m.distinctSkills>=3).sum()),'handoffMarkets':int(m.lessonTags.str.contains('MAKER_TAKER_HANDOFF').sum()),'mixedCoordinationMarkets':int(m.lessonTags.str.contains('MIXED_COORDINATION').sum())},
        'recommendedCurriculum':[
            'Stage A: FOUNDATION_ACT_HOLD across broad ordinary markets.',
            'Stage B: PURE_MAKER_BUILD_ADD batches until student ACT/hold and Maker cadence stabilize.',
            'Stage C: PASSIVE_MAKER_REPAIR as a separate subject; do not interleave heavily with Taker-add lessons at first.',
            'Stage D: ACTIVE_TAKER_REPAIR and TAKER_BUILD_ADD as separate Taker subjects.',
            'Stage E: MAKER_TAKER_HANDOFF only after the component skills pass.',
            'Stage F: MIXED_COORDINATION/L4 hard ordinary markets, then later special-market curriculum.'
        ],
        'next':'Build episode-level lesson windows from multi-label markets and compare random-mixed training vs staged curriculum on the same chronological holdout.',
        'files':{'catalog':str(CATALOG),'events':str(EVENTS)},
        'guards':['No winner/PnL.','Ordinary 2026-08-17..19 only; 2026-08-16 absent.','Retrospective Target actions are curriculum/teacher labels only.','No runtime changes.']
    }
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2));return 0

if __name__=='__main__':raise SystemExit(main())
