from __future__ import annotations
import csv, hashlib, json, py_compile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PKG=ROOT/'data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907'
REQUIRED=[
'00_READ_ME_FIRST.md','01_MISSION_AND_CORE_QUESTIONS.md','02_CONFIDENCE_SOURCE_HYPOTHESES_AND_DEFINITIONS.md','03_EVIDENCE_GUIDE_AND_DATA_DICTIONARY.md','04_ANALYSIS_AND_FALSIFICATION_PROTOCOL.md','05_CONFOUNDERS_MISSINGNESS_AND_ANTI_LEAKAGE.md','06_REQUIRED_OUTPUTS_AND_VERDICT_SCHEMA.md','07_EXTERNAL_LAB_BOUNDARY_AND_NEXT_TEST.md','08_GPT6_ENTRY_PROMPT.md','09_EVIDENCE_MANIFEST.json',
'21_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_PRIMARY_V1.csv','22_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_AUDIT_V1.csv','23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv','24_TARGET_BTC_DIRECTION_CONFIDENCE_MARKET_SUMMARY_V1.csv','25_TARGET_BTC_DIRECTION_CONFIDENCE_DATA_AUDIT_V1.json','26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv','27_TARGET_BTC_MAKER_CANCEL_DIAGNOSTICS_RETROSPECTIVE_V1.csv','28_EXECUTION_DIAGNOSTIC_EXPORT_AUDIT_V1.json','29_TARGET_DIRECTION_CONFIDENCE_DATA_GATE_V1.json','30_CODE_PREFLIGHT_PUBLIC_JOIN_V1.py','31_CODE_PREFLIGHT_EXECUTION_STATE_V1.py','32_CODE_SELECT_COMMON_COHORT_V1.py','33_CODE_MATERIALIZE_JOINT_DATASET_V1.py','34_CODE_EXPORT_EXECUTION_DIAGNOSTICS_V1.py','35_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_RETROSPECTIVE_V1.csv','36_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_AUDIT_V1.json','37_CODE_EXPORT_ACTION_PARENT_BRIDGE_V1.py',
'90_PRIOR_GPT6_DIRECTIONAL_THESIS_VERDICT_DO_NOT_INHERIT_V1.md','91_PRIOR_GPT6_DIRECTION_PERSISTENCE_EVIDENCE_DO_NOT_INHERIT_V1.json','92_PRIOR_GPT6_MARKET_VS_STATEFUL_MODEL_DO_NOT_INHERIT_V1.md','93_PRIOR_GPT6_DIRECTION_CONFLICT_ATLAS_DO_NOT_INHERIT_V1.md','94_PRIOR_GPT6_DIRECTION_FALSIFICATION_PLAN_DO_NOT_INHERIT_V1.md','95_PRIOR_GPT6_DIRECTION_ANALYSIS_CODE_DO_NOT_ASSUME_VALID_V1.py']
EXPECTED_ROWS={
'21_TARGET_BTC_DIRECTION_CONFIDENCE_ACTION_CLOCKS_PRIMARY_V1.csv':7974,
'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv':137747,
'24_TARGET_BTC_DIRECTION_CONFIDENCE_MARKET_SUMMARY_V1.csv':120,
'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv':8776,
'27_TARGET_BTC_MAKER_CANCEL_DIAGNOSTICS_RETROSPECTIVE_V1.csv':258314,
'35_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_RETROSPECTIVE_V1.csv':15077}
JSONS=['09_EVIDENCE_MANIFEST.json','25_TARGET_BTC_DIRECTION_CONFIDENCE_DATA_AUDIT_V1.json','28_EXECUTION_DIAGNOSTIC_EXPORT_AUDIT_V1.json','29_TARGET_DIRECTION_CONFIDENCE_DATA_GATE_V1.json','36_TARGET_BTC_MAKER_FILL_PARENT_BRIDGE_AUDIT_V1.json','91_PRIOR_GPT6_DIRECTION_PERSISTENCE_EVIDENCE_DO_NOT_INHERIT_V1.json']
CODES=['30_CODE_PREFLIGHT_PUBLIC_JOIN_V1.py','31_CODE_PREFLIGHT_EXECUTION_STATE_V1.py','32_CODE_SELECT_COMMON_COHORT_V1.py','33_CODE_MATERIALIZE_JOINT_DATASET_V1.py','34_CODE_EXPORT_EXECUTION_DIAGNOSTICS_V1.py','37_CODE_EXPORT_ACTION_PARENT_BRIDGE_V1.py','95_PRIOR_GPT6_DIRECTION_ANALYSIS_CODE_DO_NOT_ASSUME_VALID_V1.py']

def csv_count(p:Path):
    with p.open('r',encoding='utf-8-sig',newline='') as f:
        r=csv.reader(f); next(r,None); return sum(1 for _ in r)

def main():
    errors=[]; row_counts={}; json_ok={}; compile_ok={}
    for fn in REQUIRED:
        if not (PKG/fn).is_file(): errors.append(f'missing:{fn}')
    for fn,n in EXPECTED_ROWS.items():
        try:
            c=csv_count(PKG/fn); row_counts[fn]=c
            if c!=n: errors.append(f'row_count:{fn}:{c}!={n}')
        except Exception as e: errors.append(f'csv_error:{fn}:{e}')
    for fn in JSONS:
        try:
            json.loads((PKG/fn).read_text(encoding='utf-8-sig')); json_ok[fn]=True
        except Exception as e:
            json_ok[fn]=False; errors.append(f'json_error:{fn}:{e}')
    for fn in CODES:
        try:
            py_compile.compile(str(PKG/fn),doraise=True); compile_ok[fn]=True
        except Exception as e:
            compile_ok[fn]=False; errors.append(f'compile_error:{fn}:{e}')
    gate=json.loads((PKG/'29_TARGET_DIRECTION_CONFIDENCE_DATA_GATE_V1.json').read_text(encoding='utf-8'))
    if gate.get('status')!='PASS_WITH_DECLARED_SOURCE_MISSINGNESS': errors.append('gate_status')
    if gate.get('mandatoryChecks',{}).get('strictPastViolations')!=0: errors.append('strict_past_violation_gate')
    out={'version':'GPT6_TARGET_DIRECTION_CONFIDENCE_PACKAGE_FREEZE_AUDIT_V1','status':'PASS' if not errors else 'FAIL','requiredFiles':len(REQUIRED),'rowCounts':row_counts,'jsonParse':json_ok,'pythonCompile':compile_ok,'gateStatus':gate.get('status'),'errors':errors}
    (PKG/'38_PACKAGE_FREEZE_AUDIT_V1.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))
    if errors: raise SystemExit(1)
if __name__=='__main__': main()
