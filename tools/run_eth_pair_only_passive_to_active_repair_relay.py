from __future__ import annotations
import argparse,json,os,sys,tempfile,zipfile,shutil,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import tools.run_eth_role_separated_minimal_pair_safety_smoke as base
v3=base.v3; v2=v3.v2; ex=v2.base.ex
EPS=1e-9; TERMINAL={'FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'}

class PassiveToActiveRepairRelaySim(base.MinimalPairRoleSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots,False)
        self.relayUsed=False; self.relayHandled=set(); self.activeKeys={}; self.activeRelaySubmits=0; self.activeRelayFillQty=0.0; self.activeRelayEvents=[]; self.marketEnd=int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])

    def _project_floor_full_fill(self,side,p,q):
        up=float(self.inv['UP']); dn=float(self.inv['DOWN']); cost=float(self.cost)+float(p)*float(q)
        if side=='UP': up+=float(q)
        else: dn+=float(q)
        return float(min(up,dn)-cost)

    def process(self,t):
        before={k:float(o.get('cum') or 0.0) for k,o in self.orders.items()}
        super().process(t)
        for k in list(self.activeKeys):
            o=self.orders.get(k)
            if not o: continue
            cur=float(o.get('cum') or 0.0); old=float(before.get(k,0.0))
            if cur>old+EPS:
                inc=cur-old; self.activeRelayFillQty+=inc
                self.activeRelayEvents.append({'t':int(t),'event':'ACTIVE_REPAIR_RELAY_FILL','key':k,'incQty':inc,'cumQty':cur,'side':o['side'],'price':o['price']})

    def _submit_active_repair(self,t,source_key,side,ask):
        q=1.0/float(ask) if ask>EPS else math.inf
        if not math.isfinite(q) or q<=EPS:return False
        before=self._physical_floor(); after=self._project_floor_full_fill(side,ask,q)
        if after<=before+EPS:return False
        if len(self.slot_key)>=self.max_slots:return False
        free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:return False
        n=self.n; self.n+=1; native_side,native_price=ex.native_order(side,ask)
        try:
            if native_side=='BUY': rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),ex.hbt.GTC,ex.LIMIT,False))
            else: rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),ex.hbt.GTC,ex.LIMIT,False))
        except Exception:return False
        key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':float(ask),'qty':float(q),'cum':0.0,'placed':int(t),'status':'NEW','cancelRequested':False}
        self.slot_key[int(free)]=key; self.key_role[key]='ACTIVE_REPAIR_RELAY'; self.role_submits['ACTIVE_REPAIR_RELAY']+=1
        self.placeHist.append((int(t),side,q,ask)); self.submits+=1; self.activeKeys[key]={'sourceKey':source_key,'submittedAt':int(t)}; self.activeRelaySubmits+=1; self.relayUsed=True
        self.activeRelayEvents.append({'t':int(t),'event':'ACTIVE_REPAIR_RELAY_SUBMIT','sourceKey':source_key,'key':key,'slotId':int(free),'side':side,'ask':float(ask),'qty':float(q),'floorBefore':before,'floorAfterFullFill':after,'submitRc':rc})
        return True

    def _refresh_slots(self,t):
        candidates=[]
        for sid,key in list(self.slot_key.items()):
            if key in self.relayHandled: continue
            o=self.orders.get(key)
            if not o or self.key_role.get(key)!='ECONOMIC_CORE': continue
            try:s=self.snap(o)
            except Exception:s={}
            status=str(s.get('status') or '').upper(); cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if status not in TERMINAL: continue
            self.relayHandled.add(key)
            state,held,repair=self._state()
            if (not self.relayUsed and status!='FILLED' and cum<=EPS and state=='ONE_SIDED' and str(o.get('side'))==str(repair)):
                candidates.append((int(sid),str(key),str(o.get('side'))))
        super()._refresh_slots(t)
        if self.relayUsed or not candidates:return
        if int(self.marketEnd)-int(t)<=v2.NO_NEW_EXPOSURE_MS:return
        qv=v2.base.quotes(self.book)
        if not qv:return
        for sid,key,side in candidates:
            ask=qv.get(side,{}).get('ask')
            if ask is None:continue
            if self._submit_active_repair(t,key,side,float(ask)):
                self.activeRelayEvents.append({'t':int(t),'event':'PASSIVE_REPAIR_ZERO_FILL_HANDOFF_TO_ACTIVE','sourceKey':key});break

    def run_candidate(self,winner):
        r=self.run_minimal(winner)
        r.update({'activeRepairRelayUsed':self.relayUsed,'activeRepairRelaySubmits':self.activeRelaySubmits,'activeRepairRelayFillQty':self.activeRelayFillQty,'activeRepairRelayEvents':self.activeRelayEvents[:100]})
        return r

def slim2(r):
    s=base.slim(r);s.update({'activeRepairRelayUsed':bool(r.get('activeRepairRelayUsed')),'activeRepairRelaySubmits':int(r.get('activeRepairRelaySubmits') or 0),'activeRepairRelayFillQty':float(r.get('activeRepairRelayFillQty') or 0.0)});return s

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    tmp=Path(tempfile.mkdtemp(prefix='passive_active_repair_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            b=base.MinimalPairRoleSim(tape,4,False)
            try:br=b.run_minimal(cr['winner'])
            finally:b.close()
            c=PassiveToActiveRepairRelaySim(tape,4)
            try:rr=c.run_candidate(cr['winner'])
            finally:c.close()
            bs,cs=base.slim(br),slim2(rr);rows.append({'marketId':mid,'baseline':bs,'candidate':cs,'events':rr.get('activeRepairRelayEvents') or []})
            print(json.dumps({'marketId':mid,'baseline':bs,'candidate':cs,'events':(rr.get('activeRepairRelayEvents') or [])[:10]},ensure_ascii=False),flush=True)
        def agg(which):
            xs=[r[which] for r in rows];return {'markets':len(xs),'totalFills':sum(x['fills'] for x in xs),'avgFills':sum(x['fills'] for x in xs)/len(xs),'totalAlts':sum(x['fillSideAlternations'] for x in xs),'avgAlts':sum(x['fillSideAlternations'] for x in xs)/len(xs),'twoSided':sum(x['twoSidedMaterialized'] for x in xs),'totalPnl':sum(x['pnl'] for x in xs),'avgPnl':sum(x['pnl'] for x in xs)/len(xs),'avgFloor':sum(x['floor'] for x in xs)/len(xs),'relayUsed':sum(bool(x.get('activeRepairRelayUsed')) for x in xs),'relayFillQty':sum(float(x.get('activeRepairRelayFillQty') or 0.0) for x in xs)}
        out={'version':'ETH_PAIR_ONLY_PASSIVE_TO_ACTIVE_REPAIR_RELAY_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'summary':{'baseline':agg('baseline'),'candidate':agg('candidate')},'rows':rows,'boundary':['Pair-only Passive controller unchanged','only ECONOMIC_CORE Repair terminal 0-fill while still one-sided may handoff once per market to venue-min Active Repair','Active price=current strict-past ask and full-fill current physical Floor must strictly improve','no Target runtime, no winner at decision time, no new direction authority','no serialization/shared Floor budget/risk contraction/one-new-per-receipt veto','max4 and <=180s retained','realistic HFT; no dream fill; no 8781']}
        op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'summary':out['summary']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
