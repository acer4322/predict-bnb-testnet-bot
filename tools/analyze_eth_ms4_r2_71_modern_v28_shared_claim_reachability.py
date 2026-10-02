from __future__ import annotations
import argparse,json,math,shutil,tempfile,zipfile,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import run_eth_ms4_r2_57_risk_fill_passive_repair_obligation as r257
v2=r257.v2; EPS=1e-9

class ModernV28Reachability(r257.RiskFillPassiveRepairObligationSim):
    def __init__(self,tape,fanout_limit=1,max_slots=4):
        super().__init__(tape,fanout_limit,max_slots)
        self.r271=Counter(); self.samples=[]; self._seen=set()

    def _risk_price(self,ob):
        x=self.riskTrancheMeta.get(str(ob.get('bornFromRiskKey') or ''))
        if not x:return None
        p=float(x.get('price') or 0.0);return p if p>EPS else None

    def _live_primary(self,gen):
        out=[]
        for key in self.riskRepairCarrierKeys:
            if int(self.key_scope_gen.get(key,-1))!=int(gen):continue
            o=self.orders.get(key)
            if not o:continue
            try:st=str(self.snap(o).get('status') or '').upper()
            except Exception:st=''
            if st in v2.TERMINAL_STATUSES or o.get('cancelRequested'):continue
            rem=max(0.0,float(self.keyRepairQuotaRemaining.get(key,0.0)))
            if rem>EPS:out.append((key,o,rem))
        return out

    def _audit(self,t):
        ob=self._obligation_current()
        if not ob:return
        gen=int(ob['generation']);prim=self._live_primary(gen)
        if not prim:return
        rp=self._risk_price(ob)
        if rp is None:return
        side=self._repair_side();used=self._used_prices(side)
        debt=float(self._scope_debt_qty());reserved=float(self._reserved_repair_quota(side));avail=max(0.0,debt-reserved)
        primary_prices=[float(o['price']) for _,o,_ in prim];primary_floor=min(primary_prices)
        alts=[]
        for rank,raw in enumerate(self._live_price_levels(side),1):
            p=float(v2.kprice(raw))
            if p<=EPS or p in used or p>=primary_floor-EPS:continue
            q=1.0/p
            if not math.isfinite(q) or q<=EPS or q>12.0+EPS:continue
            if rp+p>1.0+EPS:continue
            alts.append({'rank':rank,'price':p,'qty':q,'pairSum':rp+p,
                         'fitsFixedUnreserved':q<=avail+EPS,
                         'fitsSharedParentPhysical':q<=debt+EPS,
                         'potentialOverhangVsUnreserved':max(0.0,q-avail)})
        if not alts:return
        best=alts[0]
        sig=(gen,tuple(round(x,6) for x in (debt,reserved,best['price'],best['qty'])))
        if sig in self._seen:return
        self._seen.add(sig)
        self.r271['ALT_FAVORABLE_EXISTS']+=1
        if best['fitsFixedUnreserved']:self.r271['FIXED_QUOTA_REACHABLE']+=1
        else:self.r271['FIXED_QUOTA_BLOCKED']+=1
        if best['fitsSharedParentPhysical']:self.r271['SHARED_PARENT_Q_LE_DEBT']+=1
        if not best['fitsFixedUnreserved'] and best['fitsSharedParentPhysical']:
            self.r271['FIXED_BLOCK_SHARED_PARENT_REACHABLE']+=1
        self.samples.append({'t':int(t),'generation':gen,'riskPrice':rp,'obligationOutstanding':float(ob['outstanding']),
                             'nativeDebt':debt,'reservedRepair':reserved,'unreservedRepair':avail,
                             'primary': [{'key':k,'price':float(o['price']),'remaining':rem} for k,o,rem in prim],
                             'bestSecondary':best,'altCount':len(alts)})

    def _open_one_option(self,t,qv,end):
        super()._open_one_option(t,qv,end)
        self._audit(t)

    def run_shadow(self,w):
        r=super().run_r257(w);r.update({'r271Stats':dict(self.r271),'r271Samples':self.samples[:2000]});return r

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='r271_'))
    try:
        with zipfile.ZipFile(a.bundle) as z:
            co={int(x['marketId']):x for x in json.loads(z.read('cohort.json'))['rows']}
            for m in mids:(tmp/f'{m}.json.xz').write_bytes(z.read(f'tapes/{m}.json.xz'))
        rows=[];agg=Counter()
        for m in mids:
            s=ModernV28Reachability(tmp/f'{m}.json.xz',1,4)
            try:r=s.run_shadow(co[m]['winner'])
            finally:s.close()
            st=Counter(r['r271Stats']);agg.update(st);rows.append({'marketId':m,'stats':dict(st),'samples':r['r271Samples']})
            print(json.dumps({'marketId':m,'stats':dict(st),'sample':r['r271Samples'][:2]},ensure_ascii=False),flush=True)
        out={'version':'MS4_R2_71_MODERN_V28_SHARED_CLAIM_REACHABILITY_V1','researchOnly':True,'behaviorChange':False,'markets':mids,'aggregate':dict(agg),'rows':rows,
             'boundary':['R2.57 behavior frozen','behavior-inert modern V28 reachability','secondary must be cheaper than primary and favorable vs confirmed risk entry','compares old fixed per-key unreserved quota with shared-parent physical debt reachability','no action from Target/future/winner','consumed realistic HFT','no 8781']}
        Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'aggregate':dict(agg)},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)
if __name__=='__main__':main()
