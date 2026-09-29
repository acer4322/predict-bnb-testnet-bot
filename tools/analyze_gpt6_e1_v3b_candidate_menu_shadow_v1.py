from __future__ import annotations
import argparse, json, math, os, tempfile, zipfile
from pathlib import Path
import tools.run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS
base=v3b.base
qshadow=v3b.qshadow

class E1CandidateMenuShadow(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape):
        super().__init__(tape)
        self.e1_events=[]
        self.e1_counts={
            'expandDecisionClocks':0,
            'repairAlternativeExists':0,
            'repairAlternativeUnclaimed':0,
            'repairAlternativeClaimedByLiveRepair':0,
            'repairAlternativeManagedClaim':0,
            'noPhysicalSlot':0,
        }

    def _pure_first_pair_candidate(self,side):
        used={round(float(x),10) for x in self._used_prices(side)}
        for raw in self._live_price_levels(side):
            p=base.v2.kprice(raw)
            if round(float(p),10) in used:
                continue
            if not self._pair_ok(side,p):
                continue
            return {'side':side,'price':float(p),'qty':float(1.0/p)}
        return None

    def _repair_role_for_side(self,side):
        return 'ECONOMIC_CORE' if self._core_for_side(side) is None else 'SATELLITE_REPAIR'

    def _repair_menu(self):
        out=[]
        managed_claim=bool(self.q_ladder is not None or self.q_pending_active is not None)
        for side in ('UP','DOWN'):
            debt=float(self._aggregate_for_repair_side(side))
            if debt<=EPS:
                continue
            cand=self._pure_first_pair_candidate(side)
            if cand is None:
                continue
            live_rep=[]
            for sid,key,o,role in self._live_role_rows(side=side):
                if role in v3b.REPAIR_ROLES:
                    live_rep.append({
                        'slotId':int(sid),'key':key,'role':role,'price':float(o.get('price') or 0.0),
                        'cancelRequested':bool(o.get('cancelRequested')),'cum':float(o.get('cum') or 0.0)
                    })
            out.append({
                **cand,
                'role':self._repair_role_for_side(side),
                'aggregateOutstandingDebt':debt,
                'managedClaimPresent':managed_claim,
                'liveRepairCarriers':live_rep,
                'unclaimed':(not managed_claim and len(live_rep)==0),
            })
        return out

    def _open_one_option(self,t,qv,end):
        if int(end)-int(t)>base.v2.NO_NEW_EXPOSURE_MS:
            side,role,_,_=base.MinimalPairRoleSim._role_decision(self,qv)
            if role=='SATELLITE_EXPAND':
                self.e1_counts['expandDecisionClocks']+=1
                baseline_cand=self._pure_first_pair_candidate(side)
                menu=self._repair_menu()
                if menu:self.e1_counts['repairAlternativeExists']+=1
                if any(x['managedClaimPresent'] for x in menu):self.e1_counts['repairAlternativeManagedClaim']+=1
                if any(x['liveRepairCarriers'] for x in menu):self.e1_counts['repairAlternativeClaimedByLiveRepair']+=1
                unclaimed=[x for x in menu if x['unclaimed']]
                if unclaimed:self.e1_counts['repairAlternativeUnclaimed']+=1
                free_slots=max(0,int(self.max_slots)-len(self.slot_key))
                if free_slots<=0:self.e1_counts['noPhysicalSlot']+=1
                self.e1_events.append({
                    't':int(t),
                    'baselineSide':side,'baselineRole':role,'baselineOrdinaryCandidate':baseline_cand,
                    'repairMenu':menu,'unclaimedRepairMenu':unclaimed,
                    'freeSlotsBeforeOpen':free_slots,
                    'slotCountBeforeOpen':len(self.slot_key),
                    'inventory':{'UP':float(self.inv['UP']),'DOWN':float(self.inv['DOWN'])},
                    'cost':float(self.cost),
                    'branchPayoffs':{'UP':float(self.inv['UP']-self.cost),'DOWN':float(self.inv['DOWN']-self.cost)},
                    'direction':base.MinimalPairRoleSim._direction(self,qv),
                })
        return super()._open_one_option(t,qv,end)

    def run_shadow(self):
        r=super().run_qty('__UNSCORED__')
        r['e1CandidateMenuShadow']=True
        r['e1Counts']=self.e1_counts
        r['e1Events']=self.e1_events[:10000]
        return r

