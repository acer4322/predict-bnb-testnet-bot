from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import Counter,defaultdict

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_BENCH=ROOT/'data/research/r4_v0/p0_provenance_v1/MS4_R240_CANONICAL_FULL24_BENCHMARK_20260906.json'
EPS=1e-9
FAMILY_MAP={
 'UNRECOVERED_EXPOSURE_DOMINANT':'A_UNRECOVERED_EXPOSURE',
 'REPAIR_ECONOMICS_DOMINANT':'B_REPAIR_ECONOMICS',
 'INITIAL_EXPOSURE_REPAIR_LIVENESS_FAILURE':'C_INITIAL_LIVENESS',
}

def pick_rows(d,cell):
    rows=d.get('rows') or d.get('markets') or []
    out=[]
    for r in rows:
        if cell is not None and r.get('cell')!=cell: continue
        if 'pnlDiagnosticOnly' not in r and 'pnl' not in r: continue
        out.append(r)
    return out

def compact(r):
    return {
      'marketId':int(r['marketId']),
      'pnl':float(r.get('pnlDiagnosticOnly',r.get('pnl',0.0))),
      'floor':float(r.get('floor',0.0)),
      'best':float(r.get('best',0.0)),
      'fillEvents':int(r.get('fillEvents',0)),
      'filledQty':float(r.get('filledQty',0.0)),
      'submits':int(r.get('submits',0)),
      'roleSubmits':r.get('roleSubmits') or {},
      'roleFills':r.get('roleFills') or {},
      'unauthorizedOverflowQty':float(r.get('unauthorizedOverflowQty',0.0)),
      'repairQuotaExcessMax':float(r.get('repairQuotaExcessMax',0.0)),
      'candidateCorrectness':r.get('candidateCorrectness') or r.get('correctness') or {},
      'rawInterventions':r.get('candidateInterventions') or r.get('interventions') or {},
    }

