from __future__ import annotations
import argparse, json, math, statistics, tempfile, zipfile
from collections import Counter
from pathlib import Path

from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=1e-9
REPAIR={'ECONOMIC_CORE','SATELLITE_REPAIR'}
EXPAND={'PROBE_CORE','SATELLITE_EXPAND'}

def median(xs):
    return statistics.median(xs) if xs else None

def action_class(role):
    if role in REPAIR:return 'REPAIR'
    if role in EXPAND:return 'EXPAND'
    return 'OTHER'

def summarize_market(mid,winner,r,sim):
    # Physical fills: use fill_accounting rows, one row per confirmed fill allocation clock.
    fills=[]
    for a in getattr(sim,'fill_accounting',[]):
        q=float(a.get('confirmedQty') or 0.0)
        if q<=EPS:continue
        role=str(a.get('role') or sim.key_role.get(str(a.get('key')),''))
        cls=action_class(role)
        fills.append({'t':int(a.get('t') or 0),'key':a.get('key'),'role':role,'class':cls,'qty':q,
                      'repairQty':float(a.get('matchedRepairQty') or 0.0),'overflowQty':float(a.get('overflowQty') or 0.0)})
    fills.sort(key=lambda x:(x['t'],str(x['key'])))
    seq=[x['class'] for x in fills if x['class'] in {'REPAIR','EXPAND'}]
    transitions=Counter(zip(seq,seq[1:]))
    alts=sum(a!=b for a,b in zip(seq,seq[1:]))
    repair_rows=[x for x in fills if x['class']=='REPAIR']
    expand_rows=[x for x in fills if x['class']=='EXPAND']
    service_fracs=[]
    for x in repair_rows:
        service_fracs.append(x['repairQty']/x['qty'] if x['qty']>EPS else 0.0)
    # Concurrent live Repair+Expand is sampled from submit-state structural rows indirectly via slot history.
    # Here compute an event-clock occupancy reconstruction from submit/release/fill-independent slot history.
    keyclass={k:action_class(str(sim.key_role.get(k,''))) for k in sim.orders}
    live=set(); concurrent_receipts=0; sampled=0; max_live=0
    for e in getattr(sim,'slot_history',[]):
        ev=str(e.get('event') or '')
        k=e.get('key')
        if 'SUBMIT' in ev and k: live.add(str(k))
        elif ev=='SLOT_RELEASE' and k: live.discard(str(k))
        if ev in {'SLOT_FILL','SLOT_RELEASE'} or 'SUBMIT' in ev or 'CANCEL' in ev:
            sampled+=1
            classes={keyclass.get(k,'OTHER') for k in live}
            if 'REPAIR' in classes and 'EXPAND' in classes:concurrent_receipts+=1
            max_live=max(max_live,len(live))
    # Define completed role-cycle as EXPAND->REPAIR->EXPAND in physical class sequence.
    cycles=0
    for i in range(len(seq)-2):
        if seq[i:i+3]==['EXPAND','REPAIR','EXPAND']:cycles+=1
    pnl=float(r['upQty' if winner=='UP' else 'downQty'])-float(r['buyNotional'])
    floor=float(r['floor']);best=float(r['best'])
    qsum=r.get('quantityLedgerSummary') or {}
    return {
        'marketId':mid,'winnerPostHocOnly':winner,'pnlDiagnosticOnly':pnl,'floor':floor,'best':best,
        'submits':int(r.get('submits') or 0),'fillEvents':int(r.get('fillEvents') or 0),'physicalAccountingRows':len(fills),
        'roleSequenceLen':len(seq),'roleAlternations':alts,'roleAlternationRate':alts/max(1,len(seq)-1),
        'expandRepairTransitions':int(transitions.get(('EXPAND','REPAIR'),0)),
        'repairExpandTransitions':int(transitions.get(('REPAIR','EXPAND'),0)),
        'expandExpandTransitions':int(transitions.get(('EXPAND','EXPAND'),0)),
        'repairRepairTransitions':int(transitions.get(('REPAIR','REPAIR'),0)),
        'expandFills':len(expand_rows),'repairFills':len(repair_rows),
        'repairServiceFractionMedian':median(service_fracs),'repairServiceFractionMean':sum(service_fracs)/len(service_fracs) if service_fracs else None,
        'repairFullServiceFillRate':sum(x>=1-EPS for x in service_fracs)/len(service_fracs) if service_fracs else None,
        'expandRepairExpandCycles':cycles,
        'concurrentRepairExpandSamples':concurrent_receipts,'occupancySamples':sampled,
        'concurrentRepairExpandRate':concurrent_receipts/sampled if sampled else 0.0,'maxLiveSlotsObserved':int(r.get('maxSimultaneousSlots') or max_live),
        'twoSidedMaterialized':bool(r.get('twoSidedMaterialized')),
        'ledgerInvariantViolations':qsum.get('invariantViolations'),
        'managedPassiveSubmits':int((r.get('quantityLadderCounters') or {}).get('managedPassiveSubmits',0)),
        'managedActiveSubmits':int((r.get('quantityLadderCounters') or {}).get('managedActiveSubmits',0)),
        'managedPassivePhysicalSuccess':int((r.get('quantityLadderCounters') or {}).get('managedPassivePhysicalSuccess',0)),
        'managedActivePhysicalSuccess':int((r.get('quantityLadderCounters') or {}).get('managedActivePhysicalSuccess',0)),
    }