def core_fields(r):
    return {
        'submits':r.get('submits'),'fillEvents':r.get('fillEvents'),'filledQty':r.get('filledQty'),
        'upQty':r.get('upQty'),'downQty':r.get('downQty'),'buyNotional':r.get('buyNotional'),
        'floor':r.get('floor'),'best':r.get('best'),'fillSideAlternations':r.get('fillSideAlternations'),
        'twoSidedMaterialized':r.get('twoSidedMaterialized'),'roleSubmits':r.get('roleSubmits'),
        'roleFills':r.get('roleFills'),'roleFillQty':r.get('roleFillQty'),'reanchors':r.get('reanchors'),
        'slotHistory':r.get('slotHistory'),'fillSideSequence':r.get('fillSideSequence'),
        'quantityPaymentRows':r.get('quantityPaymentRows'),'quantityLedgerSummary':r.get('quantityLedgerSummary'),
        'quantityResponsibilities':r.get('quantityResponsibilities')
    }

def eq(a,b):
    if isinstance(a,dict) and isinstance(b,dict):
        return set(a.keys())==set(b.keys()) and all(eq(a[k],b[k]) for k in a)
    if isinstance(a,list) and isinstance(b,list):
        return len(a)==len(b) and all(eq(x,y) for x,y in zip(a,b))
    if isinstance(a,(int,float)) and isinstance(b,(int,float)):
        return math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-10)
    return a==b

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    with tempfile.TemporaryDirectory(prefix='e1_menu_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:z.extract(f'tapes/{mid}.json.xz',root)
        for i,mid in enumerate(mids,1):
            tape=root/'tapes'/f'{mid}.json.xz'
            A=v3b.FifoAggregateResponsibilityLadderV3B(tape)
            try:ra=A.run_qty('__UNSCORED__')
            finally:A.close()
            B=E1CandidateMenuShadow(tape)
            try:rb=B.run_shadow()
            finally:B.close()
            pa,pb=core_fields(ra),core_fields(rb)
            parity={k:eq(pa[k],pb[k]) for k in pa}
            row={'marketId':mid,'winnerPostHocNotUsed':str(co[mid].get('winner') or '').upper(),
                 'allBehaviorParity':all(parity.values()),'parity':parity,
                 'ledgerViolations':rb['quantityLedgerSummary'].get('invariantViolations'),
                 'counts':rb['e1Counts'],'events':rb['e1Events']}
            rows.append(row)
            print(json.dumps({'progress':i,'marketId':mid,'parity':row['allBehaviorParity'],'ledgerViolations':row['ledgerViolations'],'counts':row['counts']},ensure_ascii=False),flush=True)
    eligible=[]
    for row in rows:
        for ev in row['events']:
            if ev['baselineOrdinaryCandidate'] is None or ev['freeSlotsBeforeOpen']<=0:continue
            for alt in ev['unclaimedRepairMenu']:
                eligible.append({'marketId':row['marketId'],'t':ev['t'],'baseline':ev['baselineOrdinaryCandidate'],'baselineSide':ev['baselineSide'],'baselineRole':ev['baselineRole'],'alternative':alt,'branchPayoffs':ev['branchPayoffs'],'inventory':ev['inventory'],'cost':ev['cost']})
    eligible.sort(key=lambda x:(x['t'],x['marketId'],x['alternative']['side'],x['alternative']['price']))
    out={'version':'GPT6_E1_V3B_CANDIDATE_MENU_SHADOW_V1','date':'2026-09-06','researchOnly':True,'markets':mids,
         'allBehaviorParity':all(x['allBehaviorParity'] for x in rows),'rows':rows,'eligibleCompetingRepairSeams':eligible,
         'firstEligibleSeams':eligible[:20],
         'boundary':['behavior-inert candidate menu only','baseline V3B exact-FIFO unchanged','only SATELLITE_EXPAND decision clocks inspected','alternative Repair uses ordinary Pair-Core distinct live price + Pair legality','unclaimed requires no managed Repair ownership and no live Repair-role carrier on candidate side','no winner/PnL/Target runtime input','realistic HFT','max4 unchanged','NEW24-B untouched','no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'allBehaviorParity':out['allBehaviorParity'],'eligibleSeams':len(eligible),'firstEligible':eligible[:3]},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
