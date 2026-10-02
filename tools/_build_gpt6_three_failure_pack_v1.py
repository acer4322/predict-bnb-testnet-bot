from pathlib import Path
import shutil, hashlib, json, zipfile

ROOT = Path(__file__).resolve().parents[1]
dst = ROOT / 'data/research/r4_v0/gpt6_three_failure_system_challenge_v1_20260906'
dst.mkdir(parents=True, exist_ok=True)

mapping = {
'10_SOURCE_R238_R245_SYNTHESIS.md':'data/research/r4_v0/p0_provenance_v1/MS4_R238_R245_FAILURE_CAUSALITY_AND_LIABILITY_ARCHITECTURE_SYNTHESIS_20260906.md',
'11_SOURCE_R238_PER_FILL_FAILURE_REPORT.md':'data/research/r4_v0/p0_provenance_v1/MS4_R238_PER_FILL_FAILURE_CAUSALITY_REPORT_20260906.md',
'12_SOURCE_R238_PER_FILL_FAILURE_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R238_PER_FILL_FAILURE_CAUSALITY_RESULT_20260906.json',
'20_SOURCE_R240_PREREGISTERED.json':'data/research/r4_v0/p0_provenance_v1/MS4_R240_HANDOFF_REPAIR_CREDIT_QUARANTINE_PREREGISTERED_20260906.json',
'21_SOURCE_R240_SMOKE3_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R240_SMOKE3_RESULT_20260906.json',
'22_SOURCE_R240_STAGEA16_BATCH_A_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R240_STAGEA16_BATCH_A_RESULT_20260906.json',
'23_SOURCE_R240_STAGEA16_BATCH_B_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R240_STAGEA16_BATCH_B_RESULT_20260906.json',
'24_SOURCE_R240_REMAINDER8_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R240_FULL24_REMAINDER8_RESULT_20260906.json',
'30_SOURCE_R241_ACTIVE_VETO_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R241_SMOKE8_RESULT_20260906.json',
'31_SOURCE_R242_CORE_ROUTING_BATCH_A_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R242_STAGEA16_BATCH_A_RESULT_20260906.json',
'32_SOURCE_R242_CORE_ROUTING_BATCH_B_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R242_STAGEA16_BATCH_B_RESULT_20260906.json',
'33_SOURCE_R243_PERSISTENT_CORE_SMOKE_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R243_SMOKE4_RESULT_20260906.json',
'34_SOURCE_R244_STAGEA16_BATCH_A_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R244_STAGEA16_BATCH_A_RESULT_20260906.json',
'35_SOURCE_R244_STAGEA16_BATCH_B_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R244_STAGEA16_BATCH_B_RESULT_20260906.json',
'36_SOURCE_R244_REMAINDER8_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R244_FULL24_REMAINDER8_RESULT_20260906.json',
'37_SOURCE_R245_ENRICHED_STATE_RESULT.json':'data/research/r4_v0/p0_provenance_v1/MS4_R245_ENRICHED_CORE_LIVENESS_CAUSAL_STATE_RESULT_20260906.json',
'40_SOURCE_PROVIDED_EVIDENCE_MODE_V2.md':'data/research/r4_v0/p0_provenance_v1/GPT6_BTC5M_PROVIDED_EVIDENCE_MODE_V2_20260905.md',
'41_SOURCE_MODULAR_CONTROLLER_CONTRACT_V3.md':'data/research/r4_v0/p0_provenance_v1/MODULAR_CONTROLLER_RESEARCH_CONTRACT_V3_20260905.md',
'42_SOURCE_CURRENT_HFT_OBJECTIVE_V2.json':'data/research/r4_v0/p0_provenance_v1/CURRENT_HFT_OBJECTIVE_V2_ACTION_LIVENESS_20260905.json',
'43_SOURCE_ACTIVITY_DENSITY_ANTI_COLLAPSE.md':'data/research/r4_v0/p0_provenance_v1/ACTIVITY_DENSITY_ANTI_COLLAPSE_CONTRACT_V1_20260906.md',
'80_CODE_CAP1_BASELINE.py':'tools/run_eth_ms4_r2_8_fanout_role_capacity_ablation.py',
'81_CODE_R22_FAILURE_EVIDENCE_BASE.py':'tools/run_eth_ms4_r2_2_failure_evidence_active_drain.py',
'82_CODE_R238_FAILURE_ANALYZER.py':'tools/analyze_ms4_r238_per_fill_failure_causality.py',
'83_CODE_R239_OVERFLOW_HANDOFF.py':'tools/run_eth_ms4_r2_39_overflow_responsibility_handoff.py',
'84_CODE_R240_CREDIT_QUARANTINE.py':'tools/run_eth_ms4_r2_40_handoff_repair_credit_quarantine.py',
'85_CODE_R241_ACTIVE_VETO_FALSIFICATION.py':'tools/run_eth_ms4_r2_41_negative_pair_active_veto_falsification.py',
'86_CODE_R242_CORE_FAILURE_ROUTING.py':'tools/run_eth_ms4_r2_42_core_failure_evidence_routing.py',
'87_CODE_R243_PERSISTENT_CORE_CONFIRMATION.py':'tools/run_eth_ms4_r2_43_persistent_core_failure_confirmation.py',
'88_CODE_R244_SINGLE_CORE_ACTIVE_CAUSAL.py':'tools/run_eth_ms4_r2_44_single_core_liveness_active_causal.py',
'89_CODE_R245_ENRICHED_STATE_ANALYZER.py':'tools/analyze_ms4_r245_enriched_core_liveness_causal_state.py',
}

missing=[]
for name, rel in mapping.items():
    src=ROOT/rel
    if not src.exists():
        missing.append(rel)
        continue
    shutil.copy2(src, dst/name)

# Regenerate manifest after all content is present.
manifest={}
for p in sorted(dst.iterdir()):
    if not p.is_file() or p.name=='SHA256_MANIFEST.json':
        continue
    h=hashlib.sha256(p.read_bytes()).hexdigest()
    manifest[p.name]={'sha256':h,'bytes':p.stat().st_size}
(dst/'SHA256_MANIFEST.json').write_text(json.dumps({'version':'GPT6_THREE_FAILURE_PACK_SHA256_V1','files':manifest},indent=2),encoding='utf-8')

zip_path=ROOT/'data/research/r4_v0/gpt6_three_failure_system_challenge_v1_20260906.zip'
if zip_path.exists(): zip_path.unlink()
with zipfile.ZipFile(zip_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(dst.iterdir()):
        if p.is_file(): z.write(p,arcname=f'{dst.name}/{p.name}')
print(json.dumps({'ok':not missing,'fileCount':len([p for p in dst.iterdir() if p.is_file()]),'missing':missing,'zip':str(zip_path.relative_to(ROOT)),'zipBytes':zip_path.stat().st_size},ensure_ascii=False))
