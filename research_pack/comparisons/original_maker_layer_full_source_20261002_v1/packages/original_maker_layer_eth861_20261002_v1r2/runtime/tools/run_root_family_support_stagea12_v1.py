"""Fixed-cohort source support, not handback policy promotion."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.run_root_native_composite_handback_v1 import make_fork,lab,util


def support_verdict(rows):
    usable=[r for r in rows if r['exercised'] and r['publicJoined']]
    halves=[sum(r['ordinal']//6==h for r in usable) for h in [0,1]]
    responders=sum(any(abs(r['delta'][k])>1e-8 for k in ['UP','DOWN']) for r in usable)
    if len(usable)<4 or min(halves)<2: return 'INSUFFICIENT_INDEPENDENT_SOURCE_SUPPORT'
    if responders<2: return 'RESPONSE_DEGENERATE'
    if not all(r['marketActivityPass'] for r in rows): return 'SOURCE_ACTIVITY_COLLAPSE'
    if any(sum(r['P'][k] for r in rows)<.5*sum(r['S'][k] for r in rows) for k in ['fills','qty']):
        return 'SOURCE_ACTIVITY_COLLAPSE'
    return 'CROSS_MARKET_SOURCE_SUPPORTED_ONLY'


def main():
    assert Path.cwd().resolve()==ROOT and not (ROOT/'.lan_worker_v1/staging').exists()
    m=json.loads((ROOT/'STAGE_MANIFEST.json').read_text())
    for rel,sha in m['files'].items(): assert hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==sha,rel
    backend=Path('C:/BTC5M-worker/.tmp/hftbacktest_244'); native=backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    with native.open('rb') as f: assert hashlib.file_digest(f,'sha256').hexdigest()==m['externalNativeSha256']
    sys.path.insert(0,str(backend))
    mod=importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    Fork=make_fork(mod.BoundedCoreServiceFavorableRecycleSim,mod)
    golden=json.loads((ROOT/'golden.json').read_text())
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']); be=0; start=time.time(); summaries=[]; fixture=None
    last_rows=[]
    try:
        for ordinal,mid in enumerate([m['fixture']]+m['markets']):
            source=json.loads((ROOT/f'public/{mid}.json').read_text()); rows=[]; last_rows=rows
            for branch in ['N','S','P']:
                assert be<m['maxBE']; be+=1
                (out/'BE_ACCOUNTING.json').write_text(json.dumps(dict(attemptedBE=be,marketId=mid,branch=branch)))
                sim=Fork(ROOT/f'tapes/{mid}.json.xz',branch,source)
                try:
                    result=sim.run_r247('UP'); d=sim._lab; ob=d['observer']; sig=lab.signature(sim,result)
                    correct=(util.cash_check(sim.inv,sim.cost,util.native_state(sim))['pass'] and
                        all(x<=1e-8 for x in ob.max_error.values()) and result['r247ServiceCorrectnessPass'] and
                        result['unauthorizedOverflowQty']<=1e-9 and result['repairQuotaExcessMax']<=1e-9 and
                        sim.max_simultaneous_slots<=4)
                    if branch=='S': correct=correct and sig==rows[0]['signature']
                    if branch=='P':
                        correct=correct and util.fingerprint(d['witness'])==util.fingerprint(rows[1]['witness'])
                        if d['witness'] is None: correct=correct and sig==rows[0]['signature']
                        else: correct=correct and bool(d['selectedPassive'])
                    if ordinal==0: correct=correct and sig==golden['P' if branch=='P' else 'S']
                    row=dict(marketId=mid,branch=branch,signature=sig,correct=bool(correct),
                        witness=d['witness'],selectedActive=d['selectedActive'],selectedPassive=d['selectedPassive'],
                        UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,floor=result['floor'],best=result['best'],
                        cost=sim.cost,fills=sim.fills,submits=sim.submits,qty=sum(sim.inv.values()),
                        scopeCompletions=sim.scopeCompletions,scopeFlips=sim.scopeFlips,
                        peakAbsNet=ob.peak_abs_net,netIntegralShareMs=ob.abs_net_integral_ms,cashMaxError=ob.max_error,
                        fillHistory=util.clean(sim.fillHist),orders=util.clean(sim.orders),activeMeta=util.clean(sim.activeMeta),
                        serviceLedger=util.clean(sim.serviceLedger))
                    rows.append(row)
                    assert correct, f'correctness/handoff/parity {mid} {branch}'
                finally: sim.close()
            n,s,p=rows; keep=['UP','DOWN','floor','best','cost','fills','qty','submits','scopeCompletions','scopeFlips','peakAbsNet','netIntegralShareMs']
            summary=dict(marketId=mid,ordinal=ordinal-1,selected=s['witness'] is not None,
                exercised=bool(s['selectedActive'] and p['selectedPassive']),
                publicJoined=bool(s['witness'] and s['witness']['public']),
                marketActivityPass=s['fills']==0 or p['fills']>0,
                S={k:s[k] for k in keep},P={k:p[k] for k in keep},delta={k:p[k]-s[k] for k in keep})
            blob=json.dumps(util.clean(dict(rows=rows,summary=summary)),indent=2).encode()
            assert len(blob)<=512*1024
            name=f'{mid}.json'; (out/name).write_bytes(blob)
            summary.update(artifact=name,artifactSha256=hashlib.sha256(blob).hexdigest())
            if ordinal==0: fixture=summary
            else: summaries.append(summary)
            print(json.dumps(dict(marketId=mid,fixture=ordinal==0,selected=summary['selected'],exercised=summary['exercised'],
                deltaUP=summary['delta']['UP'],deltaDOWN=summary['delta']['DOWN'],fills=[s['fills'],p['fills']])),flush=True)
            (out/'PROGRESS.json').write_text(json.dumps(dict(attemptedBE=be,completedDevelopmentMarkets=len(summaries))))
        result=dict(verdict=support_verdict(summaries),fixture=fixture,rows=summaries)
    except Exception as e:
        result=dict(verdict='CORRECTNESS_OR_HANDOFF_STOP',error=type(e).__name__+': '+str(e),fixture=fixture,rows=summaries)
        blob=json.dumps(util.clean(last_rows),indent=2).encode()
        if len(blob)<=512*1024: (out/'PARTIAL.json').write_bytes(blob)
    result.update(attemptedBE=be,elapsedSeconds=time.time()-start,modelsTrained=0,freshUsed=0,
        policyPromotion=False,trainingAutomatic=False,stageManifestSha256=hashlib.sha256((ROOT/'STAGE_MANIFEST.json').read_bytes()).hexdigest())
    blob=json.dumps(result,indent=2).encode(); assert len(blob)<=128*1024
    (out/'COMPACT.json').write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k not in ['rows','fixture']}),flush=True)
    if result['verdict']=='CORRECTNESS_OR_HANDOFF_STOP': raise SystemExit(2)


if __name__=='__main__': main()
