from __future__ import annotations
import argparse,json,sys,tempfile,zipfile
from pathlib import Path
ROOT=Path.cwd().resolve() if (Path.cwd()/"tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b
EPS=v3b.EPS

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    with tempfile.TemporaryDirectory(prefix='v3b_probe_core_scan_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            sim=v3b.FifoAggregateResponsibilityLadderV3B(root/'tapes'/f'{mid}.json.xz')
            try:r=sim.run_qty('__UNSCORED__')
            finally:sim.close()
            resp={int(x['id']):x for x in r.get('quantityResponsibilities',[])};ev=r.get('quantityLadderEvents',[]);qual=[]
            for j,e in enumerate(ev):
                if e.get('event')!='QTY_FIFO_MANAGED_PASSIVE_SUBMIT' or str(e.get('role'))!='ECONOMIC_CORE':continue
                old=float(e.get('oldestRemaining') or 0);agg=float(e.get('aggregateRemaining') or 0);qty=float(e.get('qty') or 0)
                if not (old+EPS<qty<=agg+EPS):continue
                lot=resp.get(int(e['originResponsibilityId']))
                if lot is None:continue
                mix=lot.get('sourceRoleMix') or {};support={str(k) for k,v in mix.items() if float(v)>EPS}
                if support!={'PROBE_CORE'}:continue
                key=str(e['key'])
                term=next((x for x in ev[j+1:] if x.get('event')=='QTY_FIFO_MANAGED_PASSIVE_TERMINAL' and str(x.get('key'))==key),None)
                active=next((x for x in ev[j+1:] if x.get('event')=='QTY_FIFO_MANAGED_ACTIVE_SUBMIT' and str(x.get('sourceKey'))==key),None)
                if term is None or active is None:continue
                if float(term.get('cum') or 0)>EPS:continue
                qual.append({'t':int(e['t']),'originResponsibilityId':int(e['originResponsibilityId']),'targetExpandSide':e['targetExpandSide'],'repairSide':e['side'],'passiveKey':key,'passivePrice':float(e['price']),'carrierQty':qty,'oldestRemaining':old,'aggregateRemaining':agg,'sourceRoleMix':mix,'passiveTerminalT':int(term['t']),'passiveStatus':term.get('status'),'activeT':int(active['t']),'activeLimit':float(active['limitPrice']),'activeQty':float(active['qty'])})
            row={'marketId':mid,'qualifyingCount':len(qual),'qualifyingEvents':qual,'ledgerInvariantViolations':r['quantityLedgerSummary']['invariantViolations'],'activityDiagnostic':{'submits':int(r['submits']),'fills':int(r['fillEvents']),'alternations':int(r['fillSideAlternations']),'twoSided':bool(r.get('twoSidedMaterialized'))}}
            rows.append(row);print(json.dumps({'progress':i,'of':len(mids),'marketId':mid,'qualifyingCount':len(qual),'ledger':row['ledgerInvariantViolations']},ensure_ascii=False),flush=True)
    out={'version':'V3B_INITIAL_PROBE_CORE_ACTIVE_STRUCTURAL_SCAN_V1','date':'2026-09-06','researchOnly':True,'actionAuthority':False,'winnerRead':False,'pnlComputed':False,'markets':mids,'rows':rows,'qualifyingMarketIds':[x['marketId'] for x in rows if x['qualifyingCount']>0],
         'boundary':['frozen V3B behavior only','no V3F execution','selection uses only exact FIFO lineage+role+cross-lot+terminal-zero-fill+baseline Active relay','winner/PnL not loaded','no NEW24-B/no 8781/no dream fill']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'markets':len(rows),'qualifyingMarkets':len(out['qualifyingMarketIds']),'qualifyingMarketIds':out['qualifyingMarketIds']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
