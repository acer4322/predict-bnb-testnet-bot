from __future__ import annotations
import csv, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
LABELS=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.csv'
OUT=P/'r4_p0b_stage3_strictpast_role_dataset_v1.csv'
REPORT=P/'r4_p0b_stage3_strictpast_role_dataset_v1.json'
FEATURES=[
 'seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross','floor','absNet',
 'weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s','current_mode_age_s',
 'events_5s','events_15s','transitions_15s','weak_unresolved_shares','dominant_unresolved_shares',
 'weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s',
 'weakResponsibilityCount','dominantResponsibilityCount','requested_qty','requested_px','candidateQty','candidatePx',
 'candidateGap','candidateFloor','candidateAbsNet','candidateUpside','candidateCoverage','candidateReservedQty',
 'candidateReservedRootCount','candidateCap','secondsLeftPublic','predictUpMidPublic','predictDownMidPublic','spotMinusStrikeBpsPublic'
]
LEDGER_CANDIDATE_FIELDS=['reservedQty','ackedCommitment','pendingSubmitCommitment','cancelPendingCommitment']

def load_labels():
    out={}
    with LABELS.open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f): out[(int(r['marketId']),r['candidateKey'])]=r
    return out

def iter_rows():
    pats=['r4_p0b_role_group_development_v1.json','r4_p0b_role_group_replication_v1.json','r4_p0b_role_anatomy_extension_branches_*_v1.json']
    seen={}
    for pat in pats:
        for fp in sorted(P.glob(pat)):
            obj=json.loads(fp.read_text(encoding='utf-8-sig'))
            for row in obj.get('rows',[]):
                key=(int(row['marketId']),str(row['candidateKey'])); cand=row.get('candidate') or {}
                rec={'marketId':key[0],'candidateKey':key[1],'sourceArtifact':fp.name}
                for k in FEATURES: rec[k]=cand.get(k)
                rec['candidateSide']=cand.get('candidateSide',cand.get('side')); rec['relationBefore']=cand.get('relationBefore'); rec['weakSide']=cand.get('weakSide'); rec['dominantSide']=cand.get('dominantSide')
                seen[key]=rec
    return seen

def main():
    labels=load_labels(); rows=iter_rows(); joined=[]; missing=[]
    for key,lab in labels.items():
        role=lab['knownRole']
        if key not in rows:
            missing.append({'marketId':key[0],'candidateKey':key[1],'role':role}); continue
        z=dict(rows[key]); z['knownRole']=role; z['gateA_label']='REJECT' if role=='REJECT_NO_ACTION' else 'REALIZE'; z['gateB_label']='SUBSTITUTE' if role=='PREPOSITION_REPAIR_SUBSTITUTE' else ('ADDITIVE' if role=='PARALLEL_STATE_SHAPING' else '')
        for k in LEDGER_CANDIDATE_FIELDS: z[k]=float(lab[k]) if lab.get(k) not in (None,'') else None
        joined.append(z)
    cols=['marketId','candidateKey','knownRole','gateA_label','gateB_label','candidateSide','relationBefore','weakSide','dominantSide','sourceArtifact']+FEATURES+LEDGER_CANDIDATE_FIELDS
    with OUT.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=cols); w.writeheader(); w.writerows(joined)
    counts={}
    for z in joined: counts[z['knownRole']]=counts.get(z['knownRole'],0)+1
    rep={'version':'R4_P0B_STAGE3_STRICTPAST_ROLE_DATASET_V1','researchOnly':True,'actionAuthority':False,'labelSource':LABELS.name,'rows':len(joined),'distinctMarkets':len(set(z['marketId'] for z in joined)),'roleCounts':counts,'missingKnownLabels':missing,'features':FEATURES+LEDGER_CANDIDATE_FIELDS,'strictPastGuard':'Only candidate-state/public-at-candidate fields are exported. realized*, final*, future*, successor-fill, routingChanged, credit outcome, and settlement/winner fields are excluded. acked/pending/cancel commitments are candidate-time Objective-Ledger state.','decomposition':{'gateA':'REJECT_NO_ACTION vs REALIZE(PREPOSITION or STATE_SHAPING)','gateB':'Among REALIZE only: PREPOSITION_REPAIR_SUBSTITUTE vs PARALLEL_STATE_SHAPING'}}
    REPORT.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
