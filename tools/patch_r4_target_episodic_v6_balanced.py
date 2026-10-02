from pathlib import Path
src=Path('tools/worker_r4_target_episodic_auc_ba_guard_v51_flex.py')
dst=Path('tools/worker_r4_target_episodic_class_balanced_replay_v6.py')
s=src.read_text(encoding='utf-8')
marker='def main():\n'
inject=r'''def balanced_action_pick(rng,new_idx,replay_idx,anchor_idx,labels,n=256):
    weighted=np.concatenate([new_idx,new_idx,replay_idx,anchor_idx]).astype(np.int64)
    if len(weighted)==0:return np.zeros(0,np.int64)
    out=[];half=n//2
    for cls in [0,1]:
        pool=weighted[labels[weighted]==cls]
        if len(pool):out.append(rng.choice(pool,size=half,replace=len(pool)<half).astype(np.int64))
    if len(out)==2:
        z=np.concatenate(out);rng.shuffle(z);return z
    return rng.choice(weighted,size=n,replace=len(weighted)<n).astype(np.int64)

def update_one_market_v6(models,action,hazard,new_a,new_h,rep_a,rep_h,anchor_a,anchor_h,device,losses,rng,steps=4,lr=3e-5):
    X,M,purpose,role=action;HX,HM,HY=hazard
    opts={k:torch.optim.AdamW(m.parameters(),lr=lr,weight_decay=1e-4) for k,m in models.items()}
    for _ in range(steps):
        for key,y in [('purpose',purpose),('role',role)]:
            ai=balanced_action_pick(rng,new_a,rep_a,anchor_a,y,256)
            if len(ai):
                models[key].train();opts[key].zero_grad(set_to_none=True)
                xb=torch.from_numpy(X[ai]).to(device);mb=torch.from_numpy(M[ai]).to(device);yy=torch.from_numpy(y[ai]).to(device)
                loss=losses[key](models[key](xb,mb),yy);loss.backward();torch.nn.utils.clip_grad_norm_(models[key].parameters(),1.0);opts[key].step()
        hi=v3.sample_mix(rng,new_h,rep_h,anchor_h,128,64,64)
        if len(hi):
            models['hazard'].train();opts['hazard'].zero_grad(set_to_none=True)
            xb=torch.from_numpy(HX[hi]).to(device);mb=torch.from_numpy(HM[hi]).to(device);yy=torch.from_numpy(HY[hi]).to(device)
            loss=losses['hazard'](models['hazard'](xb,mb),yy);loss.backward();torch.nn.utils.clip_grad_norm_(models['hazard'].parameters(),1.0);opts['hazard'].step()

'''
s=s.replace(marker,inject+marker,1)
s=s.replace("v3.update_one_market(cand,(X,M,purpose,role),(HX,HM,HY),na,nh,raidx,rhidx,anc_a,anc_h,dev,losses,rng,steps=4,lr=3e-5)","update_one_market_v6(cand,(X,M,purpose,role),(HX,HM,HY),na,nh,raidx,rhidx,anc_a,anc_h,dev,losses,rng,steps=4,lr=3e-5)")
s=s.replace("'version':'R4_TARGET_EPISODIC_AUC_BA_GUARD_V5_1_RESULT'","'version':'R4_TARGET_EPISODIC_CLASS_BALANCED_REPLAY_V6_RESULT'")
s=s.replace("'version':'R4_TARGET_EPISODIC_AUC_BA_GUARD_V5_1_CHAMPION'","'version':'R4_TARGET_EPISODIC_CLASS_BALANCED_REPLAY_V6_CHAMPION'")
dst.write_text(s,encoding='utf-8')
print(dst)