def agg(xs):
    pn=[x['pnl'] for x in xs]; fl=[x['floor'] for x in xs]; rs=Counter();rf=Counter()
    for x in xs: rs.update(x['roleSubmits']);rf.update(x['roleFills'])
    return {'marketsCompleted':len(xs),'wins':sum(v>0 for v in pn),'winRate':sum(v>0 for v in pn)/len(xs) if xs else None,
      'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs) if xs else None,'aggregateFloor':sum(fl),'avgFloor':sum(fl)/len(xs) if xs else None,
      'worstMarketPnl':min(pn) if pn else None,'worstTerminalFloor':min(fl) if fl else None,'totalFillEvents':sum(x['fillEvents'] for x in xs),
      'totalFilledQty':sum(x['filledQty'] for x in xs),'totalSubmits':sum(x['submits'] for x in xs),'roleSubmits':dict(rs),'roleFills':dict(rf),
      'maxUnauthorizedOverflowQty':max((x['unauthorizedOverflowQty'] for x in xs),default=0.0),'maxRepairQuotaExcess':max((x['repairQuotaExcessMax'] for x in xs),default=0.0)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--candidate',required=True);ap.add_argument('--output',required=True);ap.add_argument('--cell',default='GPT6_CANDIDATE');ap.add_argument('--benchmark',default=str(DEFAULT_BENCH));a=ap.parse_args()
    b=json.load(open(a.benchmark,encoding='utf-8')); d=json.load(open(a.candidate,encoding='utf-8')); rows=[compact(r) for r in pick_rows(d,a.cell)]
    by={x['marketId']:x for x in rows}; expected=[int(x['marketId']) for x in b['markets']]; missing=[m for m in expected if m not in by]; extra=[m for m in by if m not in expected]
    xs=[by[m] for m in expected if m in by]; ag=agg(xs); r40=b['aggregate']['R240_CONTROL']; cap=b['aggregate']['CAP1_CONTROL']
    details=[]; family=defaultdict(lambda:{'marketIds':[],'candidatePnl':0.0,'r240Pnl':0.0,'candidateFloor':0.0,'r240Floor':0.0})
    winner_delta={'pnlDelta':0.0,'floorDelta':0.0,'marketIds':[]}; loss_delta={'pnlDelta':0.0,'floorDelta':0.0,'marketIds':[]}
    for bm in b['markets']:
        m=int(bm['marketId']); x=by.get(m)
        if not x: continue
        r=bm['R240_CONTROL']; c=bm['CAP1_CONTROL']; pd=x['pnl']-r['pnl'];fd=x['floor']-r['floor']
        fam_raw=(bm.get('failureAttribution') or {}).get('mechanismFamily'); fam=FAMILY_MAP.get(fam_raw,'ORIGINAL_WIN_OR_UNATTRIBUTED')
        details.append({'marketId':m,'originalCap1Outcome':bm['originalCap1Outcome'],'family':fam,'exceptionNote':bm.get('exceptionNote'),'candidate':x,'r240':r,'candidateVsR240':{'pnlDelta':pd,'floorDelta':fd,'bestDelta':x['best']-r['best'],'fillDelta':x['fillEvents']-r['fillEvents'],'submitDelta':x['submits']-r['submits']}})
        if bm['originalCap1Outcome']=='WIN': winner_delta['marketIds'].append(m);winner_delta['pnlDelta']+=x['pnl']-c['pnl'];winner_delta['floorDelta']+=x['floor']-c['floor']
        else: loss_delta['marketIds'].append(m);loss_delta['pnlDelta']+=x['pnl']-c['pnl'];loss_delta['floorDelta']+=x['floor']-c['floor']
        if fam!='ORIGINAL_WIN_OR_UNATTRIBUTED':
            z=family[fam];z['marketIds'].append(m);z['candidatePnl']+=x['pnl'];z['r240Pnl']+=r['pnl'];z['candidateFloor']+=x['floor'];z['r240Floor']+=r['floor']
    for z in family.values():z['pnlDeltaVsR240']=z['candidatePnl']-z['r240Pnl'];z['floorDeltaVsR240']=z['candidateFloor']-z['r240Floor']
    known_correct=(ag['maxUnauthorizedOverflowQty']<=EPS and ag['maxRepairQuotaExcess']<=EPS)
    fill_ret=ag['totalFillEvents']/r40['totalFillEvents'] if r40['totalFillEvents'] else None; sub_ret=ag['totalSubmits']/r40['totalSubmits'] if r40['totalSubmits'] else None
    family_improved=sum(1 for z in family.values() if z['pnlDeltaVsR240']>EPS and z['floorDeltaVsR240']>=-EPS)
    checks={'allMarketsCompleted':not missing and len(xs)==24,'knownCorrectnessPass':known_correct,'fillRetentionVsR240':fill_ret,'submitRetentionVsR240':sub_ret,
      'antiCollapse50pctResearchGate':(fill_ret is None or fill_ret>=0.5) and (sub_ret is None or sub_ret>=0.5),
      'pnlAtLeastR240':ag['totalPnl']+EPS>=r40['totalPnl'],'floorAtLeastR240':ag['aggregateFloor']+EPS>=r40['aggregateFloor'],
      'originalWinnerAggregatePnlNotWorse':winner_delta['pnlDelta']>=-EPS,'familiesPnlAndFloorImprovedVsR240':family_improved}
    out={'version':'GPT6_THREE_FAILURE_SYSTEM_CHALLENGE_EXTERNAL_SCORE_V1','candidateFile':a.candidate,'candidateCell':a.cell,'missingMarkets':missing,'extraMarkets':extra,
      'aggregate':{'CAP1_CONTROL':cap,'R240_CONTROL':r40,'GPT6_CANDIDATE':ag},'candidateVsR240':{'totalPnlDelta':ag['totalPnl']-r40['totalPnl'],'aggregateFloorDelta':ag['aggregateFloor']-r40['aggregateFloor'],'fillRetention':fill_ret,'submitRetention':sub_ret},
      'originalWinnerCollateralVsCAP1':winner_delta,'originalLossRecoveryVsCAP1':loss_delta,'familyStratifiedVsR240':dict(family),'checks':checks,'perMarket':details,
      'note':'Candidate-specific invariants and bounded Floor tradeoffs require manual review; this scorer does not allow its summary flags to override semantic correctness.'}
    Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':not missing,'checks':checks,'candidateVsR240':out['candidateVsR240'],'families':dict(family)},ensure_ascii=False))
if __name__=='__main__':main()
