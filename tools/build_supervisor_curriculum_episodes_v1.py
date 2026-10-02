from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'supervisor_curriculum_v0'
SRC=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
EVENTS=OUT/'ordinary_market_curriculum_events_v0.csv'
GENERAL=SRC/'target_general_maker_side_hazard_v1.csv'
EPISODES=OUT/'ordinary_curriculum_episodes_v1.csv'
STATES=OUT/'ordinary_curriculum_episode_states_v1.csv'
REPORT=OUT/'ordinary_curriculum_episodes_v1_report.json'
RUN_GAP_MS=12000
PRE_MS=12000
POST_MS=6000
MIN_PURITY=.70

STAGE={
 'MAKER_BUILD':'B_MAKER_BUILD_ADD','MAKER_ADD':'B_MAKER_BUILD_ADD',
 'MAKER_REPAIR':'C_PASSIVE_REPAIR',
 'TAKER_BUILD':'D_TAKER_BUILD_ADD','TAKER_ADD':'D_TAKER_BUILD_ADD',
 'TAKER_REPAIR':'D_ACTIVE_TAKER_REPAIR',
}

def build_runs(ev:pd.DataFrame):
    runs=[];cur=None
    for r in ev.itertuples(index=False):
        item={'atMs':int(r.atMs),'skill':str(r.skill),'channel':str(r.channel),'side':str(r.side)}
        if cur is None or item['skill']!=cur['skill'] or item['atMs']-cur['lastMs']>RUN_GAP_MS:
            if cur:runs.append(cur)
            cur={'skill':item['skill'],'channel':item['channel'],'firstMs':item['atMs'],'lastMs':item['atMs'],'events':[item]}
        else:
            cur['lastMs']=item['atMs'];cur['events'].append(item)
    if cur:runs.append(cur)
    return runs

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    ev=pd.read_csv(EVENTS).sort_values(['marketEndMs','marketId','atMs']).reset_index(drop=True)
    g=pd.read_csv(GENERAL).sort_values(['market_end_ms','market_id','checkpoint_ms']).reset_index(drop=True)
    eps=[]
    for mid,x in ev.groupby('marketId',sort=False):
        x=x.sort_values('atMs');mend=int(x.marketEndMs.iloc[0]);mstart=mend-300000;runs=build_runs(x)
        # Pure single-skill runs.
        for j,r in enumerate(runs):
            start=max(mstart,int(r['firstMs'])-PRE_MS);end=min(mend,int(r['lastMs'])+POST_MS)
            inside=x[(x.atMs>=start)&(x.atMs<=end)]
            n=len(inside);own=int((inside.skill.astype(str)==r['skill']).sum());purity=own/max(n,1)
            min_events=2 if r['channel']=='MAKER' else 1
            if len(r['events'])<min_events or purity<MIN_PURITY:continue
            eps.append({'episodeId':f'{int(mid)}:SKILL:{j}','marketId':int(mid),'marketEndMs':mend,'lessonType':'SKILL','lesson':r['skill'],'stage':STAGE[r['skill']],
                        'windowStartMs':start,'windowEndMs':end,'firstActionMs':r['firstMs'],'lastActionMs':r['lastMs'],'targetEvents':len(r['events']),'allEventsInWindow':n,'lessonPurity':purity,'durationMs':end-start})
        # Explicit channel handoffs: adjacent runs with different channels within 12s.
        for j,(a,b) in enumerate(zip(runs,runs[1:])):
            gap=int(b['firstMs'])-int(a['lastMs'])
            if a['channel']==b['channel'] or gap>RUN_GAP_MS:continue
            start=max(mstart,int(a['firstMs'])-PRE_MS);end=min(mend,int(b['lastMs'])+POST_MS)
            inside=x[(x.atMs>=start)&(x.atMs<=end)]
            channels=inside.channel.astype(str).tolist();switches=sum(u!=v for u,v in zip(channels,channels[1:]))
            if switches<1:continue
            name=f"{a['channel']}_TO_{b['channel']}"
            eps.append({'episodeId':f'{int(mid)}:HANDOFF:{j}','marketId':int(mid),'marketEndMs':mend,'lessonType':'HANDOFF','lesson':name,'stage':'E_MAKER_TAKER_HANDOFF',
                        'windowStartMs':start,'windowEndMs':end,'firstActionMs':a['firstMs'],'lastActionMs':b['lastMs'],'targetEvents':len(a['events'])+len(b['events']),'allEventsInWindow':len(inside),'lessonPurity':np.nan,'durationMs':end-start,'handoffGapMs':gap,'channelSwitchesInWindow':switches})
    e=pd.DataFrame(eps).sort_values(['marketEndMs','marketId','windowStartMs','episodeId']).reset_index(drop=True)

    # Add structural difficulty from the actual strict-past states inside each lesson window.
    state_parts=[];metrics=[]
    for r in e.itertuples(index=False):
        x=g[(g.market_id.astype(int)==int(r.marketId))&(g.checkpoint_ms>=int(r.windowStartMs))&(g.checkpoint_ms<=int(r.windowEndMs))].copy()
        if len(x)==0:continue
        x['episodeId']=r.episodeId;x['lesson']=r.lesson;x['stage']=r.stage;state_parts.append(x)
        absnet=pd.to_numeric(x.maker_abs_net,errors='coerce');cov=pd.to_numeric(x.maker_paired_coverage,errors='coerce');vel=pd.to_numeric(x.maker_absnet_change_10s,errors='coerce').abs();spr=pd.concat([pd.to_numeric(x.up_spread_ticks,errors='coerce'),pd.to_numeric(x.down_spread_ticks,errors='coerce')],ignore_index=True)
        metrics.append({'episodeId':r.episodeId,'stateRows':len(x),'maxMakerAbsNet':absnet.max(),'minMakerPairedCoverage':cov.min(),'p90AbsNetVelocity10s':vel.quantile(.9),'p90SpreadTicks':spr.quantile(.9)})
    sm=pd.DataFrame(metrics);e=e.merge(sm,on='episodeId',how='left')
    def rank(s):return pd.to_numeric(s,errors='coerce').rank(pct=True).fillna(.5)
    if len(e):
        e['structuralDifficulty']=(rank(e.maxMakerAbsNet)+rank(1-e.minMakerPairedCoverage)+rank(e.p90AbsNetVelocity10s)+rank(e.p90SpreadTicks))/4
        e['difficultyBand']=pd.cut(e.structuralDifficulty,[-1,.35,.6,.8,2],labels=['EASY','MEDIUM','HARD','VERY_HARD']).astype(str)
    e.to_csv(EPISODES,index=False)
    pd.concat(state_parts,ignore_index=True).to_csv(STATES,index=False) if state_parts else pd.DataFrame().to_csv(STATES,index=False)
    rep={'reportVersion':'SUPERVISOR_CURRICULUM_EPISODES_V1','researchOnly':True,
         'source':{'markets':int(ev.marketId.nunique()),'events':len(ev),'ordinaryOnly':True,'special20260816':'ABSENT'},
         'episodeRule':{'sameSkillRunGapMs':RUN_GAP_MS,'contextPreMs':PRE_MS,'contextPostMs':POST_MS,'minPurity':MIN_PURITY,'makerMinRunEvents':2,'takerMinRunEvents':1},
         'episodes':{'total':len(e),'marketsCovered':int(e.marketId.nunique()),'lessonCounts':e.lesson.value_counts().to_dict(),'stageCounts':e.stage.value_counts().to_dict(),'difficultyBands':e.difficultyBand.value_counts().to_dict()},
         'purity':{'skillEpisodes':int((e.lessonType=='SKILL').sum()),'mean':float(e.loc[e.lessonType=='SKILL','lessonPurity'].mean()),'median':float(e.loc[e.lessonType=='SKILL','lessonPurity'].median())},
         'interpretation':'Market-level lessons were heavily mixed; episode slicing creates homogeneous skill windows. Train component skills on SKILL episodes first, then handoff episodes, then mixed full-market trajectories.',
         'next':'Run a controlled curriculum experiment on one component (Maker Repair is recommended): random mixed batches vs staged pure-skill episodes, fixed model/hyperparameters and fixed chronological market holdout.',
         'files':{'episodes':str(EPISODES),'episodeStates':str(STATES)},
         'guards':['No winner/PnL.','Target action is teacher/curriculum metadata only.','Strict-past state rows only as features.','No runtime changes.']}
    REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
