from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_18_recoverability_backed_surplus.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r218',_STAGED);r218=importlib.util.module_from_spec(sp);sp.loader.exec_module(r218)
else:
    import tools.run_eth_ms4_r2_18_recoverability_backed_surplus as r218
r28=r218.r28;v2=r218.v2;EPS=1e-9

class BorrowDirectionAnatomySim(r218.RecoverabilityBackedSurplusSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.r228Events=[];self.borrowMeta={};self._r228qv=None
    def _sig(self,qv,side):
        if not qv:return {'bookImbalance':None,'depthSide':None,'upMid':None,'priceSide':None,'depthAligned':None,'priceAligned':None,'consensusAligned':None}
        imb=float(qv.get('imb') or 0.0);depth='UP' if imb>=0 else 'DOWN'
        mid=.5*(float(qv['UP']['bid'])+float(qv['UP']['ask']))
        if mid>.5+EPS:price='UP'
        elif mid<.5-EPS:price='DOWN'
        else:price='NEUTRAL'
        cons=price!='NEUTRAL' and price==depth
        return {'bookImbalance':imb,'depthSide':depth,'upMid':mid,'priceSide':price,
                'depthAligned':depth==side,'priceAligned':price==side,
                'consensusAligned':bool(cons and depth==side),'signalsConsensus':bool(cons)}
    def _open_one_option(self,t,qv,end):
        self._r228qv=qv
        return super()._open_one_option(t,qv,end)
    def _submit_expand_with_authority(self,t,side,p,q,proj,auth,event_name):
        before_n=self.n;is_borrow=(auth.get('source')=='RECOVERABILITY_BACKED')
        sig=self._sig(self._r228qv,side) if is_borrow else None
        rec=auth.get('recoverability') or {}
        floor=float(self._physical_floor());best=float(max(self.inv.values())-self.cost)
        ok=super()._submit_expand_with_authority(t,side,p,q,proj,auth,event_name)
        if ok and is_borrow:
            key=f'{side}_{before_n}';meta={'submitT':int(t),'key':key,'generation':int(self.key_scope_gen.get(key,self.scopeGeneration)),
                 'side':side,'expandPrice':float(p),'expandQty':float(q),'riskCost':float(auth.get('riskCost') or 0.0),
                 'ordinaryAvailableCredit':float(auth.get('realCredit') or 0.0),'creditDeficit':max(0.0,float(auth.get('riskCost') or 0.0)-float(auth.get('realCredit') or 0.0)),
                 'recoverabilityReason':rec.get('reason'),'forwardPairSum':rec.get('forwardPairSum'),'futureRepairPrice':rec.get('futureRepairPrice'),'futureRepairQty':rec.get('futureRepairQty'),
                 'physicalFloorAtSubmit':floor,'bestPnlAtSubmit':best,'scopeRepairProgressClocks':int(self.scopeRepairProgressClocks),
                 'totalRepairProgressClocks':int(self.totalRepairProgressClocks),**(sig or {})}
            self.borrowMeta[key]=meta;self.r228Events.append({'event':'BORROW_SUBMIT',**meta})
        return ok
    def process(self,t):
        before={k:float(self.orders.get(k,{}).get('cum') or 0.0) for k in self.borrowMeta}
        super().process(t)
        for k,old in before.items():
            o=self.orders.get(k)
            if not o:continue
            inc=max(0.0,float(o.get('cum') or 0.0)-old)
            if inc<=EPS:continue
            meta=self.borrowMeta[k]
            ev={'event':'BORROW_FILL','fillT':int(t),'key':k,'fillQty':inc,'orderPrice':float(o.get('price') or 0.0),**meta,
                'physicalFloorAfterFillProcess':float(self._physical_floor()),'bestPnlAfterFillProcess':float(max(self.inv.values())-self.cost)}
            self.r228Events.append(ev)
    def run_r228(self,w):
        r=super().run_r218(w);r['r228Events']=self.r228Events[:3000];r['r228BorrowSubmitCount']=sum(e['event']=='BORROW_SUBMIT' for e in self.r228Events);r['r228BorrowFillEvents']=sum(e['event']=='BORROW_FILL' for e in self.r228Events);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r228_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=BorrowDirectionAnatomySim(tape,4)
            try:c=sim.run_r228(cr['winner'])
            finally:sim.close()
            fills=[e for e in c['r228Events'] if e.get('event')=='BORROW_FILL'];winner=str(cr['winner']).upper()
            for e in fills:e['winnerPostHocOnly']=winner;e['borrowSideWinnerAlignedPostHoc']=str(e['side']).upper()==winner
            row={'marketId':mid,'winnerPostHocOnly':winner,
                 'control':{'fills':b['fillEvents'],'submits':b['submits'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},
                 'candidate':{'fills':c['fillEvents'],'submits':c['submits'],'pnl':c['pnlDiagnosticOnly'],'floor':c['floor'],'best':c['best']},
                 'delta':{'fills':c['fillEvents']-b['fillEvents'],'pnl':c['pnlDiagnosticOnly']-b['pnlDiagnosticOnly'],'floor':c['floor']-b['floor'],'best':c['best']-b['best']},
                 'borrowSubmits':c['r228BorrowSubmitCount'],'borrowFillEvents':c['r228BorrowFillEvents'],'borrowFills':fills,'r218Stats':c['r218Stats']}
            rows.append(row);print(json.dumps({'marketId':mid,'winner':winner,'delta':row['delta'],'borrowSubmits':row['borrowSubmits'],'borrowFillEvents':row['borrowFillEvents'],'borrowFills':fills},ensure_ascii=False),flush=True)
        fills=[(r,e) for r in rows for e in r['borrowFills']]
        def group(field):
            z=[(r,e) for r,e in fills if e.get(field) is True]
            return {'fillEvents':len(z),'markets':len(set(r['marketId'] for r,e in z)),'winnerAlignedFills':sum(bool(e.get('borrowSideWinnerAlignedPostHoc')) for r,e in z),
                    'winnerAlignedShare':sum(bool(e.get('borrowSideWinnerAlignedPostHoc')) for r,e in z)/len(z) if z else None,
                    'marketPnlDeltaSum':sum(float(r['delta']['pnl']) for r,e in z),'marketFloorDeltaSum':sum(float(r['delta']['floor']) for r,e in z)}
        out={'version':'MS4_R2_28_R218_BORROW_FILL_DIRECTION_ANATOMY_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,
             'aggregate':{'borrowFillEvents':len(fills),'borrowFillMarkets':len(set(r['marketId'] for r,e in fills)),
                          'winnerAlignedBorrowFills':sum(bool(e.get('borrowSideWinnerAlignedPostHoc')) for r,e in fills),
                          'depthAligned':group('depthAligned'),'priceAligned':group('priceAligned'),'consensusAligned':group('consensusAligned')},
             'boundary':['exact R2.18 behavior; instrumentation only','submit direction features are strict-past current book only','winner and CAP1->R2.18 economic deltas are posthoc evaluation only','no threshold fitting','no behavior promotion from four markets','<=180s unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'aggregate':out['aggregate']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
