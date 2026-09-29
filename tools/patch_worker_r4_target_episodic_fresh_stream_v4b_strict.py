from pathlib import Path
src=Path('tools/worker_r4_target_episodic_fresh_stream_v4.py')
dst=Path('tools/worker_r4_target_episodic_fresh_stream_v4b_strict.py')
lines=src.read_text(encoding='utf-8').splitlines()
for i,l in enumerate(lines):
    if "seen_a=[]; seen_h=[]; curve=[]; accepted_counts={'purpose':0,'role':0,'hazard':0}; any_accept=0" in l:
        lines[i]="    seen_a=[]; seen_h=[]; curve=[]; accepted=0"
        break
start=next(i for i,l in enumerate(lines) if 'before=v3.teacher_loss_parts' in l)
end=next(i for i,l in enumerate(lines[start:],start) if "print(json.dumps({'step':step" in l)
block='''        before=v3.current_teacher_loss(champion,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,device,losses)
        cand=v3.clone_models(champion,v11,hz,X.shape[-1],device)
        v3.update_one_market(cand,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,rep_a,rep_h,anchor_a,anchor_h,device,losses,rng,steps=4,lr=3e-5)
        after=v3.current_teacher_loss(cand,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,device,losses)
        g=v3.eval_all(cand,(X,M,purpose,role),(HX,HM,HY),guard_a,guard_h,device)
        teacher_ok=before is not None and after is not None and after < before-1e-7
        all_components_ok=all(g[k]['auc']>=prev_guard[k]['auc'] for k in ['purpose','role','hazardAny','hazardTaker','hazardAdd'])
        accept=bool(teacher_ok and all_components_ok)
        if accept:
            champion=cand; prev_guard=g; accepted+=1
        else:
            del cand
            if torch.cuda.is_available(): torch.cuda.empty_cache()
        seen_a.append(new_a); seen_h.append(new_h)
        curve.append({'step':step,'marketId':int(mid),'accepted':accept,'teacherLossBefore':before,'teacherLossAfter':after,'candidateGuardCompositeAuc':g['compositeAuc'],'championGuardCompositeAuc':prev_guard['compositeAuc']})
        print(json.dumps({'step':step,'marketId':int(mid),'accepted':accept,'guardComposite':prev_guard['compositeAuc']}),flush=True)'''.splitlines()
lines[start:end+1]=block
text='\n'.join(lines)+'\n'
text=text.replace("'version':'R4_TARGET_EPISODIC_FRESH_STREAM_V4_RESULT'","'version':'R4_TARGET_EPISODIC_FRESH_STREAM_V4B_STRICT_RESULT'")
text=text.replace("'selectiveRollback':'V3 frozen semantics'","'rollback':'whole candidate accepted only if all five guard AUCs individually non-decrease'")
text=text.replace("'acceptedHeadUpdates':accepted_counts,'episodesWithAnyAcceptedHead':any_accept,","'acceptedUpdates':accepted,'rejectedUpdates':len(adapt_ids)-accepted,")
text=text.replace("'version':'R4_TARGET_EPISODIC_FRESH_STREAM_V4_CHAMPION'","'version':'R4_TARGET_EPISODIC_FRESH_STREAM_V4B_STRICT_CHAMPION'")
text=text.replace("'acceptedHeadUpdates':accepted_counts,","'acceptedUpdates':accepted,")
text=text.replace("print(json.dumps({'acceptedHeadUpdates':accepted_counts,'episodesWithAnyAcceptedHead':any_accept,'baselineFinalComposite':baseline_final['compositeAuc'],'finalFinalComposite':final_final['compositeAuc'],'delta':final_final['compositeAuc']-baseline_final['compositeAuc'],'out':str(out)},indent=2),flush=True)","print(json.dumps({'accepted':accepted,'rejected':len(adapt_ids)-accepted,'baselineFinalComposite':baseline_final['compositeAuc'],'finalFinalComposite':final_final['compositeAuc'],'delta':final_final['compositeAuc']-baseline_final['compositeAuc'],'out':str(out)},indent=2),flush=True)")
dst.write_text(text,encoding='utf-8')
print(dst)
