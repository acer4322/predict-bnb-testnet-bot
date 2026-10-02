"""Receipt-aware observation bridge for the existing V2 policy; no policy edits."""
import hashlib
import json

def attach_receipts(accounting,receipts):
    assert len(accounting)==len(receipts),'receipt/accounting row alignment'
    for a,r in zip(accounting,receipts):
        assert a['side']==('UP' if r['side']==1 else 'DOWN')
        assert abs(a['confirmedQty']-r['qty'])<1e-9
        assert abs(a['executionPriceFromInheritedSubstrate']-r['contractPrice'])<1e-9
        a.update(key=r['key'],receiptSequence=r['sequence'],nativeMaker=r['maker'])

def sig(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()

class TraceSummary:
    def __init__(self):self.rows=[]
    def write(self,line):
        r=json.loads(line);assert len(self.rows)<5000
        self.rows.append(dict(decisionId=r['decisionId'],t=r['t'],
            preHash=sig(r['preActionState']),actions=r['actionsSincePreviousDecision'],
            selected=r['selectedCandidate'],routes=r['sameIntentRouteDecisions']))
        return len(line)
    def close(self):pass

def first_divergence(a,b):
    assert len(a)==len(b) and [r['t'] for r in a]==[r['t'] for r in b],'decision clock mismatch'
    for x,y in zip(a,b):
        if x['actions']!=y['actions']:
            return dict(decisionId=x['decisionId'],t=x['t'],samePreActionState=x['preHash']==y['preHash'],A=x,B=y)
    return None

def make_sim(minimal,v2):
    Original=v2.simulator_class(minimal)
    class ReceiptV2(Original):
        def __init__(self,tape,cell):
            # Existing trace serialization still executes, but only a bounded
            # summary is retained; no multi-MB corpus written/returned.
            super().__init__(tape,cell,'NUL')
            self.detail_stream.close();self.detail_stream=TraceSummary()
        def process(self,t):
            before=len(self.detail_fill_accounting)
            # Bypass ONLY V1's incorrect one-owner-clock==one-receipt observer.
            # Keep inherited Minimal role/physical processing; record_fill above
            # still calculates FIFO attribution once per actual receipt.
            minimal.MinimalPairRoleSim.process(self,t)
            rows=self.detail_fill_accounting[before:]
            attach_receipts(rows,self._receipt_delta_rows)
            for a in rows:
                key=a['key'];a['role']=self.key_role.get(key,'UNKNOWN')
                self.detail_last_fill[key]=int(t)
                assert key in self.detail_outcomes,'unowned detail receipt'
                self.detail_outcomes[key]['fills'].append(dict(a))
    return ReceiptV2
