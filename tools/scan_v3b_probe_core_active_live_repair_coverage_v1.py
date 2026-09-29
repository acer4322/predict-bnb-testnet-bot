from __future__ import annotations
import argparse,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS; REPAIR_ROLES={'ECONOMIC_CORE','SATELLITE_REPAIR'}

def live_at(hist,t):
    live={}
    for e in hist:
        if int(e.get('t',0))>t:break
        ev=e.get('event');k=e.get('key')
        if ev=='ROLE_SLOT_SUBMIT':
            live[k]={'role':str(e.get('role')),'side':str(e.get('side')),'price':float(e.get('price')),'qty':float(e.get('qty')),'cum':0.0}
        elif ev=='ROLE_FILL' and k in live:
            live[k]['cum']=float(e.get('cum',live[k]['cum']))
        elif ev=='SLOT_RELEASE': live.pop(k,None)
    for x in live.values():x['remaining']=max(0.0,x['qty']-x['cum'])
    return live

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='probe_core_cov_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            sim=v3b.FifoAggregateResponsibilityLadderV3B(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_qty('__UNSCORED__')
            finally:sim.close()
            resp={int(x['id']):x for x in r['quantityResponsibilities']};ev=r['quantityLadderEvents'];qual=[]
            for j,e in enumerate(ev):
                if e.get('event')!='QTY_FIFO_MANAGED_PASSIVE_SUBMIT' or e.get('role')!='ECONOMIC_CORE':continue
                old=float(e.get('oldestRemaining') or 0);agg=float(e.get('aggregateRemaining') or 0);qty=float(e.get('qty') or 0)
                if not(old+EPS<qty<=agg+EPS):continue
                lot=resp.get(int(e['originResponsibilityId']));mix=(lot or {}).get('sourceRoleMix') or {};support={str(k) for k,v in mix.items() if float(v)>EPS}
                if support!={'PROBE_CORE'}:continue
                key=e['key'];term=next((x for x in ev[j+1:] if x.get('event')=='QTY_FIFO_MANAGED_PASSIVE_TERMINAL' and x.get('key')==key),None);act=next((x for x in ev[j+1:] if x.get('event')=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT' and x.get('sourceKey')==key),None)
                if term is None or act is None or float(term.get('cum') or 0)>EPS:continue
                t=int(act['t']);live=live_at(r['slotHistory'],t);repair_side=str(act['side']);cover=[{'key':k,**x} for k,x in live.items() if x['side']==repair_side and x['role'] in REPAIR_ROLES and x['remaining']>EPS];cover_qty=sum(x['remaining'] for x in cover)
                qual.append({'passiveT':int(e['t']),'activeT':t,'originResponsibilityId':int(e['originResponsibilityId']),'repairSide':repair_side,'targetExpandSide':e['targetExpandSide'],'aggregateRemaining':agg,'activeTargetRemaining':float(act['targetAggregateRemaining']),'activeLimit':float(act['limitPrice']),'activeQty':float(act['qty']),'originPrice':float(lot['price']),'activePairSum':float(lot['price'])+float(act['limitPrice']),'liveRepairCoverageCount':len(cover),'liveRepairCoverageQty':cover_qty,'coverageRatioToTarget':cover_qty/max(EPS,float(act['targetAggregateRemaining'])),'liveRepairCoverage':cover})
            row={'marketId':mid,'qualifyingEvents':qual,'qualifyingCount':len(qual),'hasLiveRepairCoverage':any(x['liveRepairCoverageQty']>EPS for x in qual),'ledgerInvariantViolations':r['quantityLedgerSummary']['invariantViolations']};rows.append(row)
            print(json.dumps({'progress':i,'marketId':mid,'qualifying':len(qual),'hasCoverage':row['hasLiveRepairCoverage'],'coverage':[round(x['liveRepairCoverageQty'],6) for x in qual]},ensure_ascii=False),flush=True)
    out={'version':'V3B_PROBE_CORE_ACTIVE_LIVE_REPAIR_COVERAGE_SCAN_V1','date':'2026-09-06','researchOnly':True,'runtimeAuthority':False,'winnerRead':False,'pnlComputed':False,'markets':mids,'rows':rows,'coverageMarkets':[r['marketId'] for r in rows if r['hasLiveRepairCoverage']],'zeroCoverageMarkets':[r['marketId'] for r in rows if r['qualifyingCount'] and not r['hasLiveRepairCoverage']],'boundary':['frozen V3B only','strict-past live order state at Active decision','same-side ECONOMIC_CORE/SATELLITE_REPAIR remaining qty is execution coverage telemetry','no candidate outcome/no PnL/no winner','no NEW24-B']}
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'coverageMarkets':out['coverageMarkets'],'zeroCoverageMarkets':out['zeroCoverageMarkets']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
