from __future__ import annotations
import argparse,json,lzma
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_v1 as roll
from tools import test_r4_preposition_responsibility_prune_v2 as v2
OUT=ROOT/'data/research/r4_v0/hourly'
PRE=OUT/'r4_rolling_gap_owner_fresh24_preregistered.json'

def load_market(mid:int):
    ps=sorted((ROOT/'data/hft_forward_paper_v1/markets').glob(f'{mid}_*.json.xz'),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    for p in ps:
        try:d=json.load(lzma.open(p,'rt',encoding='utf-8'))
        except:continue
        if int(d.get('marketId') or 0)==mid and 'R2_RESIDUAL' in str(d.get('student') or '') and d.get('orderMeta'):return d
    raise RuntimeError(f'no frozen R2 forward market {mid}')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--offset',type=int,default=0);ap.add_argument('--count',type=int,default=8);a=ap.parse_args()
    ids=json.load(open(PRE,encoding='utf-8'))['marketIds'][a.offset:a.offset+a.count];rows=[];errs=[]
    for mid in ids:
        d=load_market(int(mid))
        for cfg in ('ROLL_KEEP_GAP_OWNER','ROLL_KEEP_FLAT_ACTIVE2'):
            try:r=roll.simulate(d,cfg);r['config']=cfg;rows.append(r)
            except Exception as e:errs.append({'marketId':mid,'config':cfg,'error':f'{type(e).__name__}:{e}'})
        print(json.dumps({'market':mid,'rows':len(rows),'errors':len(errs)},ensure_ascii=False),flush=True)
    ag={cfg:roll.agg([r for r in rows if r['config']==cfg]) for cfg in ('ROLL_KEEP_GAP_OWNER','ROLL_KEEP_FLAT_ACTIVE2')}
    by={}
    for mid in ids:
        g=next((r for r in rows if r['marketId']==mid and r['config']=='ROLL_KEEP_GAP_OWNER'),None);f=next((r for r in rows if r['marketId']==mid and r['config']=='ROLL_KEEP_FLAT_ACTIVE2'),None)
        if g and f:by[str(mid)]={'gapDurable':g['durableBase'],'flat2Durable':f['durableBase'],'gapEverSafe':g['everSafe'],'flat2EverSafe':f['everSafe'],'gapFloor':g['final']['floor'],'flat2Floor':f['final']['floor'],'gapAbsNet':g['final']['absNet'],'flat2AbsNet':f['final']['absNet'],'gapEarly':g['earlyMakerFillShares'],'flat2Early':f['earlyMakerFillShares'],'gapEarlySurplus':g['earlySurplusFillShares'],'flat2EarlySurplus':f['earlySurplusFillShares'],'flatSecondAcq':f['counts'].get('flatSecondAcquisitions',0)}
    rep={'version':'R4_FLAT_ACTIVE2_ABLATION_V1','researchOnly':True,'cohort':'same frozen fresh24 development cohort','offset':a.offset,'ids':ids,'semantics':'GAP_OWNER unchanged except FLAT candidate may acquire a second full-18 option only when 1-3 total live Maker owners already exist; admission still requires pMaker support + receipt-clock thin queue + MPQ. Once relation becomes WEAK, realized-gap ownership resumes; SURPLUS cancels.','aggregate':ag,'byMarket':by,'errors':errs,'rows':rows}
    p=OUT/f'r4_flat_active2_ablation_v1_o{a.offset}_n{len(ids)}.json';p.write_text(json.dumps(rep,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'artifact':str(p.relative_to(ROOT)).replace('\\','/'),'aggregate':ag,'byMarket':by,'errors':errs},ensure_ascii=False))
if __name__=='__main__':main()
