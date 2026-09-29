from __future__ import annotations
import csv,json,random,statistics
from collections import Counter,defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATES=ROOT/'data/research/target_controller_hazard_v21_states.csv'
OLD_REVEAL=ROOT/'data/research/target_architecture_blind_random_v0_reveal.csv'
BLIND=ROOT/'data/research/target_taker_escalation_burst_urgency_v0_blind.csv'
REVEAL=ROOT/'data/research/target_taker_escalation_burst_urgency_v0_reveal.csv'
REPORT=ROOT/'data/research/target_taker_escalation_burst_urgency_v0_report.json'
LOG=ROOT/'data/research/target_taker_escalation_research_log_v2.json'
SEED=2026081802
PER_REGIME=15


def num(v):
    try:return float(v)
    except:return None

def intval(v):
    try:return int(float(v))
    except:return None

def urgency(row):
    tsl=num(row.get('time_since_last_taker_ms'))
    msa=num(row.get('maker_streak_age_ms'))
    rg=num(row.get('risk_growth_5s'))
    gg=num(row.get('gap_growth_5s'))
    recent=tsl is not None and tsl<=10000
    young=msa is not None and msa<=15000
    change=(rg is not None and abs(rg)>1e-9) or (gg is not None and abs(gg)>1e-9)
    votes=int(recent)+int(young)+int(change)
    return ('TAKER' if votes>=2 else 'PASSIVE'),recent,young,change,votes

def old_markets():
    if not OLD_REVEAL.exists():return set()
    with OLD_REVEAL.open(encoding='utf-8',newline='') as f:
        return {int(float(r['market_id'])) for r in csv.DictReader(f)}

