from pathlib import Path

src=Path('data/research/r4_v0/p0_provenance_v1/r4_target_episodic_teacher_loop_stress_v2.py')
dst=Path('data/research/r4_v0/p0_provenance_v1/r4_target_episodic_teacher_loop_selective_v3.py')
lines=src.read_text(encoding='utf-8').splitlines()

# Insert per-head teacher loss helper before main.
main_i=next(i for i,l in enumerate(lines) if l.startswith('def main():'))
helper='''def teacher_loss_parts(models,action,hazard,aidx,hidx,device,losses):
    X,M,purpose,role=action; HX,HM,HY=hazard
    out={"purpose":None,"role":None,"hazard":None}
    with torch.no_grad():
        if len(aidx):
            xb=torch.from_numpy(X[aidx]).to(device); mb=torch.from_numpy(M[aidx]).to(device)
            yp=torch.from_numpy(purpose[aidx]).to(device); yr=torch.from_numpy(role[aidx]).to(device)
            out["purpose"]=float(losses["purpose"](models["purpose"](xb,mb),yp).cpu())
            out["role"]=float(losses["role"](models["role"](xb,mb),yr).cpu())
        if len(hidx):
            xb=torch.from_numpy(HX[hidx]).to(device); mb=torch.from_numpy(HM[hidx]).to(device); yy=torch.from_numpy(HY[hidx]).to(device)
            out["hazard"]=float(losses["hazard"](models["hazard"](xb,mb),yy).cpu())
    return out
'''.splitlines()
lines[main_i:main_i]=helper+['']

# Replace initialization.
for i,l in enumerate(lines):
    if 'seen_a=[]; seen_h=[]; seen_mids=set(); curve=[]; accepted=0' in l:
        lines[i]='    seen_a=[]; seen_h=[]; seen_mids=set(); curve=[]; accepted_counts={"purpose":0,"role":0,"hazard":0}; any_accept_count=0'
        break

# Replace loop decision block from before_loss through print.
start=next(i for i,l in enumerate(lines) if 'before_loss=current_teacher_loss' in l)
end=next(i for i,l in enumerate(lines[start:],start) if 'print(json.dumps({"step":step' in l)
new_block='''        before_parts=teacher_loss_parts(champion,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,device,losses)
        cand=clone_models(champion,v11,hz,X.shape[-1],device)
        update_one_market(cand,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,rep_a,rep_h,anchor_a,anchor_h,device,losses,rng)
        after_parts=teacher_loss_parts(cand,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,device,losses)
        g=eval_all(cand,(X,M,purpose,role),(HX,HM,HY),guard_a,guard_h,device)
        accepts={}
        # Purpose and Role have independent champions.
        for key in ["purpose","role"]:
            teacher_ok=(before_parts[key] is not None and after_parts[key] is not None and after_parts[key] < before_parts[key]-1e-7)
            guard_ok=g[key]["auc"] >= prev_guard[key]["auc"]
            accepts[key]=bool(teacher_ok and guard_ok)
        # Shared Hazard encoder/head is accepted only when all 3 hazard tasks are non-decreasing.
        teacher_ok=(before_parts["hazard"] is not None and after_parts["hazard"] is not None and after_parts["hazard"] < before_parts["hazard"]-1e-7)
        hazard_guard_ok=all(g[k]["auc"] >= prev_guard[k]["auc"] for k in ["hazardAny","hazardTaker","hazardAdd"])
        accepts["hazard"]=bool(teacher_ok and hazard_guard_ok)
        if accepts["purpose"]:
            champion["purpose"].load_state_dict(cand["purpose"].state_dict()); accepted_counts["purpose"]+=1
        if accepts["role"]:
            champion["role"].load_state_dict(cand["role"].state_dict()); accepted_counts["role"]+=1
        if accepts["hazard"]:
            champion["hazard"].load_state_dict(cand["hazard"].state_dict()); accepted_counts["hazard"]+=1
        any_accept=any(accepts.values())
        if any_accept: any_accept_count+=1
        del cand
        if torch.cuda.is_available(): torch.cuda.empty_cache()
        # Re-evaluate the selectively assembled champion; component metrics are independent by model.
        prev_guard=eval_all(champion,(X,M,purpose,role),(HX,HM,HY),guard_a,guard_h,device)
        if int(mid) not in seen_mids:
            seen_a.append(new_a); seen_h.append(new_h); seen_mids.add(int(mid))
        curve.append({
          "step":step,"cycle":cycle,"marketId":int(mid),"acceptedHeads":accepts,"anyAccepted":any_accept,
          "newActionExamples":int(len(new_a)),"newHazardExamples":int(len(new_h)),
          "teacherLossBefore":before_parts,"teacherLossAfter":after_parts,
          "candidateGuard":{k:g[k]["auc"] for k in ["purpose","role","hazardAny","hazardTaker","hazardAdd"]},
          "championGuard":{k:prev_guard[k]["auc"] for k in ["purpose","role","hazardAny","hazardTaker","hazardAdd"]}
        })
        print(json.dumps({"step":step,"cycle":cycle,"marketId":int(mid),"acceptedHeads":accepts,"guardComposite":prev_guard["compositeAuc"]}),flush=True)'''.splitlines()
