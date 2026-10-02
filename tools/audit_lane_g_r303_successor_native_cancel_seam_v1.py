from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import audit_lane_g_r303_successor_blocker_decomposition_v1 as blk
EPS=blk.EPS; v2=blk.reach.v2

class SuccessorNativeCancelSeamAudit(blk.SuccessorBlockerAudit):
    def __init__(self,tape,mid):
        super().__init__(tape,mid)
        self.nativeCancelSeams=[]

    def _candidate_snapshot(self,side):
        try:c=self._candidate_from_levels_v8(side,'ECONOMIC_CORE',False)
        except Exception as e:return {'ok':False,'reason':'CANDIDATE_ERROR','detail':type(e).__name__}
        if c is None:return {'ok':False,'reason':'NO_LEGAL_PASSIVE_CANDIDATE'}
        p,q,proj,split=c
        return {'ok':True,'price':float(p),'qty':float(q),'projected':float(proj),
                'repairQty':float((split or {}).get('repairQty') or 0.0),'overflowQty':float((split or {}).get('overflowQty') or 0.0),
                'overflowRisk':float((split or {}).get('overflowRisk') or 0.0)}

    def _request_cancel(self,t,sid,reason):
        key=self.slot_key.get(int(sid));o=self.orders.get(key) if key is not None else None
        ob=self._succ_ob()
        if ob is not None and key in self.riskRepairCarrierKeys and o is not None and not o.get('cancelRequested'):
            gen=int(ob['generation'])
            if int(self.key_scope_gen.get(key,-1))==gen and float(ob.get('outstanding') or 0.0)>EPS:
                try:s=self.snap(o);status=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0);leaves=s.get('leavesQty')
                except Exception:status='';cum=float(o.get('cum') or 0.0);leaves=None
                if cum<=EPS and status not in v2.TERMINAL_STATUSES:
                    qv=v2.base.quotes(self.book);side=str(o.get('side'));qside=(qv or {}).get(side,{})
                    live=self._live_dedicated_repair_rows(gen)
                    quota=sum(max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0))) for k,_ in live)
                    self.nativeCancelSeams.append({'t':int(t),'generation':gen,'key':str(key),'slotId':int(sid),'reason':str(reason),
                      'side':side,'role':str(self.key_role.get(key) or ''),'ownPrice':float(o.get('price') or 0.0),'qty':float(o.get('qty') or 0.0),
                      'cum':cum,'leavesQty':float(leaves) if leaves is not None else None,'status':status,
                      'obligation':{'bornQty':float(ob.get('bornQty') or 0.0),'outstanding':float(ob.get('outstanding') or 0.0),'repaidQty':float(ob.get('repaidQty') or 0.0),
                                    'passiveRepaidQty':float(ob.get('passiveRepaidQty') or 0.0),'activeRepaidQty':float(ob.get('activeRepaidQty') or 0.0)},
                      'liveRepairCount':len(live),'representedQuota':float(quota),'representationMargin':float(quota)-float(ob.get('outstanding') or 0.0),
                      'book':{'bid':float(qside.get('bid')) if qside.get('bid') is not None else None,'ask':float(qside.get('ask')) if qside.get('ask') is not None else None},
                      'replacementCandidate':self._candidate_snapshot(side),'slots':len(self.slot_key),'active':len(self.activeKeys),
                      'scopeDebt':float(self._scope_debt_qty()),'thesisSide':self.intentThesisSide,'repairSide':self._repair_side(),
                      'availableExpandCredit':float(self._available_expand_risk_credit()),'riskAuthorityCurrentGeneration':float(self._risk_authority_current_generation())})
        return super()._request_cancel(t,sid,reason)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',default='1946317,1946640');ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='lane_g_r303_succ_cancel_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];suggest=[]
        for m in mids:
            s=SuccessorNativeCancelSeamAudit(tmp/f'{m}.json.xz',m)
            try:r=s.run_audit(co[m]['winner'])
            finally:s.close()
            first=s.nativeCancelSeams[0] if s.nativeCancelSeams else None
            row={'marketId':m,'seamCount':len(s.nativeCancelSeams),'firstSeam':first,'allSeams':s.nativeCancelSeams[:200],
                 'successorBirths':s.successorBirths,'correct':bool(r.get('residualCorrect'))}
            rows.append(row)
            if first:suggest.append({'marketId':m,'t':first['t'],'generation':first['generation'],'key':first['key'],'reason':first['reason']})
            print(json.dumps({'marketId':m,'seamCount':len(s.nativeCancelSeams),'firstSeam':first,'correct':row['correct']},ensure_ascii=False),flush=True)
        out={'version':'LANE_G_R303_SUCCESSOR_NATIVE_CANCEL_SEAM_AUDIT_V1_20260907','researchOnly':True,'runtimeAuthority':False,'behaviorMutation':False,
             'rows':rows,'preregisterSuggestionOutcomeBlind':suggest,'gates':{'correctnessPass':all(r['correct'] for r in rows),'seamFound':bool(suggest)},
             'selectionBoundary':['V1C KEEP_SHARED_PARENT_RESIDUAL lineage only','first native cancel request per market targeting successor-generation dedicated Repair carrier','carrier must be live and confirmed-fill zero','successor obligation outstanding must be positive','selection uses chronology/state only; no winner/terminal outcome'],
             'policyBoundary':['observation only','no fixed seconds/windows/rank/age gate','no action mutation','consumed only','fresh untouched','realistic HFT','no dream fill','no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'gates':out['gates'],'preregisterSuggestion':suggest},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