def candidate_features():
    excluded=old_markets()
    by_regime=defaultdict(lambda:defaultdict(list))
    with STATES.open(encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            mid=intval(r.get('market_id')); prior=intval(r.get('prior_taker_parents')); sec=num(r.get('seconds_left'))
            if mid is None or mid in excluded or prior is None or prior<1 or sec is None or not (15<=sec<=240):continue
            reg=str(r.get('regime') or '')
            if reg not in {'STRESS_2026_08_16','ORDINARY_2026_08_17'}:continue
            # Build a feature-only record. Do not copy future-label columns here.
            keep={k:r.get(k) for k in [
                'market_id','segment_id','regime','sample_ms','seconds_left','risk_deficit','abs_payoff_gap','maker_abs_payoff_gap',
                'worst_case_pnl','payoff_gap','maker_payoff_gap','prior_maker_parents','prior_taker_parents','time_since_last_maker_ms',
                'time_since_last_taker_ms','maker_streak_age_ms','maker_parents_since_last_taker','maker_shares_since_last_taker',
                'maker_notional_since_last_taker','max_risk_since_taker_reset','max_gap_since_taker_reset','same_payoff_gap_sign_age_ms',
                'same_maker_gap_sign_age_ms','risk_growth_1s','risk_growth_3s','risk_growth_5s','gap_growth_1s','gap_growth_3s','gap_growth_5s',
                'maker_gap_growth_1s','maker_gap_growth_3s','maker_gap_growth_5s']}
            by_regime[reg][mid].append(keep)
    return by_regime

def write_csv(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    fields=list(rows[0].keys()) if rows else []
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)

def summary(rows):
    if not rows:return {'rows':0}
    exact=sum(r['blind_action']==r['target_action_5s'] for r in rows)/len(rows)
    pred_t=[r for r in rows if r['blind_action']=='TAKER']
    true_t=[r for r in rows if r['target_action_5s']=='TAKER']
    tp=sum(r['blind_action']=='TAKER' and r['target_action_5s']=='TAKER' for r in rows)
    precision=tp/len(pred_t) if pred_t else None
    recall=tp/len(true_t) if true_t else None
    return {'rows':len(rows),'markets':len({r['market_id'] for r in rows}),'accuracy':exact,
            'alwaysPassiveAccuracy':sum(r['target_action_5s']=='PASSIVE' for r in rows)/len(rows),
            'predicted':dict(Counter(r['blind_action'] for r in rows)),'target':dict(Counter(r['target_action_5s'] for r in rows)),
            'takerPrecision':precision,'takerRecall':recall,'confusion':dict(Counter(f"{r['blind_action']}->{r['target_action_5s']}" for r in rows))}

def main():
    rng=random.Random(SEED)
    by=candidate_features(); blind=[]
    sample_meta={}
    for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16'):
        mids=sorted(by.get(reg,{}))
        chosen=rng.sample(mids,min(PER_REGIME,len(mids)))
        sample_meta[reg]={'eligibleMarkets':len(mids),'sampledMarkets':len(chosen)}
        for mid in chosen:
            # Choose one state without labels using the fixed RNG.
            state=rng.choice(by[reg][mid])
            action,recent,young,change,votes=urgency(state)
            blind.append({**state,'blind_action':action,'recent_taker_le_10s':int(recent),'young_maker_streak_le_15s':int(young),'active_change_5s':int(change),'urgency_votes':votes})
    blind.sort(key=lambda r:(r['regime'],int(float(r['market_id']))))
    write_csv(BLIND,blind)

    # Reveal only after blind decisions are persisted.
    labels={}
    with STATES.open(encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            key=(intval(r.get('market_id')),intval(r.get('sample_ms')))
            if key[0] is None or key[1] is None:continue
            labels[key]={'taker_within_5s':intval(r.get('taker_within_5s')) or 0,'repair_within_5s':intval(r.get('repair_within_5s')) or 0,
                         'add_within_5s':intval(r.get('add_within_5s')) or 0,'taker_within_15s':intval(r.get('taker_within_15s')) or 0}
    reveal=[]
    for r in blind:
        key=(int(float(r['market_id'])),int(float(r['sample_ms']))); lab=labels.get(key,{})
        target='TAKER' if lab.get('taker_within_5s') else 'PASSIVE'
        reveal.append({**r,'target_action_5s':target,'target_taker_within_5s':lab.get('taker_within_5s',0),
                       'target_repair_within_5s':lab.get('repair_within_5s',0),'target_add_within_5s':lab.get('add_within_5s',0),
                       'target_taker_within_15s':lab.get('taker_within_15s',0),'exact_5s':int(r['blind_action']==target)})
    write_csv(REVEAL,reveal)
    report={'reportVersion':'TARGET_TAKER_ESCALATION_BURST_URGENCY_V0','researchOnly':True,'liveChanges':False,'parameterSweep':False,
            'randomSeed':SEED,'topic':'TAKER escalation as short-horizon intervention-burst continuation rather than absolute risk threshold',
            'hypothesis':'Immediate Taker escalation is more likely when Target has just used Taker recently, the Maker streak is young, and portfolio state is actively changing.',
            'frozenRule':{'recentTaker':'time_since_last_taker_ms <= 10000','youngMakerStreak':'maker_streak_age_ms <= 15000',
                          'activeChange':'abs(risk_growth_5s)>0 OR abs(gap_growth_5s)>0','TAKER':'at least 2 of 3','PASSIVE':'otherwise'},
            'blindBoundary':{'excludedPriorBlindMarkets':len(old_markets()),'futureLabelsReadOnlyAfterBlindCsvWritten':True,'oneRandomStrictPastStatePerMarket':True,
                             'requiresPriorTaker':True,'secondsLeftRange':[15,240]},'sample':sample_meta,
            'overall':summary(reveal),'byRegime':{reg:summary([r for r in reveal if r['regime']==reg]) for reg in ('ORDINARY_2026_08_17','STRESS_2026_08_16')},
            'byVoteCount':{str(v):summary([r for r in reveal if int(r['urgency_votes'])==v]) for v in range(4)},
            'outputs':{'blind':str(BLIND),'reveal':str(REVEAL),'report':str(REPORT),'researchLog':str(LOG)}}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    base=report['overall']; verdict='KEEP' if base['accuracy']>base['alwaysPassiveAccuracy'] and (base['takerPrecision'] or 0)>=0.5 else 'OBSERVE' if (base['takerPrecision'] or 0)>=0.4 else 'REJECT'
    log={'reportVersion':'TARGET_TAKER_ESCALATION_RESEARCH_LOG_V2','researchOnly':True,'liveChanges':False,
         'topic':report['topic'],'hypothesis':report['hypothesis'],
         'literatureReview':['target_controller_hazard_v21_report: Taker hazard falls sharply as time_since_last_taker and maker_streak_age grow; stress 5s hazard is ~9x ordinary.',
                             'risk/gap growth surfaces are U-shaped: large short-horizon changes associate with more Taker; absolute risk level alone is weak/non-monotone.'],
         'method':report['blindBoundary']|{'rule':report['frozenRule'],'randomSeed':SEED,'sample':sample_meta},
         'result':{'overall':report['overall'],'byRegime':report['byRegime'],'byVoteCount':report['byVoteCount']},
         'blindScore':{'accuracy':base['accuracy'],'alwaysPassiveBaseline':base['alwaysPassiveAccuracy'],'takerPrecision':base['takerPrecision'],'takerRecall':base['takerRecall']},
         'decision':verdict,
         'KEEP':['short-horizon burst-continuation framing'] if verdict!='REJECT' else [],
         'OBSERVE':['recent Taker as urgency component','young Maker streak as urgency component','short-horizon state change magnitude/sign'],
         'REJECT':['long Maker persistence / long time-since-Taker as escalation trigger'],
         'nextQuestion':'If Burst-Urgency V0 is insufficient, isolate which of recent-Taker, young-Maker, and active-change contributes stable incremental precision on chronological held-out markets; do not tune thresholds.'}
    LOG.write_text(json.dumps(log,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'verdict':verdict,'overall':report['overall'],'byRegime':report['byRegime'],'sample':sample_meta,'report':str(REPORT),'log':str(LOG)},ensure_ascii=False,indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
