from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_v1 as roll
OUT=ROOT/'data/research/r4_v0/hourly'
PRE=OUT/'r4_rolling_gap_owner_fresh24_preregistered.json'
CFGS=('ROLL_KEEP_GAP_OWNER','ROLL_KEEP_FLAT_ACTIVE2','ROLL_KEEP_STRIKE_CONFIRM_FLAT2','ROLL_KEEP_STRIKE_CONTRARY_FLAT2')

def load_market(mid:int):
    ps=sorted((ROOT/'data/hft_forward_paper_v1/markets').glob(f'{mid}_*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for p in ps:
        try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
        except Exception:continue
        if int(d.get('marketId') or 0)==mid and d.get('orderMeta'): return d
    raise RuntimeError(f'no frozen forward market {mid}')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--offset',type=int,default=0);ap.add_argument('--count',type=int,default=4);a=ap.parse_args()
    ids=json.load(open(PRE,encoding='utf-8'))['marketIds'][a.offset:a.offset+a.count]
    rows=[];errs=[]
    for mid in ids:
        d=load_market(int(mid))
        for cfg in CFGS:
            try:r=roll.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:errs.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
    ag={cfg:roll.agg([r for r in rows if r['config']==cfg]) for cfg in CFGS}
    by={}
    for mid in ids:
        by[str(mid)]={}
        for cfg in CFGS:
            r=next((x for x in rows if x['marketId']==mid and x['config']==cfg),None)
            if r:
                by[str(mid)][cfg]={'durable':r['durableBase'],'everSafe':r['everSafe'],'floor':r['final']['floor'],'absNet':r['final']['absNet'],'early':r['earlyMakerFillShares'],'earlySurplus':r['earlySurplusFillShares'],'flatSecondAcq':r['counts'].get('flatSecondAcquisitions',0),'strikeEligible':r['counts'].get('strikeSecondEligible',0),'strikeRejected':r['counts'].get('strikeSecondRejected',0)}
    rep={'version':'R4_STRIKE_DISTANCE_CONFIDENCE_GATE_V1','researchOnly':True,'cohort':'same preregistered fresh24 development cohort','offset':a.offset,'ids':ids,'preRegistered':{'base':'GAP_OWNER unchanged','unconditionalControl':'FLAT_ACTIVE2','confirmGate':'FLAT second full-18 owner only when 120<=secondsLeft<=300, candidate-side Predict mid 0.55-0.80, strikeTowardCandidate in [2,10) bps; public source asof <= decision time and <=1000ms old','contraryControl':'same but strikeTowardCandidate in (-10,-2] bps','allOtherGuards':'receipt-clock thin queue + pMaker support + MPQ + 18-share parent + realistic HFT unchanged','queueAge':'strike gate is acquisition permission only; once acquired, existing responsibility/MPQ lifecycle owns KEEP/PULL so gate drift does not instantly cancel'},'aggregate':ag,'byMarket':by,'errors':errs,'rows':rows}
    p=OUT/f'r4_strike_distance_confidence_gate_v1_o{a.offset}_n{len(ids)}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'byMarket':by,'errors':errs},ensure_ascii=False))
if __name__=='__main__':main()
