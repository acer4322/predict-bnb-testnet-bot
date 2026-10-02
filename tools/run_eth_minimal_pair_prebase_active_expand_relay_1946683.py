from __future__ import annotations
import argparse,json,math,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
v2=base.v2
EPS=1e-9
MID=1946683

class PrebaseActiveExpandRelaySim(base.MinimalPairRoleSim):
    """Single research mutation: one pre-two-sided Active relay for a terminal zero-fill
    same-side SATELLITE_EXPAND child. Direction/intent is inherited; only execution route changes.
    Pair economics is the only hard strategy safety. <=180s boundary and execution legality retained.
    """
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.prebaseActiveRelayUsed=False;self.activeRelayHandled=set();self.activeRelaySubmits=0;self.activeRelayFillQty=0.0;self.activeRelayEvents=[];self._activeFillSeen={}

    def _submit_active_relay(self,t:int,sid:int,source_key:str,source_order:dict)->bool:
        if self.prebaseActiveRelayUsed:return False
        state,held,_=self._state();side=str(source_order.get('side'))
        if state!='ONE_SIDED' or held!=side:return False
        end=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])
        if end-int(t)<=v2.NO_NEW_EXPOSURE_MS:return False
        qv=v2.base.quotes(self.book)
        if not qv or qv.get(side,{}).get('ask') is None:return False
        ask=float(qv[side]['ask'])
        self.minimal_pair_checks+=1
        if not self._pair_ok(side,ask):
            self.minimal_pair_blocks+=1;self.veto['MINIMAL_PAIR_ECONOMICS_ACTIVE_RELAY']+=1
            self.activeRelayEvents.append({'t':int(t),'event':'ACTIVE_RELAY_PAIR_BLOCK','sourceKey':source_key,'side':side,'ask':ask});return False
        q=1.0/ask if ask>EPS else math.inf
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:return False
        n=self.n;self.n+=1;native_side,native_price=v2.base.ex.native_order(side,ask)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),v2.base.ex.hbt.GTC,v2.base.ex.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),v2.base.ex.hbt.GTC,v2.base.ex.LIMIT,False))
        except Exception as ex:
            self.activeRelayEvents.append({'t':int(t),'event':'ACTIVE_RELAY_SUBMIT_ERROR','sourceKey':source_key,'error':str(ex)});return False
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':ask,'qty':q,'cum':0.0,'placed':int(t),'status':'NEW'}
        self.placeHist.append((int(t),side,q,ask));self.submits+=1
        self.slot_key[int(sid)]=key;self.key_role[key]='ACTIVE_EXPAND_RELAY';self.role_submits['ACTIVE_EXPAND_RELAY']+=1
        self.prebaseActiveRelayUsed=True;self.activeRelaySubmits+=1;self._activeFillSeen[key]=0.0
        self.activeRelayEvents.append({'t':int(t),'event':'PREBASE_ACTIVE_EXPAND_RELAY_SUBMIT','sourceKey':source_key,'key':key,'slotId':int(sid),'side':side,'ask':ask,'qty':q,'submitRc':rc})
        return True

    def _refresh_slots(self,t:int):
        cand=[]
        for sid,key in list(self.slot_key.items()):
            if key in self.activeRelayHandled:continue
            o=self.orders.get(key)
            if not o or self.key_role.get(key)!='SATELLITE_EXPAND':continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper()
            if status in v2.TERMINAL_STATUSES:
                cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
                if cum<=EPS:
                    self.activeRelayHandled.add(key);cand.append((int(sid),str(key),dict(o),status))
        super()._refresh_slots(t)
        for sid,key,o,status in cand:
            if self.prebaseActiveRelayUsed:break
            if sid in self.slot_key:continue
            if self._submit_active_relay(int(t),sid,key,o):
                self.activeRelayEvents.append({'t':int(t),'event':'PASSIVE_ZERO_FILL_HANDOFF_TO_ACTIVE','sourceKey':key,'sourceStatus':status})
        # collect active relay fills from authoritative HFT snapshots
        for key in list(self._activeFillSeen):
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:continue
            cur=float(s.get('cumExecQty') or o.get('cum') or 0.0);old=float(self._activeFillSeen.get(key,0.0))
            if cur>old+EPS:
                inc=cur-old;self._activeFillSeen[key]=cur;self.activeRelayFillQty+=inc
                self.activeRelayEvents.append({'t':int(t),'event':'ACTIVE_RELAY_FILL','key':key,'incQty':inc,'cumQty':cur,'price':float(o.get('price') or 0.0)})

    def run_relay(self,winner):
        r=self.run_minimal(winner)
        r.update({'prebaseActiveRelayUsed':self.prebaseActiveRelayUsed,'activeRelaySubmits':self.activeRelaySubmits,'activeRelayFillQty':self.activeRelayFillQty,'activeRelayEvents':self.activeRelayEvents[:120]})
        return r

def slim(r):
    z=base.slim(r);z.update({'prebaseActiveRelayUsed':bool(r.get('prebaseActiveRelayUsed')),'activeRelaySubmits':int(r.get('activeRelaySubmits') or 0),'activeRelayFillQty':float(r.get('activeRelayFillQty') or 0.0)});return z

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();tmp=Path(tempfile.mkdtemp(prefix='prebase_active_1946683_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cr={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']}[MID];tape=tmp/'tapes'/f'{MID}.json.xz'
        b=base.MinimalPairRoleSim(tape,4,False)
        try:br=b.run_minimal(cr['winner'])
        finally:b.close()
        c=PrebaseActiveExpandRelaySim(tape,4,False)
        try:rr=c.run_relay(cr['winner'])
        finally:c.close()
        out={'version':'ETH_MINIMAL_PAIR_PREBASE_ACTIVE_EXPAND_RELAY_1946683_V1','date':'2026-09-05','researchOnly':True,'runtimeAuthority':False,'marketId':MID,'winnerPostHocOnly':cr['winner'],'baseline':br,'candidate':rr,'summary':{'baseline':slim(br),'candidate':slim(rr)},'boundary':['single execution mutation only: terminal zero-fill same-side SATELLITE_EXPAND may hand off once to venue-min Active relay before two-sided materialization','direction and continuation intent inherited from Pair-only controller','Pair economics remains only hard strategy safety','<=180s boundary/max4/reanchor/250ms realistic-HFT frozen','GTC limit at current strict-past ask used only for Active relay','no Target/winner runtime input; no dream fill; no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary'],'events':rr.get('activeRelayEvents')},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
