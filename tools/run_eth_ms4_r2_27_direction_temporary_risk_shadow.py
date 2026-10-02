from __future__ import annotations
import argparse,json,os,shutil,tempfile,zipfile,sys,importlib.util,math
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
_STAGED=Path.cwd()/'.lan_worker_v1'/'staging'/'run_eth_ms4_r2_17_recoverable_safe_surplus_shadow.py'
if _STAGED.exists():
    sp=importlib.util.spec_from_file_location('r217',_STAGED);r217=importlib.util.module_from_spec(sp);sp.loader.exec_module(r217)
else:
    import tools.run_eth_ms4_r2_17_recoverable_safe_surplus_shadow as r217
r28=r217.r28;v2=r217.v2;EPS=1e-9

class DirectionTemporaryRiskShadow(r217.RecoverableSafeSurplusShadow):
    def __init__(self,tape,max_slots=4):
        super().__init__(tape,max_slots)
        self.r227=Counter();self.r227Events=[];self._r227seen=set()

    def _signal_row(self,qv,side):
        imb=float(qv.get('imb') or 0.0)
        depth='UP' if imb>=0.0 else 'DOWN'
        upb=float(qv['UP']['bid']);upa=float(qv['UP']['ask']);mid=0.5*(upb+upa)
        if mid>0.5+EPS:price='UP'
        elif mid<0.5-EPS:price='DOWN'
        else:price='NEUTRAL'
        consensus=(price!='NEUTRAL' and depth==price)
        return {
            'bookImbalance':imb,'depthImbalanceSide':depth,'upMid':mid,'priceImpliedSide':price,
            'priceEdgeFromHalf':mid-0.5,'signalsConsensus':bool(consensus),
            'depthAlignedToCandidate':bool(depth==side),
            'priceAlignedToCandidate':bool(price==side),
            'consensusAlignedToCandidate':bool(consensus and depth==side),
            'signalsDisagree':bool(price!='NEUTRAL' and depth!=price),
        }

    def _direction_shadow_check(self,t,qv):
        if self.scopeSide is None:return
        side=str(self.scopeSide);cand=self._expand_candidate(side)
        if cand is None:return
        p,q=cand;before=float(self._physical_floor());after=float(self._candidate_alone_floor(side,p,q));risk=max(0.0,before-after);credit=float(self._available_expand_risk_credit())
        if credit+EPS>=risk:return
        key=(int(t),int(self.scopeGeneration),side,round(p,8),round(q,8))
        if key in self._r227seen:return
        self._r227seen.add(key);self.r227['CREDIT_BLOCKED']+=1
        rec=self._recoverability(side,p,q);sig=self._signal_row(qv,side)
        if bool(rec.get('recoverable')):self.r227['RECOVERABLE']+=1
        fp=rec.get('forwardPairSum')
        favorable=bool(rec.get('recoverable') and fp is not None and float(fp)<1.0-EPS)
        if favorable:self.r227['RECOVERABLE_FAVORABLE']+=1
        if sig['depthAlignedToCandidate']:self.r227['DEPTH_ALIGNED']+=1
        if sig['priceAlignedToCandidate']:self.r227['PRICE_ALIGNED']+=1
        if sig['consensusAlignedToCandidate']:self.r227['CONSENSUS_ALIGNED']+=1
        self.r227Events.append({'t':int(t),'generation':int(self.scopeGeneration),'side':side,'expandPrice':float(p),'expandQty':float(q),
                                'riskCost':risk,'availableCredit':credit,'creditDeficit':max(0.0,risk-credit),
                                'recoverable':bool(rec.get('recoverable')),'recoverabilityReason':rec.get('reason'),
                                'forwardPairSum':fp,'recoverableFavorable':favorable,**sig})

    def _open_one_option(self,t,qv,end):
        self._direction_shadow_check(t,qv)
        # Bypass R2.17's own shadow hook; execute exact CAP1 path once.
        return r28.FanoutRoleCapacitySim._open_one_option(self,t,qv,end)

    def run_r227(self,w):
        r=super(r217.RecoverableSafeSurplusShadow,self).run_cap(w)
        r['r227Stats']=dict(self.r227);r['r227Events']=self.r227Events[:5000]
        return r

