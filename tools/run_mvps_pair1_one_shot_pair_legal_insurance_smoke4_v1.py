from __future__ import annotations
import argparse, importlib.util, json, math, os, shutil, tempfile, zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_pair_economics_serialization_24hft.py'
SRC=STAGED if STAGED.exists() else HERE/'run_eth_pair_economics_serialization_24hft.py'
sp=importlib.util.spec_from_file_location('pair_base_for_insurance',SRC);pair=importlib.util.module_from_spec(sp);sp.loader.exec_module(pair)
Base=pair.LadderSim
EPS=1e-9
MIDS=[1945866,1945869,1945898,1945986]

class Pair1OneShotInsurance(Base):
    def __init__(self,tape):
        super().__init__(tape,1,True,False,False)
        self.insurance_used=False;self.insurance_submits=0;self.insurance_keys=[];self.insurance_events=[]
    def _try_insurance(self,t,qv,end):
        if self.insurance_used:return False
        if int(end)-int(t)<=pair.lad.NO_NEW_EXPOSURE_MS:return False
        if len(self.slot_key)>=1:return False
        uq=sum(float(a) for a,_ in self.un['UP']);dq=sum(float(a) for a,_ in self.un['DOWN'])
        if max(uq,dq)<=EPS:return False
        parent='UP' if uq>=dq else 'DOWN'; side='DOWN' if parent=='UP' else 'UP'
        bid=float(qv[side]['bid']);ask=float(qv[side]['ask'])
        if bid<=EPS or bid>=1.0-EPS or ask<=bid+EPS:return False
        qty=1.0/bid if bid>EPS else math.inf
        if not math.isfinite(qty) or qty<=EPS or qty>12.0+EPS:return False
        avg=self.unmatched_avg(parent)
        pair_sum=None if avg is None else float(avg)+bid
        if avg is None or pair_sum>1.0000001:return False
        before=self.n;self.submit(int(t),side,bid,qty);key=f'{side}_{before}';self.slot_key[1]=key;self.slot_history.append({'t':int(t),'event':'SLOT_SUBMIT','slotId':1,'key':key,'side':side,'price':bid,'qty':qty,'insurance':True})
        self.insurance_used=True;self.insurance_submits+=1;self.insurance_keys.append(key);self.insurance_events.append({'t':int(t),'parentSide':parent,'insuranceSide':side,'unmatchedParentQty':max(uq,dq),'parentAvg':float(avg),'price':bid,'qty':qty,'pairSum':pair_sum,'key':key})
        return True
    def _open_free_slots(self,t,qv,end):
        if self._try_insurance(t,qv,end):return
        return super()._open_free_slots(t,qv,end)
    def run_insurance(self,winner):
        r=self.run_ladder(winner)
        fill_qty=0.0;filled_keys=[]
        for k in self.insurance_keys:
            o=self.orders.get(k) or {};cum=float(o.get('cum') or 0.0);fill_qty+=cum
            if cum>EPS:filled_keys.append(k)
        r.update({'insuranceUsed':self.insurance_used,'insuranceSubmits':self.insurance_submits,'insuranceKeys':self.insurance_keys,'insuranceFilledKeys':filled_keys,'insuranceFillQty':fill_qty,'insuranceEvents':self.insurance_events})
        return r

def met(r,winner):
    pnl=float(r['pnlDiagnosticOnly']);n=float(r['buyNotional']);return {'pnl':pnl,'buyNotional':n,'pnlPer100BuyNotional':100*pnl/n if n>EPS else None,'fills':int(r['fillEvents']),'floor':float(r['floor']),'submits':int(r['submits']),'insuranceUsed':bool(r.get('insuranceUsed',False)),'insuranceSubmits':int(r.get('insuranceSubmits',0)),'insuranceFillQty':float(r.get('insuranceFillQty',0.0)),'insuranceEvents':r.get('insuranceEvents',[])}
def summ(rows,arm):
    xs=[x for x in rows if x['arm']==arm];pn=[x['pnl'] for x in xs];s=sum(pn);best=max(pn);nt=sum(x['buyNotional'] for x in xs)
    return {'markets':len(xs),'aggregatePnl':s,'winRate':sum(x>EPS for x in pn)/len(xs),'worstPnl':min(pn),'bestPnl':best,'leaveOneBestOut':s-best,'totalBuyNotional':nt,'aggregatePnlPer100BuyNotional':100*s/nt if nt>EPS else None,'totalFills':sum(x['fills'] for x in xs),'insuranceExerciseMarkets':sum(x['insuranceSubmits']>0 for x in xs),'insuranceFilledMarkets':sum(x['insuranceFillQty']>EPS for x in xs)}
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='pair1_ins_sm4_'));rows=[]
    try:
        with zipfile.ZipFile(a.bundle) as z:z.extractall(tmp)
        co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}
        for mid in MIDS:
            tape=tmp/'tapes'/f'{mid}.json.xz';winner=str(co[mid]['winner']).upper()
            s=Base(tape,1,True,False,False)
            try:r=s.run_ladder(winner)
            finally:s.close()
            row={'marketId':mid,'winnerPostHocOnly':winner,'arm':'N_PAIR1_MINIMAL',**met(r,winner)};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
            s=Pair1OneShotInsurance(tape)
            try:r=s.run_insurance(winner)
            finally:s.close()
            row={'marketId':mid,'winnerPostHocOnly':winner,'arm':'I_ONE_SHOT_PAIR_LEGAL_INSURANCE',**met(r,winner)};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        summary={a:summ(rows,a) for a in ['N_PAIR1_MINIMAL','I_ONE_SHOT_PAIR_LEGAL_INSURANCE']}
        out={'version':'MVPS_PAIR1_ONE_SHOT_PAIR_LEGAL_INSURANCE_SMOKE4_V1_20260908','researchOnly':True,'runtimeAuthority':False,'markets':MIDS,'rows':rows,'summary':summary,'boundary':['one insurance submit max per market','insurance only with current unmatched inventory and Pair economics <=1.0000001','same venue-min qty/current bid','one physical slot only','after insurance return to untouched Pair1','same realistic HFT/no dream/no Target runtime/no8781/no fresh/reserve']}
        op=(Path(os.environ.get('BTC5M_LAN_RESULT_DIR','.'))/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':summary},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
