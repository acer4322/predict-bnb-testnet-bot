from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter
import numpy as np,joblib
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r1_queue_aware_repair.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('ms4r1',_STAGED);r1=importlib.util.module_from_spec(sp);sp.loader.exec_module(r1)
else:
    import tools.run_eth_ms4_r1_queue_aware_repair as r1
EPS=1e-9;ACTIVE_WINDOW_MS=500
ROLES=['PROBE_CORE','ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND']

class FillabilityActiveRepairSim(r1.QueueAwareRepairRoutingSim):
    def __init__(self,tape,model_bundle,max_slots=4):
        super().__init__(tape,max_slots);self.execModel=model_bundle;self.activeKeys=set();self.activeMeta={};self.activeStats=Counter();self.activeFillQty=0.0;self.executionDecisions=[]
    def _active_reserved_repair(self):
        z=0.0
        for k in list(self.activeKeys):
            if self.key_scope_gen.get(k)==self.scopeGeneration:z+=max(0.0,float(self.keyRepairQuotaRemaining.get(k,0.0)))
        return z
    def _reserved_repair_quota(self,repair_side=None):
        return float(super()._reserved_repair_quota(repair_side)+self._active_reserved_repair())
    def _rank(self,side,p):
        levels=[float(r1.v2.kprice(x)) for x in self._live_price_levels(side)];kp=float(r1.v2.kprice(p))
        try:return levels.index(kp)+1
        except ValueError:return 99
    def _depth(self,side,p):
        try:return float(self._level_depth(side,float(p)))
        except Exception:return 0.0
    def _score(self,side,role,p,q,split):
        levels=[float(r1.v2.kprice(x)) for x in self._live_price_levels(side)];best=levels[0] if levels else float(p);rank=self._rank(side,p);depth=self._depth(side,p);best_depth=self._depth(side,best)
        opp='DOWN' if side=='UP' else 'UP';oppavg=self.unmatched_avg(opp);ps=(float(oppavg)+float(p)) if oppavg is not None else None
        floor=float(self._physical_floor());upside=float(max(float(self.inv['UP']),float(self.inv['DOWN']))-float(self.cost))
        vals=[float(p),float(rank),math.log1p(max(0.,depth)),float(best),math.log1p(max(0.,best_depth)),float((best-float(p))/0.01),1. if self._pair_ok(side,p) else 0.,(float(ps)-1.) if ps is not None else 0.,floor,upside-floor,float(self._scope_debt_qty()),float(self._reserved_repair_quota()),float(self._available_expand_risk_credit()),float(len(self.slot_key)),float((split or {}).get('repairQty',0.0)),float((split or {}).get('overflowQty',0.0)),1. if side=='UP' else 0.]
        vals.extend(1. if role==r else 0. for r in ROLES)
        model=self.execModel['model'];return float(model.predict_proba(np.asarray([vals],float))[0,1]),{'rank':rank,'depth':depth,'bestPrice':best,'bestDepth':best_depth,'pairSum':ps}
    def _has_live_active(self):
        for k in list(self.activeKeys):
            if self.key_scope_gen.get(k)!=self.scopeGeneration:continue
            o=self.orders.get(k)
            if not o:continue
            try:s=self.snap(o);status=str(s.get('status') or '').upper()
            except Exception:status=''
            if status not in r1.v2.TERMINAL_STATUSES:return True
        return False
    def _submit_active(self,t,side,role,q,score,diag):
        qv=r1.v2.base.quotes(self.book)
        if not qv or qv.get(side,{}).get('ask') is None:self.activeStats['NO_ACTIVE_ASK']+=1;return False
        ap=float(qv[side]['ask'])
        if role=='ECONOMIC_CORE' and not self._pair_ok(side,ap):self.activeStats['ACTIVE_CORE_PAIR_BLOCK']+=1;return False
        before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,ap,q))
        if after<=before+EPS:self.activeStats['ACTIVE_NOT_FLOOR_IMPROVING']+=1;return False
        # Same pure-Repair quantity only; no Overflow and no new objective/risk authority.
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.,debt-reserved)
        if avail+EPS<float(q):self.activeStats['ACTIVE_REPAIR_QUOTA_UNAVAILABLE']+=1;return False
        n=self.n;self.n+=1;native_side,native_price=r1.v2.base.ex.native_order(side,ap)
        try:
            if native_side=='BUY':rc=int(self.bt.submit_buy_order(0,int(n),native_price,float(q),r1.v2.base.ex.hbt.GTC,r1.v2.base.ex.hbt.LIMIT,False))
            else:rc=int(self.bt.submit_sell_order(0,int(n),native_price,float(q),r1.v2.base.ex.hbt.GTC,r1.v2.base.ex.hbt.LIMIT,False))
        except Exception:self.activeStats['SUBMIT_EXCEPTION']+=1;return False
        key=f'{side}_{n}';self.orders[key]={'n':n,'side':side,'price':ap,'qty':float(q),'cum':0.0,'placed':int(t),'status':'NEW'};self.placeHist.append((int(t),side,float(q),ap));self.submits+=1
        self.key_role[key]=role;self.key_scope_gen[key]=int(self.scopeGeneration);self.role_submits[role]+=1
        self.keyRepairQuotaAuthorized[key]=float(q);self.keyRepairQuotaRemaining[key]=float(q);self.keyOverflowQtyAuthorized[key]=0.0;self.keyOverflowQtyRemaining[key]=0.0;self.totalRepairQuotaAuthorized+=float(q)
        self.activeKeys.add(key);self.activeMeta[key]={'submitAt':int(t),'fillSeen':0.0,'role':role,'score':float(score)};self.activeStats['SUBMIT']+=1
        ev={'t':int(t),'event':'MS4_R2_ACTIVE_REPAIR_SUBMIT','key':key,'scopeGeneration':int(self.scopeGeneration),'side':side,'role':role,'passiveFillability':float(score),'passiveRank':diag.get('rank'),'passiveDepth':diag.get('depth'),'activePrice':ap,'qty':float(q),'debt':debt,'reservedBefore':reserved,'submitRc':rc}
        self.executionDecisions.append(ev);self.slot_history.append(ev);self._audit_reservation();return True
    def _submit_role_v8(self,t,side,role,p,q,proj,split=None):
        if role in ('ECONOMIC_CORE','SATELLITE_REPAIR') and isinstance(split,dict) and float(split.get('overflowQty',0.0))<=EPS and float(split.get('repairQty',0.0))+EPS>=float(q):
            score,diag=self._score(side,role,p,q,split);decision={'t':int(t),'event':'MS4_R2_EXECUTION_SCORE','scopeGeneration':int(self.scopeGeneration),'side':side,'role':role,'passivePrice':float(p),'qty':float(q),'passiveFillability':float(score),**diag}
            self.executionDecisions.append(decision)
            if score<0.5 and not self._has_live_active():
                if self._submit_active(t,side,role,q,score,diag):self.activeStats['LOW_FILLABILITY_SWITCH']+=1;return True
                self.activeStats['LOW_FILLABILITY_ACTIVE_BLOCKED']+=1
            else:self.activeStats['KEEP_PASSIVE']+=1
        return super()._submit_role_v8(t,side,role,p,q,proj,split)
    def _manage_active(self,t):
        for key in list(self.activeKeys):
            o=self.orders.get(key);m=self.activeMeta.get(key)
            if not o or not m:self.activeKeys.discard(key);continue
            cur=float(o.get('cum') or 0.0);old=float(m.get('fillSeen') or 0.0)
            if cur>old+EPS:self.activeFillQty+=cur-old;m['fillSeen']=cur;self.activeStats['FILL_EVENT']+=1
            try:s=self.snap(o);status=str(s.get('status') or '').upper();live=r1.v2.base.live(status)
            except Exception:status='';live=False
            if live and int(t)-int(m['submitAt'])>=ACTIVE_WINDOW_MS and not o.get('cancelRequested'):
                co=self.bt.orders(0).get(o['n'])
                if co is not None and bool(co.cancellable):
                    try:self.bt.cancel(0,o['n'],False);o['cancelRequested']=True;self.activeStats['CANCEL_REMAINDER']+=1
                    except Exception:pass
            if status in r1.v2.TERMINAL_STATUSES:
                self.keyRepairQuotaRemaining[key]=0.0;self.keyOverflowQtyRemaining[key]=0.0;self.activeKeys.discard(key);self.activeStats['TERMINAL_'+status]+=1
    def process(self,t):
        super().process(t);self._manage_active(t)
    def run_r21(self,winner):
        r=super().run_v88(winner);r['ms4R2ActiveRepairStats']=dict(self.activeStats);r['ms4R2ActiveRepairFillQty']=float(self.activeFillQty);r['ms4R2ExecutionDecisions']=self.executionDecisions[:1200];return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];model=joblib.load(a.model);tmp=Path(tempfile.mkdtemp(prefix='ms4_r21_fillactive_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz';ctl=r1.QueueAwareRepairRoutingSim(tape,4)
            try:r0=ctl.run_v88(cr['winner'])
            finally:ctl.close()
            rows.append({'marketId':mid,'cell':'MS4_R1_CONTROL','winnerPostHocOnly':cr['winner'],**r0});sim=FillabilityActiveRepairSim(tape,model,4)
            try:r=sim.run_r21(cr['winner'])
            finally:sim.close()
            rows.append({'marketId':mid,'cell':'MS4_R2_1_FILLABILITY_ACTIVE_REPAIR','winnerPostHocOnly':cr['winner'],**r})
            print(json.dumps({'marketId':mid,'r1Sub':r0['submits'],'r21Sub':r['submits'],'r1Fill':r0['fillEvents'],'r21Fill':r['fillEvents'],'r1Pnl':r0['pnlDiagnosticOnly'],'r21Pnl':r['pnlDiagnosticOnly'],'r1Floor':r0['floor'],'r21Floor':r['floor'],'active':r['ms4R2ActiveRepairStats'],'activeQty':r['ms4R2ActiveRepairFillQty'],'unauth':r['unauthorizedOverflowQty'],'quotaExcess':r['repairQuotaExcessMax']},ensure_ascii=False),flush=True)
        c={r['marketId']:r for r in rows if r['cell']=='MS4_R1_CONTROL'};n={r['marketId']:r for r in rows if r['cell']=='MS4_R2_1_FILLABILITY_ACTIVE_REPAIR'};cmp=[]
        for m in mids:cmp.append({'marketId':m,'submitRetention':n[m]['submits']/c[m]['submits'] if c[m]['submits'] else None,'fillRetention':n[m]['fillEvents']/c[m]['fillEvents'] if c[m]['fillEvents'] else None,'pnlDelta':n[m]['pnlDiagnosticOnly']-c[m]['pnlDiagnosticOnly'],'floorDelta':n[m]['floor']-c[m]['floor'],'activeFillQty':n[m]['ms4R2ActiveRepairFillQty']})
        correct=all(n[m]['unauthorizedOverflowQty']<=EPS and n[m]['repairQuotaExcessMax']<=EPS for m in mids);live=all(n[m]['fillEvents']>=0.5*c[m]['fillEvents'] for m in mids if c[m]['fillEvents']>0)
        out={'version':'MS4_R2_1_FILLABILITY_ACTIVE_REPAIR_SMOKE_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,'rows':rows,'comparison':cmp,'gates':{'correctnessPass':correct,'antiCollapse50pctPass':live},'boundary':['Only already-authorized pure Repair candidate may switch actuator','p<0.5 frozen classifier boundary','ECONOMIC_CORE active remains pair-compatible','Active candidate-alone Floor must improve','same Repair qty; zero Overflow','one live Active Repair per scope','no new risk/debt/Expand authority','<=180s unchanged','realistic HFT','no dream fill','no 8781']};op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'gates':out['gates'],'comparison':cmp},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