def agg(rows):
    n=len(rows);pn=[x['pnlDiagnosticOnly'] for x in rows]
    def mean(k):
        xs=[x[k] for x in rows if x.get(k) is not None]
        return sum(xs)/len(xs) if xs else None
    tot_seq=sum(x['roleSequenceLen'] for x in rows);tot_trans=max(0,tot_seq-n)
    er=sum(x['expandRepairTransitions'] for x in rows);re=sum(x['repairExpandTransitions'] for x in rows)
    return {
      'markets':n,'wins':sum(x>EPS for x in pn),'losses':sum(x<-EPS for x in pn),'winRate':sum(x>EPS for x in pn)/n if n else None,
      'totalPnl':sum(pn),'avgPnl':mean('pnlDiagnosticOnly'),'worstPnl':min(pn) if pn else None,'bestPnl':max(pn) if pn else None,
      'avgFloor':mean('floor'),'avgBest':mean('best'),'avgFills':mean('fillEvents'),'avgSubmits':mean('submits'),
      'avgRoleAlternationRate':mean('roleAlternationRate'),'aggregateRoleAlternationRate':sum(x['roleAlternations'] for x in rows)/tot_trans if tot_trans else None,
      'expandToRepairRate':er/max(1,sum(x['expandRepairTransitions']+x['expandExpandTransitions'] for x in rows)),
      'repairToExpandRate':re/max(1,sum(x['repairExpandTransitions']+x['repairRepairTransitions'] for x in rows)),
      'avgRepairServiceFraction':mean('repairServiceFractionMean'),'avgRepairFullServiceFillRate':mean('repairFullServiceFillRate'),
      'marketsWithAtLeast1ERAcycle':sum(x['expandRepairExpandCycles']>=1 for x in rows),'marketsWithAtLeast2ERAcycles':sum(x['expandRepairExpandCycles']>=2 for x in rows),
      'totalERAcycles':sum(x['expandRepairExpandCycles'] for x in rows),'avgERAcycles':mean('expandRepairExpandCycles'),
      'avgConcurrentRepairExpandRate':mean('concurrentRepairExpandRate'),'maxLiveSlotsObserved':max((x['maxLiveSlotsObserved'] for x in rows),default=0),
      'ledgerInvariantMarkets':sum(bool(x.get('ledgerInvariantViolations')) for x in rows),
      'totalManagedPassiveSubmits':sum(x['managedPassiveSubmits'] for x in rows),'totalManagedActiveSubmits':sum(x['managedActiveSubmits'] for x in rows)
    }

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_main_v3b_') as td:
      root=Path(td)
      with zipfile.ZipFile(a.bundle) as z:
        co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
        for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
      for i,mid in enumerate(mids,1):
        sim=v3b.FifoAggregateResponsibilityLadderV3B(root/'tapes'/f'{mid}.json.xz')
        try:
          r=sim.run_qty('__UNSCORED__');row=summarize_market(mid,str(co[mid]['winner']).upper(),r,sim)
        finally:sim.close()
        rows.append(row);print(json.dumps({'progress':i,'of':len(mids),'marketId':mid,'pnl':row['pnlDiagnosticOnly'],'fills':row['fillEvents'],'altRate':row['roleAlternationRate'],'ER':row['expandRepairTransitions'],'RE':row['repairExpandTransitions'],'cycles':row['expandRepairExpandCycles']},ensure_ascii=False),flush=True)
    out={'version':'MANAGEMENT_MAINLINE_V3B_WHOLEMARKET_BASELINE_V1_20260907','researchOnly':True,'runtimeAuthority':False,'rows':rows,'aggregate':agg(rows),
         'targetReferenceDescriptiveOnly':{'actionsPerMarketApprox':29.7,'roleAlternationApprox':0.524,'expandToRepairApprox':0.649,'repairToExpandApprox':0.427,'source':'BTC5M_LAB_HANDOFF_MANAGEMENT_TRAINING_V1_20260907 section 7.3'},
         'boundary':['whole historical HFT baseline','V3B exact FIFO unchanged','winner only posthoc PnL','no action mutation','no Target runtime input','no NEW24-B','no dream fill','no 8781']}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
