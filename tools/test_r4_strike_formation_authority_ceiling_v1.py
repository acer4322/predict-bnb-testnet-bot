from __future__ import annotations
import argparse,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_v1 as roll
OUT=ROOT/'data/research/r4_v0/hourly'
DEFAULT_PRE=OUT/'r4_rolling_gap_owner_fresh24_preregistered.json'
CFGS=('ROLL_KEEP_GAP_OWNER','ROLL_KEEP_GAP_OWNER_STRIKE_MOD_WEAK_SYNTH','ROLL_KEEP_GAP_OWNER_STRIKE_MOD_WEAK_REVERSE_SYNTH')

def load_market(mid:int):
    ps=sorted((ROOT/'data/hft_forward_paper_v1/markets').glob(f'{mid}_*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for p in ps:
        try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
        except:continue
        if int(d.get('marketId') or 0)==mid and 'R2_RESIDUAL' in str(d.get('student') or '') and d.get('orderMeta'):return d
    raise RuntimeError(f'no frozen R2 forward market {mid}')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--pre',type=Path,default=DEFAULT_PRE);ap.add_argument('--offset',type=int,default=0);ap.add_argument('--count',type=int,default=8);a=ap.parse_args()
    ids=json.load(open(a.pre,encoding='utf-8'))['marketIds'][a.offset:a.offset+a.count];rows=[];errs=[]
    for mid in ids:
        d=load_market(int(mid))
        for cfg in CFGS:
            try:r=roll.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:errs.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
    ag={c:roll.agg([r for r in rows if r['config']==c]) for c in CFGS}
    by={}
    for mid in ids:
        by[str(mid)]={}
        for c in CFGS:
            r=next((x for x in rows if x['marketId']==mid and x['config']==c),None)
            if r:by[str(mid)][c]={'durable':r['durableBase'],'everSafe':r['everSafe'],'floor':r['final']['floor'],'absNet':r['final']['absNet'],'makerFilled':r['makerFilledShares'],'early':r['earlyMakerFillShares'],'surplus':r['earlySurplusFillShares'],'damage':r['earlyFloorDamage'],'synthIntents':r['counts'].get('synthIntents',0),'synthFillShares':r['counts'].get('synthFillShares',0),'synthEligible':r['counts'].get('synthModeEligible',0),'synthBlockedExisting':r['counts'].get('synthBlockedExistingOwner',0),'synthBlockedThin':r['counts'].get('synthBlockedThinGate',0),'synthBlockedMPQ':r['counts'].get('synthBlockedMPQ',0)}
    tag=a.pre.stem.replace('r4_','')
    rep={'version':'R4_STRIKE_FORMATION_AUTHORITY_CEILING_V1','researchOnly':True,'notRuntimePromotable':True,'preFile':str(a.pre),'offset':a.offset,'ids':ids,'semantics':{'baseline':'GAP_OWNER unchanged','supportSynth':'After baseline acquisition, when current realized absNet>=18, identify weak side from realized state; if Predict edge 0.10-0.30 and strict-past spot-vs-strike supports current dominant, create one full-18 weak-side passive intent only if no live weak owner, thin queue passes, current gap absorbs 18, and MPQ passes. No pMaker-side support required.','reverseSynth':'Identical but strike-vs-dominant direction reversed as falsification control.'},'guards':{'receiptClockPublic':True,'full18Only':True,'noWinner':True,'noDreamFill':True,'riskQueueHFT':True,'liveChanges':False},'aggregate':ag,'byMarket':by,'errors':errs,'rows':rows}
    p=OUT/f'r4_strike_formation_authority_ceiling_v1_{tag}_o{a.offset}_n{len(ids)}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)),'aggregate':ag,'errors':errs},ensure_ascii=False))
if __name__=='__main__':main()
