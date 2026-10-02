from __future__ import annotations
import argparse,json,math
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
REQ_NUM=['floor','best','fillEvents','filledQty','submits','unauthorizedOverflowQty','repairQuotaExcessMax','maxSimultaneousDistinctPrices']

def finite_num(v):
 return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(float(v))
def pnl_value(r):
 if 'pnlDiagnosticOnly' in r:return r['pnlDiagnosticOnly']
 if 'pnl' in r:return r['pnl']
 return None
def selected_rows(d,cell):
 rows=d.get('rows')
 if not isinstance(rows,list):return None
 return [r for r in rows if isinstance(r,dict) and r.get('cell')==cell]
def validate_row(r):
 errs=[]
 if 'marketId' not in r or not finite_num(r.get('marketId')):errs.append('marketId missing/nonfinite')
 pv=pnl_value(r)
 if not finite_num(pv):errs.append('pnl missing/nonfinite')
 for k in REQ_NUM:
  if k not in r or not finite_num(r.get(k)):errs.append(f'{k} missing/nonfinite')
 for k in ('roleSubmits','roleFills'):
  v=r.get(k)
  if not isinstance(v,dict):errs.append(f'{k} missing/not_dict');continue
  for rk,rv in v.items():
   if not finite_num(rv) or float(rv)<-EPS:errs.append(f'{k}.{rk} invalid')
 cc=r.get('candidateCorrectness',r.get('correctness'))
 if cc is not None and not isinstance(cc,dict):errs.append('candidateCorrectness/correctness not_dict')
 return errs
def compact(r):
 return {'marketId':int(r['marketId']),'pnl':float(pnl_value(r)),'floor':float(r['floor']),'best':float(r['best']),
         'fillEvents':int(r['fillEvents']),'filledQty':float(r['filledQty']),'submits':int(r['submits']),
         'roleSubmits':dict(r['roleSubmits']),'roleFills':dict(r['roleFills']),
         'unauthorizedOverflowQty':float(r['unauthorizedOverflowQty']),'repairQuotaExcessMax':float(r['repairQuotaExcessMax']),
         'maxSimultaneousDistinctPrices':int(r['maxSimultaneousDistinctPrices']),
         'candidateCorrectness':r.get('candidateCorrectness',r.get('correctness')),
         'rawInterventions':r.get('candidateInterventions',r.get('interventions'))}
