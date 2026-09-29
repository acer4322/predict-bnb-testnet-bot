from pathlib import Path
p=Path('tools/audit_lane_g_post_first_event_parallel_candidate_fate_v1.py')
s=p.read_text(encoding='utf-8')
old="""  if cand_rep>EPS:return 'CANDIDATE_PAYS_REMAINING_SHARED_REPAIR_DEBT'
  if cand_ov>EPS:
   return 'CANDIDATE_TRANSITIONS_TO_OWNED_SUCCESSOR_OVERFLOW' if native_birth else 'OWNERSHIP_SEMANTIC_UNRESOLVED'"""
new="""  if cand_ov>EPS:
   owned=any(str(e.get('sourceKey'))==str(self.candidateKey) for e in native_birth)
   return 'CANDIDATE_TRANSITIONS_TO_OWNED_SUCCESSOR_OVERFLOW' if owned else 'OWNERSHIP_SEMANTIC_UNRESOLVED'
  if cand_rep>EPS:return 'CANDIDATE_PAYS_REMAINING_SHARED_REPAIR_DEBT'"""
assert old in s
s=s.replace(old,new)
old2="  if overflow>EPS and not native_birth: correctness=False"
new2="""  endpoint_ov=float((self.postEndpoint or {}).get('candidateOverflowAllocated') or 0.0)
  endpoint_owned=any(str(e.get('sourceKey'))==str(self.candidateKey) for e in ((self.postEndpoint or {}).get('nativeSuccessorOwnerEvents') or []))
  if endpoint_ov>EPS and not endpoint_owned: correctness=False"""
assert old2 in s
s=s.replace(old2,new2)
p.write_text(s,encoding='utf-8')
print('patched')
