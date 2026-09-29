from __future__ import annotations
import argparse,json,math,os,tempfile,zipfile
from pathlib import Path
from collections import deque,Counter
from tools import run_eth_quantity_responsibility_ladder_v3b_fifo_aggregate as v3b

EPS=v3b.EPS
base=v3b.base

def mid(qv,side):
    z=qv.get(side) or {}
    return 0.5*(float(z.get('bid') or 0.0)+float(z.get('ask') or 0.0))

def sgn(x):
    return 1.0 if x>EPS else -1.0 if x<-EPS else 0.0

class NextBookMoveBelief(v3b.FifoAggregateResponsibilityLadderV3B):
    def __init__(self,tape,spec):
        super().__init__(tape);self.spec=spec;self.targetSeen=False;self.target=None;self.label=None
        self.lastUpMid=None;self.lastMoveT=None;self.midMoves=deque(maxlen=32);self.receiptsSinceMove=0
    def _feature(self,t,qv):
        expand=str(self.spec['expandSide']);align=1.0 if expand=='UP' else -1.0
        upm=mid(qv,'UP');moves=list(self.midMoves)
        def sm(n):return float(sum(x['d'] for x in moves[-n:])) if moves else 0.0
        def share(n):
            z=moves[-n:]
            return None if not z else float(sum(1 for x in z if align*x['d']>EPS)/len(z))
        def vol(n):
            z=moves[-n:]
            return 0.0 if not z else float(sum(abs(x['d']) for x in z)/len(z))
        last=moves[-1]['d'] if moves else 0.0
        run=0
        if moves:
            ls=sgn(align*moves[-1]['d'])
            for x in reversed(moves):
                if sgn(align*x['d'])==ls and ls!=0:run+=1
                else:break
            run*=int(ls)
        return {
            'alignedBookImbalance':float(qv.get('imb') or 0.0)*align,
            'spread':float(qv.get('spread') or 0.0),
            'upMid':upm,
            'expandMidMinusHalf':align*(upm-0.5),
            'lastMoveAligned':align*last,
            'sameMoveRunAligned':float(run),
            'sumLast4MovesAligned':align*sm(4),
            'sumLast16MovesAligned':align*sm(16),
            'expandMoveShareLast8':share(8),
            'expandMoveShareLast16':share(16),
            'meanAbsMoveLast8':vol(8),
            'meanAbsMoveLast16':vol(16),
            'receiptsSinceLastMidMove':float(self.receiptsSinceMove),
            'observedPriorMidMoves':float(len(moves)),
            'weakBid':float(qv[self.spec['weakSide']]['bid']),'weakAsk':float(qv[self.spec['weakSide']]['ask']),
            'expandBid':float(qv[expand]['bid']),'expandAsk':float(qv[expand]['ask']),
        }
    def _open_one_option(self,t,qv,end):
        upm=mid(qv,'UP')
        if self.lastUpMid is None:self.lastUpMid=upm;self.receiptsSinceMove=0
        elif abs(upm-self.lastUpMid)>EPS:
            self.midMoves.append({'t':int(t),'d':float(upm-self.lastUpMid),'from':float(self.lastUpMid),'to':float(upm)})
            self.lastUpMid=upm;self.lastMoveT=int(t);self.receiptsSinceMove=0
        else:self.receiptsSinceMove+=1
        if int(t)==int(self.spec['t']) and not self.targetSeen:
            self.targetSeen=True;self.target={'t':int(t),'upMid':upm,'features':self._feature(t,qv),'expandSide':str(self.spec['expandSide']),'weakSide':str(self.spec['weakSide'])}
        elif self.targetSeen and self.label is None and int(t)>int(self.spec['t']):
            d=float(upm-float(self.target['upMid']))
            if abs(d)>EPS:
                align=1.0 if str(self.spec['expandSide'])=='UP' else -1.0
                self.label={'t':int(t),'lagMs':int(t)-int(self.spec['t']),'upMidDelta':d,'alignedDelta':align*d,'towardExpand':bool(align*d>0),'newUpMid':upm}
        return super()._open_one_option(t,qv,end)
    def run_belief(self):
        r=super().run_qty('__UNSCORED__')
        return {'targetSeen':self.targetSeen,'target':self.target,'label':self.label,'ledgerViolations':(r.get('quantityLedgerSummary') or {}).get('invariantViolations') or {},'maxSlots':int(r.get('maxSimultaneousSlots') or 0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--specs',required=True);ap.add_argument('--output',required=True);ap.add_argument('--max-states',type=int,default=100);a=ap.parse_args()
    src=json.loads(Path(a.specs).read_text(encoding='utf-8'));specs=list(src.get('states') or src.get('events') or [])[:int(a.max_states)];rows=[]
    with tempfile.TemporaryDirectory(prefix='mgmt_book_belief_') as td:
        root=Path(td)
        with zipfile.ZipFile(a.bundle) as z:
            for mid in sorted({int(s['marketId']) for s in specs}):z.extract(f'tapes/{mid}.json.xz',root)
        for i,s in enumerate(specs,1):
            mid=int(s['marketId']);sim=NextBookMoveBelief(root/'tapes'/f'{mid}.json.xz',s)
            try:r=sim.run_belief()
            finally:sim.close()
            row={'marketId':mid,'t':int(s['t']),'repairProgressFrac':float(s['repairProgressFrac']),'targetSeen':r['targetSeen'],'features':None if r['target'] is None else r['target']['features'],'label':r['label'],'ledgerClean':not bool(r['ledgerViolations']),'max4':r['maxSlots']<=4}
            rows.append(row);print(json.dumps({'progress':i,'of':len(specs),'marketId':mid,'seen':row['targetSeen'],'label':row['label'],'ledgerClean':row['ledgerClean']},ensure_ascii=False),flush=True)
    labeled=[r for r in rows if r['label'] is not None]
    out={'version':'MANAGEMENT_MAINLINE_V3B_NEXT_BOOK_MOVE_BELIEF_V1_20260907','researchOnly':True,'runtimeAuthority':False,'stateCount':len(rows),'targetSeen':sum(r['targetSeen'] for r in rows),'labeledCount':len(labeled),'towardExpand':sum(bool(r['label']['towardExpand']) for r in labeled),'towardRepair':sum(not bool(r['label']['towardExpand']) for r in labeled),'rows':rows,
         'boundary':['current V3B behavior unchanged; data capture only','label = first subsequent structural change in prediction-market UP mid, aligned to current Expand side','no fixed clock horizon','all features strict-past at target receipt','event-count path features only; no winner/settlement/Target future/public-source input','belief/context only; not action authority','consumed H100 only/no NEW24-B/no dream fill/no 8781']}
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'stateCount':len(rows),'targetSeen':out['targetSeen'],'labeledCount':out['labeledCount'],'towardExpand':out['towardExpand'],'towardRepair':out['towardRepair']},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
