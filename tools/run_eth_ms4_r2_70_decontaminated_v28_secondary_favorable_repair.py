from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
v2=r257.v2; EPS=1e-9

class DecontaminatedV28SecondaryFavorableRepairSim(r257.RiskFillPassiveRepairObligationSim):
    """R2.70: one distinct-price favorable passive Repair option beside frozen R2.57 liveness Repair.

    This is explicitly a decontaminated V28 replication.  The extra carrier:
      * never replaces the primary dedicated Repair carrier;
      * uses one spare physical max4 slot;
      * is fully covered by currently-unreserved native Repair debt at submit;
      * is cheaper than the live primary Repair carrier;
      * pairs favorably with the confirmed risk-tranche entry price;
      * uses frozen V8 Repair allocation/credit accounting after actual fill.
    """
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r270=Counter(); self.r270Events=[]
        self.secondaryKeys=set(); self.secondaryGenUsed=set(); self.secondaryMeta={}

    def _live_primary_rows(self,gen):
        out=[]
        for key in list(self.riskRepairCarrierKeys):
            if int(self.key_scope_gen.get(key,-1))!=int(gen): continue
            o=self.orders.get(key)
            if not o: continue
            try: st=str(self.snap(o).get('status') or '').upper()
            except Exception: st=''
            if st in v2.TERMINAL_STATUSES or o.get('cancelRequested'): continue
            rem=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
            if rem>EPS: out.append((key,o,rem))
        return out

    def _secondary_live(self,gen):
        for key in self.secondaryKeys:
            if int(self.key_scope_gen.get(key,-1))!=int(gen): continue
            o=self.orders.get(key)
            if not o: continue
            try: st=str(self.snap(o).get('status') or '').upper()
            except Exception: st=''
            if st not in v2.TERMINAL_STATUSES and not o.get('cancelRequested'): return True
        return False

    def _risk_entry_price(self,ob):
        key=str(ob.get('bornFromRiskKey') or '')
        x=self.riskTrancheMeta.get(key)
        if not x: return None
        p=float(x.get('price') or 0.0)
        return p if p>EPS else None

    def _try_secondary(self,t,end):
        if self.scopeSide is None or int(end)-int(t)<=v2.NO_NEW_EXPOSURE_MS: return False
        ob=self._obligation_current()
        if not ob: return False
        gen=int(ob['generation'])
        if gen in self.secondaryGenUsed or self._secondary_live(gen): return False
        if self._has_stale_scope_reservation():
            self.r270['BLOCK_STALE_SCOPE']+=1; return False
        if len(self.slot_key)+len(self.activeKeys)>=self.max_slots:
            self.r270['BLOCK_SHARED_CAPACITY']+=1; return False
        prim=self._live_primary_rows(gen)
        if not prim:
            self.r270['BLOCK_NO_LIVE_PRIMARY']+=1; return False
        risk_p=self._risk_entry_price(ob)
        if risk_p is None:
            self.r270['BLOCK_NO_RISK_ENTRY_PRICE']+=1; return False
        repair_side=self._repair_side()
        used=self._used_prices(repair_side)
        primary_prices=[float(o['price']) for _,o,_ in prim]
        primary_floor=min(primary_prices)
        chosen=None
        for raw in self._live_price_levels(repair_side):
            p=float(v2.kprice(raw))
            if p<=EPS or p in used: continue
            if p>=primary_floor-EPS: continue  # secondary is the cheaper passive option
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS: continue
            if risk_p+p>1.0+EPS: continue
            sp=self._repair_split(repair_side,p,q)
            if sp is None: continue
            if float(sp.get('overflowQty') or 0.0)>EPS: continue
            chosen=(p,q,sp); break
        if chosen is None:
            self.r270['BLOCK_NO_FULLY_COVERED_FAVORABLE_SECONDARY']+=1; return False
        p,q,sp=chosen
        before_n=self.n
        if not self._submit_role_v8(t,repair_side,'SATELLITE_REPAIR',p,q,sp['fullFloor'],sp):
            self.r270['BLOCK_SUBMIT']+=1; return False
        key=f'{repair_side}_{before_n}'
        self.secondaryKeys.add(key); self.secondaryGenUsed.add(gen)
        meta={'key':key,'generation':gen,'riskKey':ob.get('bornFromRiskKey'),'riskPrice':risk_p,
              'side':repair_side,'price':p,'qty':q,'pairSumAtSubmit':risk_p+p,
              'pairEdgePerShareAtSubmit':1.0-risk_p-p,'submittedAt':int(t),
              'primaryPrices':primary_prices,'obligationOutstanding':float(ob['outstanding']),
              'nativeScopeDebt':float(self._scope_debt_qty()),'reservedRepairAfter':float(self._reserved_repair_quota(repair_side)),
              'fillQty':0.0,'fillEvents':0,'realizedPairEdge':0.0,'terminal':None}
        self.secondaryMeta[key]=meta; self.r270['SUBMIT']+=1
        ev={'t':int(t),'event':'R270_SECONDARY_FAVORABLE_REPAIR_SUBMIT',**meta}
        self.r270Events.append(ev); self.slot_history.append(ev)
        return True

    def _open_one_option(self,t,qv,end):
        # Frozen R2.57 owns risk initiation and the primary liveness Repair path.
        super()._open_one_option(t,qv,end)
        # Secondary option is opportunistic only; same-receipt native throttle remains authoritative.
        self._try_secondary(t,end)

    def process(self,t):
        start=len(self.splitEvents)
        super().process(t)
        for ev in self.splitEvents[start:]:
            if ev.get('event')!='ROLE_FILL_SPLIT': continue
            key=str(ev.get('key'))
            if key not in self.secondaryMeta: continue
            inc=float(ev.get('fillInc') or 0.0)
            rq=float(ev.get('repairAllocated') or 0.0)
            if inc<=EPS: continue
            x=self.secondaryMeta[key]; x['fillQty']+=inc; x['fillEvents']+=1
            # The risk anchor is the actual confirmed risk-tranche entry price.
            edge=(1.0-float(x['riskPrice'])-float(ev.get('price') or x['price']))*rq
            x['realizedPairEdge']+=edge
            self.r270['FILL']+=1; self.r270['FILL_QTY_MILLI']+=int(round(inc*1000))
            self.r270['REPAIR_QTY_MILLI']+=int(round(rq*1000))
            self.r270Events.append({'t':int(t),'event':'R270_SECONDARY_FAVORABLE_REPAIR_FILL','key':key,
                'fillQty':inc,'repairAllocated':rq,'price':float(ev.get('price') or x['price']),
                'riskPrice':float(x['riskPrice']),'pairSum':float(x['riskPrice'])+float(ev.get('price') or x['price']),
                'realizedPairEdge':edge})
        for key,x in self.secondaryMeta.items():
            if x['terminal'] is not None: continue
            o=self.orders.get(key)
            if not o: continue
            try: st=str(self.snap(o).get('status') or '').upper()
            except Exception: st=''
            if st in v2.TERMINAL_STATUSES and key not in self.slot_key.values():
                x['terminal']=st; self.r270['TERMINAL']+=1
                if float(x['fillQty'])<=EPS: self.r270['ZERO_FILL_TERMINAL']+=1
                self.r270Events.append({'t':int(t),'event':'R270_SECONDARY_TERMINAL','key':key,'status':st,'fillQty':x['fillQty']})

    def run_r270(self,winner):
        r=super().run_r257(winner)
        edge=sum(float(x.get('realizedPairEdge') or 0.0) for x in self.secondaryMeta.values())
        correct=bool(r.get('r255CorrectnessPass')) and float(r.get('repairQuotaExcessMax',0.0))<=EPS and float(r.get('unauthorizedOverflowQty',0.0))<=EPS
        r.update({'r270Version':'MS4_R2_70_DECONTAMINATED_V28_SECONDARY_FAVORABLE_REPAIR_V1',
                  'r270Stats':dict(self.r270),'r270Events':self.r270Events[:3000],
                  'r270SecondarySubmits':int(self.r270.get('SUBMIT',0)),
                  'r270SecondaryFills':int(self.r270.get('FILL',0)),
                  'r270SecondaryFillQty':float(self.r270.get('FILL_QTY_MILLI',0))/1000.0,
                  'r270SecondaryRepairQty':float(self.r270.get('REPAIR_QTY_MILLI',0))/1000.0,
                  'r270SecondaryRealizedPairEdge':float(edge),
                  'r270SecondaryZeroFillTerminals':int(self.r270.get('ZERO_FILL_TERMINAL',0)),
                  'r270SecondaryState':list(self.secondaryMeta.values()),
                  'r270CorrectnessPass':bool(correct)})
        return r

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--bundle',required=True); ap.add_argument('--market-ids',required=True); ap.add_argument('--output',required=True)
    a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; tmp=Path(tempfile.mkdtemp(prefix='ms4_r270_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for mid in mids:(tmp/f'{mid}.json.xz').write_bytes(z.read(f'tapes/{mid}.json.xz'))
        rows=[]; cmp=[]
        for mid in mids:
            w=co[mid]['winner']; tape=tmp/f'{mid}.json.xz'
            bsim=r257.RiskFillPassiveRepairObligationSim(tape,1,4)
            try:b=bsim.run_r257(w)
            finally:bsim.close()
            sim=DecontaminatedV28SecondaryFavorableRepairSim(tape,1,4)
            try:c=sim.run_r270(w)
            finally:sim.close()
            rows += [{'marketId':mid,'cell':'R257_CONTROL','winnerPostHocOnly':w,**b},
                     {'marketId':mid,'cell':'R270_SECONDARY_FAVORABLE_REPAIR','winnerPostHocOnly':w,**c}]
            d={'marketId':mid,'secondarySubmits':c['r270SecondarySubmits'],'secondaryFills':c['r270SecondaryFills'],
               'secondaryQty':c['r270SecondaryFillQty'],'secondaryPairEdge':c['r270SecondaryRealizedPairEdge'],
               'pnlDelta':float(c['pnlDiagnosticOnly'])-float(b['pnlDiagnosticOnly']),
               'floorDelta':float(c['floor'])-float(b['floor']),'bestDelta':float(c['best'])-float(b['best']),
               'gapDelta':(float(c['best'])-float(c['floor']))-(float(b['best'])-float(b['floor'])),
               'fillDelta':int(c['fillEvents'])-int(b['fillEvents']),'submitDelta':int(c['submits'])-int(b['submits']),
               'candidatePnl':float(c['pnlDiagnosticOnly']),'candidateFloor':float(c['floor']),'candidateBest':float(c['best']),
               'bestGt2':float(c['best'])>2.0,'floorGtMinus1':float(c['floor'])>-1.0,
               'asymmetricPackage':float(c['best'])>2.0 and float(c['floor'])>-1.0,
               'correct':bool(c['r270CorrectnessPass']),'repairQuotaExcessMax':float(c.get('repairQuotaExcessMax',0.0)),
               'unauthorizedOverflowQty':float(c.get('unauthorizedOverflowQty',0.0))}
            cmp.append(d); print(json.dumps(d,ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_70_DECONTAMINATED_V28_SECONDARY_FAVORABLE_REPAIR_RESULT_V1','researchOnly':True,'markets':mids,
             'rows':rows,'comparison':cmp,
             'gates':{'correctnessPass':all(x['correct'] for x in cmp),'secondarySubmitExercised':any(x['secondarySubmits']>0 for x in cmp),
                      'secondaryFillExercised':any(x['secondaryFills']>0 for x in cmp)},
             'boundary':['decontaminated V28 replication','R2.57 primary liveness Repair frozen','at most one secondary option per risk generation','secondary cheaper than primary and favorable vs confirmed risk entry','secondary fully covered by currently unreserved native Repair debt','no overflow/new-risk authority in secondary lane','max4','<=180s','no Target/winner/future runtime input','realistic HFT','no dream fill','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates']},ensure_ascii=False),flush=True)
    finally: shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__': main()
