from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('ms4r1',_STAGED);r1=importlib.util.module_from_spec(sp);sp.loader.exec_module(r1)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as r1
EPS=1e-9; ACTIVE_WINDOW_MS=500; TERMINAL={'FILLED','CANCELED','CANCELLED','EXPIRED','REJECTED'}

class ActiveRepairTerminalFallback(r1.QueueAwareRepairRoutingSim):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots);self.handledPassive=set();self.activeKeys=set();self.activeMeta={};self.activeStats=Counter();self.activeFillQty=0.0
    def _submit_active_repair(self,t,side,source_key):
        if int((self.payload.get('market') or {}).get('window_end_ms') or self.meta['lastReceivedMs'])-int(t)<=r1.v2.NO_NEW_EXPOSURE_MS:
            self.activeStats['BLOCK_LATE']+=1;return False
        qv=r1.v2.base.quotes(self.book)
        if not qv or side not in qv or qv[side].get('ask') is None:return False
        p=float(qv[side]['ask']);q=1.0/p if p>EPS else math.inf
        if not math.isfinite(q) or q<=EPS or q>12.0+EPS:self.activeStats['BLOCK_PHYSICAL']+=1;return False
        sp=self._repair_split(side,p,q)
        if sp is None:self.activeStats['BLOCK_SPLIT_OR_BUDGET']+=1;return False
        n=self.n;self.n+=1;native_side,native_price=r1.v2.base.ex.native_order(side,p)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),r1.v2.base.ex.hbt.GTC,r1.v2.base.ex.hbt.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),r1.v2.base.ex.hbt.GTC,r1.v2.base.ex.hbt.LIMIT,False))
        except Exception:self.activeStats['SUBMIT_EXCEPTION']+=1;return False
        key=f'{side}_{n}';free=next((sid for sid in range(1,self.max_slots+1) if sid not in self.slot_key),None)
        if free is None:self.activeStats['BLOCK_NO_SLOT']+=1;return False
        self.orders[key]={'n':n,'side':side,'price':p,'qty':q,'cum':0.0,'placed':int(t),'status':'NEW'};self.placeHist.append((int(t),side,q,p));self.submits+=1
        self.slot_key[int(free)]=key;self.key_role[key]='SATELLITE_REPAIR';self.key_scope_gen[key]=int(self.scopeGeneration);self.role_submits['SATELLITE_REPAIR']+=1
        rq=float(sp['repairQty']);oq=float(sp['overflowQty']);self.keyRepairQuotaAuthorized[key]=rq;self.keyRepairQuotaRemaining[key]=rq;self.keyOverflowQtyAuthorized[key]=oq;self.keyOverflowQtyRemaining[key]=oq;self.totalRepairQuotaAuthorized+=rq;self.totalOverflowQtyAuthorized+=oq
        self.activeKeys.add(key);self.activeMeta[key]={'submitAt':int(t),'sourceKey':source_key,'fillSeen':0.0};self.activeStats['SUBMIT']+=1
        self.slot_history.append({'t':int(t),'event':'ACTIVE_REPAIR_TERMINAL_FALLBACK_SUBMIT','key':key,'sourceKey':source_key,'side':side,'ask':p,'qty':q,'repairQty':rq,'overflowQty':oq,'submitRc':rc})
        return True
    def _scan_terminal_zero_fill(self,t):
        candidates=[]
        for sid,key in list(self.slot_key.items()):
            if key in self.handledPassive or key in self.activeKeys:continue
            role=self.key_role.get(key,'UNASSIGNED')
            if role not in {'ECONOMIC_CORE','SATELLITE_REPAIR'}:continue
            o=self.orders.get(key)
            if not o:continue
            try:s=self.snap(o)
            except Exception:continue
            st=str(s.get('status') or '').upper();cum=float(s.get('cumExecQty') or o.get('cum') or 0.0)
            if st in TERMINAL:
                self.handledPassive.add(key)
                if cum<=EPS:candidates.append((key,str(o['side'])))
        return candidates
    def _manage_active(self,t):
        for key in list(self.activeKeys):
            o=self.orders.get(key);a=self.activeMeta.get(key)
            if not o or not a:continue
            cur=float(o.get('cum') or 0.0);old=float(a.get('fillSeen') or 0.0)
            if cur>old+EPS:self.activeFillQty+=cur-old;a['fillSeen']=cur;self.activeStats['FILL_EVENT']+=1
            try:s=self.snap(o);live=r1.v2.base.live(s.get('status'))
            except Exception:live=False
            if live and int(t)-int(a['submitAt'])>=ACTIVE_WINDOW_MS and not o.get('cancelRequested'):
                curord=self.bt.orders(0).get(o['n'])
                if curord is not None and bool(curord.cancellable):
                    try:self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.activeStats['CANCEL_REMAINDER']+=1
                    except Exception:pass
    def _refresh_slots(self,t):
        todo=self._scan_terminal_zero_fill(t);super()._refresh_slots(t);self._manage_active(t)
        # at most one fallback per receipt; other terminal sources can retry only via future passive carrier
        if todo:self._submit_active_repair(int(t),todo[0][1],todo[0][0])
    def process(self,t):
        super().process(t);self._manage_active(t)
    def run_d5(self,winner):
        r=super().run_v88(winner);r['activeRepairTerminalFallback']=dict(self.activeStats);r['activeRepairFillQty']=float(self.activeFillQty);return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_d5_active_repair_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);cohort={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=cohort[mid];tape=tmp/'tapes'/f'{mid}.json.xz';c=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=c.run_v88(cr['winner'])
            finally:c.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0});s=ActiveRepairTerminalFallback(tape,4)
            try:r=s.run_d5(cr['winner'])
            finally:s.close()
            rows.append({'marketId':mid,'cell':'MS4_D5_ACTIVE_REPAIR_TERMINAL_FALLBACK','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'progress':mid,'r1Sub':r0['submits'],'d5Sub':r['submits'],'r1Fill':r0['fillEvents'],'d5Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'d5Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'d5Floor':r['floor'],'active':r['activeRepairTerminalFallback'],'activeQty':r['activeRepairFillQty'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_D5_ACTIVE_REPAIR_TERMINAL_FALLBACK'};cmp=[{'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeFillQty':n[m]['activeRepairFillQty']} for m in mids]
        out={'version':'MS4_D5_ACTIVE_REPAIR_TERMINAL_FALLBACK_20260905','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids),'antiCollapsePass':all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)},'boundary':['same responsibility passive terminal zero-fill only','active Repair uses current ask GTC LIMIT and 500ms remainder window inherited from prior active execution mechanic','same Repair/Overflow split and authoritative debt reservation','no new risk credit','<=180s unchanged','one active fallback per receipt','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
