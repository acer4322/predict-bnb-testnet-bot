from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/lan_worker_returns/r4-v12-same30-artifacts'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_target_sequence_v12_same30_shadow_synthesis.json'
TARGET=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_target_same30_taker_purpose_decomposition_v1.json'
PHASES=['FORMATION_180_300','MANAGEMENT_60_180','PROTECTION_0_60']

def phase_of(sec): return 'FORMATION_180_300' if sec>180 else 'MANAGEMENT_60_180' if sec>=60 else 'PROTECTION_0_60'
def rising_edges(rows,key):
    out=[];prev=False
    for q in sorted(rows,key=lambda z:z['atMs']):
        cur=bool(q.get(key))
        if cur and not prev: out.append(q)
        prev=cur
    return out

def main():
    chunks=[json.loads((SRC/f'r4_target_sequence_v12_chunk{i}.json').read_text()) for i in range(3)]
    markets=[m for c in chunks for m in c['markets']]
    target=json.loads(TARGET.read_text())
    qs=[q for m in markets for q in m.get('queries',[])]
    acts=[a for m in markets for a in m.get('actualActions',[])]
    phase={}
    all_edges=[]; formation_add_edges=[]; taker_edges=[]; repair_edges=[]
    for m in markets:
        z=sorted(m.get('queries',[]),key=lambda q:q['atMs'])
        ed=rising_edges(z,'supportedDeepPositive'); all_edges += [(m['marketId'],q) for q in ed]
        for q in ed:
            if q.get('deepRole')=='TAKER': taker_edges.append((m['marketId'],q))
            if q.get('deepPurpose')=='REPAIR': repair_edges.append((m['marketId'],q))
            if q.get('phase')=='FORMATION_180_300' and q.get('deepPurpose')=='ADD': formation_add_edges.append((m['marketId'],q))
    for ph in PHASES:
        z=[q for q in qs if q['phase']==ph]; a=[x for x in acts if phase_of(float(x['secondsLeft']))==ph]; e=[q for _,q in all_edges if q['phase']==ph]
        def mean(k): return float(np.mean([float(q[k]) for q in z])) if z else None
        phase[ph]={
            'queries':len(z),'actualActions':len(a),'actualMaker':sum(x['role']=='MAKER' for x in a),'actualTaker':sum(x['role']=='TAKER' for x in a),'actualRepair':sum(x['purpose']=='REPAIR' for x in a),'actualAdd':sum(x['purpose']=='ADD' for x in a),
            'supportRate':mean('inTargetSupport'),'hazardPositiveRate':mean('hazardPositive'),'supportedHazardRate':mean('supportedDeepPositive'),
            'meanPAny':mean('pAnyAction1s'),'meanPTakerHazard':mean('pTakerAction1s'),'meanPAddHazard':mean('pAddAction1s'),'meanPPurposeAdd':mean('pPurposeAdd'),'meanPRoleTaker':mean('pRoleTaker'),
            'supportedHazardEdges':len(e),'edgeRoleTaker':sum(q['deepRole']=='TAKER' for q in e),'edgeRoleMaker':sum(q['deepRole']=='MAKER' for q in e),'edgePurposeAdd':sum(q['deepPurpose']=='ADD' for q in e),'edgePurposeRepair':sum(q['deepPurpose']=='REPAIR' for q in e),
            'edgeTakerAdd':sum(q['deepRole']=='TAKER' and q['deepPurpose']=='ADD' for q in e),'edgeTakerRepair':sum(q['deepRole']=='TAKER' and q['deepPurpose']=='REPAIR' for q in e),'edgeMakerAdd':sum(q['deepRole']=='MAKER' and q['deepPurpose']=='ADD' for q in e),'edgeMakerRepair':sum(q['deepRole']=='MAKER' and q['deepPurpose']=='REPAIR' for q in e),
            'blockedNewAddByPhaseCheckpoints':sum(bool(q.get('blockedNewAddByPhase')) for q in z)
        }
    tb={'FORMATION_180_300':target['timeBands']['180_300'],'MANAGEMENT_60_180':target['timeBands']['60_180'],'PROTECTION_0_60':target['timeBands']['0_60']}
    comparisons={}
    for ph in PHASES:
        t=tb[ph]; p=phase[ph]
        comparisons[ph]={
            'targetTakerParents':int(t['parents']),
            'targetTakerRepairParents':int(t['REPAIR']['parents']),
            'targetTakerAddParents':int(t['ADD']['parents']),
            'r3RealizedTakerActions':int(p['actualTaker']),
            'deepSupportedHazardEdges':int(p['supportedHazardEdges']),
            'deepTakerEdges':int(p['edgeRoleTaker']),
            'deepTakerRepairEdges':int(p['edgeTakerRepair']),
            'deepTakerAddEdges':int(p['edgeTakerAdd']),
            'deepTakerEdgeVsTargetParentRatio':float(p['edgeRoleTaker']/t['parents']) if t['parents'] else None,
            'r3TakerVsTargetParentRatio':float(p['actualTaker']/t['parents']) if t['parents'] else None,
        }
    out={
      'version':'R4_TARGET_SEQUENCE_V1_2_SAME30_SHADOW_SYNTHESIS',
      'researchOnly':True,'actionAuthority':False,
      'cohort':'same30 = V5 formal10 + extension20; all 30 realistic-HFT replays completed',
      'markets':len(markets),'errors':sum('error' in m for m in markets),'queries':len(qs),'actualActions':len(acts),
      'actualRole':dict(Counter(a['role'] for a in acts)),'actualPurpose':dict(Counter(a['purpose'] for a in acts)),
      'phase':phase,'targetComparison':comparisons,
      'supportedHazardEdgesTotal':len(all_edges),'formationAddEdges':len(formation_add_edges),'takerEdgesTotal':len(taker_edges),'repairEdgesTotal':len(repair_edges),
      'interpretation':[
        'Rising-edge counts are used for action-density comparison so persistent hazard states are not counted once per checkpoint.',
        'Deep stack remains shadow-only; no order or PnL mutation occurred.',
        'FORMATION >180s is the only phase where ADD/new exposure can be considered in future action-enabled research.',
        '60-180s deep ADD outputs remain blocked by the fixed no-new-entry guard and may not authorize new exposure.',
        'Target parent counts and R3 realized actions are not identical event semantics; ratios are diagnostic magnitude comparisons only.'
      ],
      'guards':['strict-past runtime inputs','Target support gate required','coverage semantic correction applied','no <=180s new exposure','no winner/settlement input','no threshold sweep']
    }
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({k:out[k] for k in ['markets','errors','queries','actualActions','actualRole','actualPurpose','supportedHazardEdgesTotal','formationAddEdges','takerEdgesTotal']},indent=2))
    print(json.dumps({'phase':phase,'targetComparison':comparisons},indent=2))
if __name__=='__main__': main()