def agg(xs):
 pn=[x['pnl'] for x in xs];fl=[x['floor'] for x in xs];rs=Counter();rf=Counter()
 for x in xs:rs.update(x['roleSubmits']);rf.update(x['roleFills'])
 return {'marketsCompleted':len(xs),'wins':sum(v>0 for v in pn),'winRate':sum(v>0 for v in pn)/len(xs) if xs else None,
         'totalPnl':sum(pn),'avgPnl':sum(pn)/len(xs) if xs else None,'aggregateFloor':sum(fl),'avgFloor':sum(fl)/len(xs) if xs else None,
         'worstMarketPnl':min(pn) if pn else None,'worstTerminalFloor':min(fl) if fl else None,
         'totalFillEvents':sum(x['fillEvents'] for x in xs),'totalFilledQty':sum(x['filledQty'] for x in xs),'totalSubmits':sum(x['submits'] for x in xs),
         'roleSubmits':dict(rs),'roleFills':dict(rf),'maxUnauthorizedOverflowQty':max((x['unauthorizedOverflowQty'] for x in xs),default=None),
         'maxRepairQuotaExcess':max((x['repairQuotaExcessMax'] for x in xs),default=None),'maxDistinctPrices':max((x['maxSimultaneousDistinctPrices'] for x in xs),default=None)}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--candidate',required=True);ap.add_argument('--output',required=True);ap.add_argument('--cell',default='GPT6_CANDIDATE');ap.add_argument('--benchmark',default=str(DEFAULT_BENCH));a=ap.parse_args()
 b=json.load(open(a.benchmark,encoding='utf-8'));d=json.load(open(a.candidate,encoding='utf-8'))
 expected=[int(x['marketId']) for x in b['markets']]
 raw=selected_rows(d,a.cell)
 schema_errors=[]
 if raw is None:raw=[];schema_errors.append('top-level rows missing/not_list')
 for i,r in enumerate(raw):
  for e in validate_row(r):schema_errors.append(f'row[{i}] {e}')
 ids=[int(r['marketId']) for r in raw if 'marketId' in r and finite_num(r.get('marketId'))]
 counts=Counter(ids);duplicates=sorted([m for m,n in counts.items() if n!=1]);missing=sorted(set(expected)-set(ids));extra=sorted(set(ids)-set(expected))
 integrity=not schema_errors and not duplicates and not missing and not extra and len(raw)==len(expected)
 rows=[compact(r) for r in raw] if integrity else []
 by={x['marketId']:x for x in rows};xs=[by[m] for m in expected] if integrity else []
 r40=b['aggregate']['R240_CONTROL'];cap=b['aggregate']['CAP1_CONTROL'];ag=agg(xs)
 details=[];family=defaultdict(lambda:{'marketIds':[],'candidatePnl':0.0,'r240Pnl':0.0,'candidateFloor':0.0,'r240Floor':0.0})
 winner_delta={'pnlDelta':0.0,'floorDelta':0.0,'marketIds':[]};loss_delta={'pnlDelta':0.0,'floorDelta':0.0,'marketIds':[]}
 if integrity:
  for bm in b['markets']:
   m=int(bm['marketId']);x=by[m];r=bm['R240_CONTROL'];c=bm['CAP1_CONTROL'];pd=x['pnl']-r['pnl'];fd=x['floor']-r['floor']
   fam_raw=(bm.get('failureAttribution') or {}).get('mechanismFamily');fam=FAMILY_MAP.get(fam_raw,'ORIGINAL_WIN_OR_UNATTRIBUTED')
   details.append({'marketId':m,'originalCap1Outcome':bm['originalCap1Outcome'],'family':fam,'exceptionNote':bm.get('exceptionNote'),'candidate':x,'r240':r,
                   'candidateVsR240':{'pnlDelta':pd,'floorDelta':fd,'bestDelta':x['best']-r['best'],'fillDelta':x['fillEvents']-r['fillEvents'],'submitDelta':x['submits']-r['submits']}})
   if bm['originalCap1Outcome']=='WIN':winner_delta['marketIds'].append(m);winner_delta['pnlDelta']+=x['pnl']-c['pnl'];winner_delta['floorDelta']+=x['floor']-c['floor']
   else:loss_delta['marketIds'].append(m);loss_delta['pnlDelta']+=x['pnl']-c['pnl'];loss_delta['floorDelta']+=x['floor']-c['floor']
   if fam!='ORIGINAL_WIN_OR_UNATTRIBUTED':
    z=family[fam];z['marketIds'].append(m);z['candidatePnl']+=x['pnl'];z['r240Pnl']+=r['pnl'];z['candidateFloor']+=x['floor'];z['r240Floor']+=r['floor']
  for z in family.values():z['pnlDeltaVsR240']=z['candidatePnl']-z['r240Pnl'];z['floorDeltaVsR240']=z['candidateFloor']-z['r240Floor']
 known_correct=bool(integrity and ag['maxUnauthorizedOverflowQty'] is not None and ag['maxRepairQuotaExcess'] is not None and ag['maxDistinctPrices'] is not None and ag['maxUnauthorizedOverflowQty']<=EPS and ag['maxRepairQuotaExcess']<=EPS and ag['maxDistinctPrices']<=4)
 fill_ret=(ag['totalFillEvents']/r40['totalFillEvents']) if integrity and r40['totalFillEvents'] else None
 sub_ret=(ag['totalSubmits']/r40['totalSubmits']) if integrity and r40['totalSubmits'] else None
 family_improved=sum(1 for z in family.values() if z['pnlDeltaVsR240']>EPS and z['floorDeltaVsR240']>=-EPS) if integrity else 0
 checks={'dataIntegrityPass':integrity,'allMarketsCompleted':integrity,'knownCorrectnessPass':known_correct,'fillRetentionVsR240':fill_ret,'submitRetentionVsR240':sub_ret,
         'antiCollapse50pctResearchGate':bool(integrity and fill_ret is not None and sub_ret is not None and fill_ret>=0.5 and sub_ret>=0.5),
         'pnlAtLeastR240':bool(integrity and ag['totalPnl']+EPS>=r40['totalPnl']),
         'floorAtLeastR240':bool(integrity and ag['aggregateFloor']+EPS>=r40['aggregateFloor']),
         'originalWinnerAggregatePnlNotWorse':bool(integrity and winner_delta['pnlDelta']>=-EPS),
         'familiesPnlAndFloorImprovedVsR240':family_improved,
         'machineStrictPass':False}
 checks['machineStrictPass']=bool(checks['dataIntegrityPass'] and checks['knownCorrectnessPass'] and checks['antiCollapse50pctResearchGate'] and checks['pnlAtLeastR240'] and checks['floorAtLeastR240'] and checks['originalWinnerAggregatePnlNotWorse'] and family_improved>=2)
 out={'version':'GPT6_THREE_FAILURE_SYSTEM_CHALLENGE_EXTERNAL_SCORE_V2_STRICT','candidateFile':a.candidate,'candidateCell':a.cell,
      'dataIntegrity':{'schemaErrors':schema_errors,'duplicateMarketIds':duplicates,'missingMarkets':missing,'extraMarkets':extra,'selectedRowCount':len(raw),'expectedRowCount':len(expected)},
      'aggregate':{'CAP1_CONTROL':cap,'R240_CONTROL':r40,'GPT6_CANDIDATE':ag},
      'candidateVsR240':None if not integrity else {'totalPnlDelta':ag['totalPnl']-r40['totalPnl'],'aggregateFloorDelta':ag['aggregateFloor']-r40['aggregateFloor'],'fillRetention':fill_ret,'submitRetention':sub_ret},
      'originalWinnerCollateralVsCAP1':winner_delta,'originalLossRecoveryVsCAP1':loss_delta,'familyStratifiedVsR240':dict(family),'checks':checks,'perMarket':details,
      'note':'Missing/null/nonfinite/duplicate/unexpected selected candidate rows are hard failures. Candidate-specific semantic invariants still require explicit manual review and may only make PASS stricter.'}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':integrity,'checks':checks,'dataIntegrity':out['dataIntegrity'],'candidateVsR240':out['candidateVsR240']},ensure_ascii=False))
if __name__=='__main__':main()