lines[start:end+1]=new_block

# Patch report/checkpoint fields and version strings.
text='\n'.join(lines)+'\n'
text=text.replace('"version":"R4_TARGET_EPISODIC_TEACHER_LOOP_STRESS_V2_RESULT"','"version":"R4_TARGET_EPISODIC_TEACHER_LOOP_SELECTIVE_V3_RESULT"')
text=text.replace('"rollback":{"guardCompositeTolerance":-0.0005,"componentAucFloorDelta":-0.015}','"rollback":{"purpose":"own guard AUC non-decreasing","role":"own guard AUC non-decreasing","hazard":"ANY/TAKER/ADD guard AUC all non-decreasing"}')
text=text.replace('"acceptedUpdates":accepted,"rejectedUpdates":len(adapt_ids)-accepted,','"acceptedHeadUpdates":accepted_counts,"episodesWithAnyAcceptedHead":any_accept_count,"episodesWithNoAcceptedHead":len(adapt_ids)-any_accept_count,')
text=text.replace('"version":"R4_TARGET_EPISODIC_TEACHER_LOOP_STRESS_V2_CHAMPION"','"version":"R4_TARGET_EPISODIC_TEACHER_LOOP_SELECTIVE_V3_CHAMPION"')
text=text.replace('"acceptedUpdates":accepted,"adaptationMarkets":base_adapt_ids,"cycles":args.cycles,','"acceptedHeadUpdates":accepted_counts,"episodesWithAnyAcceptedHead":any_accept_count,"adaptationMarkets":base_adapt_ids,"cycles":args.cycles,')
old='print(json.dumps({"accepted":accepted,"rejected":len(adapt_ids)-accepted,"baselineTestComposite":baseline_test["compositeAuc"],"finalTestComposite":final_test["compositeAuc"],"delta":final_test["compositeAuc"]-baseline_test["compositeAuc"],"out":str(out),"checkpoint":str(ck)},indent=2),flush=True)'
new='print(json.dumps({"acceptedHeadUpdates":accepted_counts,"episodesWithAnyAcceptedHead":any_accept_count,"baselineTestComposite":baseline_test["compositeAuc"],"finalTestComposite":final_test["compositeAuc"],"delta":final_test["compositeAuc"]-baseline_test["compositeAuc"],"out":str(out),"checkpoint":str(ck)},indent=2),flush=True)'
text=text.replace(old,new)
text=text.replace('"interpretationGuard":"Consumed development stress. The source test61 was previously inspected in aggregate; V2 tests durability of repeated episodic learning only and cannot provide formal promotion evidence."','"interpretationGuard":"Consumed development selective-head experiment. Purpose/Role/Hazard may accept different post-episode updates; Hazard still requires all three hazard guard AUCs non-decreasing. No formal promotion evidence."')
dst.write_text(text,encoding='utf-8')
print(dst)