def summarize_bins(events,winner):
    # One row per market-second-candidate side and domain, using first opportunity clock in the bin.
    bins={}
    for e in events:
        k=(int(e['t'])//1000,str(e['side']))
        bins.setdefault(k,e)
    xs=list(bins.values())
    domains={
        'ALL':lambda e:True,
        'RECOVERABLE':lambda e:bool(e.get('recoverable')),
        'RECOVERABLE_FAVORABLE':lambda e:bool(e.get('recoverableFavorable')),
    }
    out={}
    for name,pred in domains.items():
        z=[e for e in xs if pred(e)]
        base_correct=sum(str(e['side']).upper()==winner for e in z)
        row={'bins':len(z),'winnerAligned':base_correct,'winnerAlignmentRate':base_correct/len(z) if z else None}
        for sig,field in [('depth','depthAlignedToCandidate'),('price','priceAlignedToCandidate'),('consensus','consensusAlignedToCandidate')]:
            a=[e for e in z if bool(e.get(field))]
            c=sum(str(e['side']).upper()==winner for e in a)
            row[sig]={'bins':len(a),'winnerAligned':c,'precision':c/len(a) if a else None,'supportShare':len(a)/len(z) if z else None}
        out[name]=row
    return out,len(xs)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True);ap.add_argument('--market-ids',required=True);ap.add_argument('--output',required=True)
    a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];tmp=Path(tempfile.mkdtemp(prefix='ms4_r227_'))
    try:
        zipfile.ZipFile(a.bundle).extractall(tmp);co={int(x['marketId']):x for x in json.load(open(tmp/'cohort.json',encoding='utf-8'))['rows']};rows=[]
        for mid in mids:
            cr=co[mid];tape=tmp/'tapes'/f'{mid}.json.xz'
            ctl=r28.FanoutRoleCapacitySim(tape,1,4)
            try:b=ctl.run_cap(cr['winner'])
            finally:ctl.close()
            sim=DirectionTemporaryRiskShadow(tape,4)
            try:s=sim.run_r227(cr['winner'])
            finally:sim.close()
            exact=(int(b['fillEvents'])==int(s['fillEvents']) and int(b['submits'])==int(s['submits']) and abs(float(b['pnlDiagnosticOnly'])-float(s['pnlDiagnosticOnly']))<=1e-12 and abs(float(b['floor'])-float(s['floor']))<=1e-12 and abs(float(b['best'])-float(s['best']))<=1e-12)
            winner=str(cr['winner']).upper();summary,nb=summarize_bins(s['r227Events'],winner)
            row={'marketId':mid,'winnerPostHocOnly':winner,'behaviorMetricsExactMatch':exact,
                 'control':{'fills':b['fillEvents'],'submits':b['submits'],'pnl':b['pnlDiagnosticOnly'],'floor':b['floor'],'best':b['best']},
                 'shadow':{'stats':s['r227Stats'],'uniqueSecondBins':nb,'summary1s':summary,'events':s['r227Events']}}
            rows.append(row);print(json.dumps({'marketId':mid,'winner':winner,'exact':exact,'stats':s['r227Stats'],'summary1s':summary},ensure_ascii=False),flush=True)

        # Aggregate unique-second bins, plus market-balanced precision so one busy market cannot dominate.
        agg_events=[];agg_stats=Counter()
        for r in rows:
            agg_stats.update(r['shadow']['stats'])
            seen=set()
            for e in r['shadow']['events']:
                k=(r['marketId'],int(e['t'])//1000,str(e['side']))
                if k in seen:continue
                seen.add(k);agg_events.append((r['marketId'],r['winnerPostHocOnly'],e))
        domains={
            'ALL':lambda e:True,
            'RECOVERABLE':lambda e:bool(e.get('recoverable')),
            'RECOVERABLE_FAVORABLE':lambda e:bool(e.get('recoverableFavorable')),
        }
        aggregate={}
        for dname,pred in domains.items():
            z=[x for x in agg_events if pred(x[2])]
            base=sum(str(e['side']).upper()==w for _,w,e in z)
            dr={'bins':len(z),'winnerAligned':base,'winnerAlignmentRate':base/len(z) if z else None,'marketsWithSupport':len(set(m for m,_,_ in z))}
            for sig,field in [('depth','depthAlignedToCandidate'),('price','priceAlignedToCandidate'),('consensus','consensusAlignedToCandidate')]:
                zz=[x for x in z if bool(x[2].get(field))];cc=sum(str(e['side']).upper()==w for _,w,e in zz)
                bym=defaultdict(list)
                for m,w,e in zz:bym[m].append(1.0 if str(e['side']).upper()==w else 0.0)
                macro=(sum(sum(v)/len(v) for v in bym.values())/len(bym)) if bym else None
                dr[sig]={'bins':len(zz),'winnerAligned':cc,'precision':cc/len(zz) if zz else None,
                         'supportShare':len(zz)/len(z) if z else None,'marketsWithSupport':len(bym),'marketBalancedPrecision':macro}
            aggregate[dname]=dr
        out={'version':'MS4_R2_27_DIRECTION_TEMPORARY_RISK_SHADOW_V1','researchOnly':True,'runtimeAuthority':False,'markets':mids,
             'allBehaviorMetricsExactMatch':all(r['behaviorMetricsExactMatch'] for r in rows),'aggregateStats':dict(agg_stats),
             'aggregateUnique1s':aggregate,'rows':rows,
             'boundary':['shadow only; CAP1 behavior exact','opportunity domain is R2.17-style venue-min Expand blocked by ordinary realized monetary credit','recoverability uses current live Repair reservations plus at most one execution-priority current-book passive Repair tranche only for descriptive opportunity classification','direction signals are fixed strict-past book depth imbalance sign and current binary midpoint side; no threshold sweep','winner is posthoc scoring only and never enters runtime state/decision','both raw and unique-1s support reported','<=180s unchanged','realistic HFT','no dream fill','no 8781']}
        op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if str(a.output).upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
        print(json.dumps({'ok':True,'allBehaviorMetricsExactMatch':out['allBehaviorMetricsExactMatch'],'aggregateStats':out['aggregateStats'],'aggregateUnique1s':out['aggregateUnique1s']},ensure_ascii=False),flush=True)
    finally:shutil.rmtree(tmp,ignore_errors=True)

if __name__=='__main__':main()
