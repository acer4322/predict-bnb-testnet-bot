from __future__ import annotations
import json,math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_EXACT_FORK_H100_MERGED_V1_20260907.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_ACTION_VALUE_CORPUS_V1_20260907.json'
EPS=1e-9

def apply_candidate(spec,side,p,q):
 u=float(spec['inventory']['UP']);d=float(spec['inventory']['DOWN']);c=float(spec['cost'])
 if side=='UP':u+=q
 else:d+=q
 c+=p*q
 pu=u-c;pd=d-c;fav=str(spec['expandSide']);weak=str(spec['weakSide']);pay={'UP':pu,'DOWN':pd}
 return {'floor':min(pu,pd),'best':max(pu,pd),'gap':abs(pu-pd),'favoredPayoff':pay[fav],'weakPayoff':pay[weak]}

def main():
 d=json.loads(SRC.read_text(encoding='utf-8'));rows=[]
 for r in d['rows']:
  s=r['stateSpec'];u=float(s['inventory']['UP']);dn=float(s['inventory']['DOWN']);c=float(s['cost']);fav=str(s['expandSide']);weak=str(s['weakSide']);pay={'UP':u-c,'DOWN':dn-c}
  rp=s['repairCandidate'];ep=s['expandCandidate'];ri=apply_candidate(s,str(rp['side']),float(rp['price']),float(rp['qty']));ei=apply_candidate(s,str(ep['side']),float(ep['price']),float(ep['qty']))
  cur={'floor':float(s['floor']),'best':float(s['best']),'gap':float(s['best'])-float(s['floor']),'favoredPayoff':float(pay[fav]),'weakPayoff':float(pay[weak])}
  f={
   'repairProgressFrac':float(s['repairProgressFrac']),'remainingFrac':float(s['remainingDebtQty'])/float(s['initialDebtQty']) if float(s['initialDebtQty'])>EPS else 0.0,
   'initialDebtQty':float(s['initialDebtQty']),'paidDebtQty':float(s['paidDebtQty']),'remainingDebtQty':float(s['remainingDebtQty']),
   'upQty':u,'downQty':dn,'absNet':abs(u-dn),'cost':c,'floor':float(s['floor']),'best':float(s['best']),'payoffGap':float(s['best'])-float(s['floor']),
   'favoredQty':float(s['inventory'][fav]),'weakQty':float(s['inventory'][weak]),'favoredPayoff':float(pay[fav]),'weakPayoff':float(pay[weak]),
   'freeSlots':float(s['freeSlots']),'liveSlots':float(s['liveSlots']),'qLadderLive':1.0 if s['qLadderLive'] else 0.0,'liveRepairSlots':float(s['liveRepairSlots']),'liveExpandSlots':float(s['liveExpandSlots']),
   'bookImbalance':float(s['book']['imbalance']),'bookSpread':float(s['book']['spread']),'weakBid':float(s['book']['weakBid']),'weakAsk':float(s['book']['weakAsk']),'expandBid':float(s['book']['expandBid']),'expandAsk':float(s['book']['expandAsk']),
   'weakQuotedSpread':float(s['book']['weakAsk'])-float(s['book']['weakBid']),'expandQuotedSpread':float(s['book']['expandAsk'])-float(s['book']['expandBid']),
   'repairPrice':float(rp['price']),'repairQty':float(rp['qty']),'expandPrice':float(ep['price']),'expandQty':float(ep['qty']),
   'repairPriceMinusWeakBid':float(rp['price'])-float(s['book']['weakBid']),'expandPriceMinusExpandBid':float(ep['price'])-float(s['book']['expandBid']),
  }
  for k in ('floor','best','gap','favoredPayoff','weakPayoff'):
   f[f'repairImmediateDelta{k[0].upper()+k[1:]}']=float(ri[k])-float(cur[k]);f[f'reexpandImmediateDelta{k[0].upper()+k[1:]}']=float(ei[k])-float(cur[k]);f[f'immediateReexpandMinusRepair{k[0].upper()+k[1:]}']=float(ei[k])-float(ri[k])
  y={f'd{k[0].upper()+k[1:]}':float(r['reexpandMinusRepairTerminalVector'][k]) for k in ('floor','best','gap','favoredPayoff','weakPayoff')}
  y.update({'dFills':float(r['reexpandMinusRepairTerminalVector']['fills']),'dSubmits':float(r['reexpandMinusRepairTerminalVector']['submits']),'dAlternations':float(r['reexpandMinusRepairTerminalVector']['alternations']),'dActiveSubmits':float(r['reexpandMinusRepairTerminalVector']['activeSubmits']),'dManagedRepairQty':float(r['reexpandMinusRepairTerminalVector']['managedRepairQty'])})
  rr=r['branchResolution']['NEXT_REPAIR'];er=r['branchResolution']['NEXT_REEXPAND'];y['repairStructuralFill']=1.0 if rr and rr['kind']=='FILL' else 0.0;y['reexpandStructuralFill']=1.0 if er and er['kind']=='FILL' else 0.0;y['repairResolutionLagMs']=None if rr is None else int(rr['lagMs']);y['reexpandResolutionLagMs']=None if er is None else int(er['lagMs'])
  rows.append({'marketId':int(r['marketId']),'t':int(s['t']),'features':f,'targets':y,'nativeClassDiagnosticOnly':str(s['nativeClass']),'floorBestClassDiagnosticOnly':str(r.get('floorBestClass'))})
 rows.sort(key=lambda x:(x['t'],x['marketId']))
 families={
  'PROGRESS_ONLY':['repairProgressFrac','remainingFrac'],
  'PORTFOLIO_RESPONSIBILITY':['repairProgressFrac','remainingFrac','initialDebtQty','paidDebtQty','remainingDebtQty','upQty','downQty','absNet','cost','floor','best','payoffGap','favoredQty','weakQty','favoredPayoff','weakPayoff'],
  'EXECUTION_BOOK_ONLY':['freeSlots','liveSlots','qLadderLive','liveRepairSlots','liveExpandSlots','bookImbalance','bookSpread','weakBid','weakAsk','expandBid','expandAsk','weakQuotedSpread','expandQuotedSpread','repairPrice','repairQty','expandPrice','expandQty','repairPriceMinusWeakBid','expandPriceMinusExpandBid'],
  'FULL_WITH_CANDIDATE_GEOMETRY':list(rows[0]['features'].keys()) if rows else []
 }
 out={'version':'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_ACTION_VALUE_CORPUS_V1_20260907','researchOnly':True,'runtimeAuthority':False,'source':str(SRC.relative_to(ROOT)),'rows':rows,'rowCount':len(rows),'featureFamilies':families,
      'primaryTargets':['dFloor','dBest','dFavoredPayoff','dWeakPayoff'],'secondaryTargets':['dGap','dFills','dSubmits','dAlternations','dActiveSubmits','dManagedRepairQty'],
      'boundary':['strict-past pre-branch state/candidate features only','nativeClass is diagnostic and excluded from feature families','winner/settlement/Target/future absent','first eligible state per consumed market','targets are exact same-prefix REEXPAND minus REPAIR terminal consequence vectors','no scalar reward/no runtime authority/no NEW24-B/no 8781']}
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'output':str(OUT.relative_to(ROOT)),'rows':len(rows),'features':len(families['FULL_WITH_CANDIDATE_GEOMETRY'])},ensure_ascii=False))
if __name__=='__main__':main()
