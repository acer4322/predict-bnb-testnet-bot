from pathlib import Path
import argparse, json, sys
import numpy as np
import torch

SEED=20260829

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--bundle-dir',required=True)
    ap.add_argument('--fresh-dir',required=True)
    ap.add_argument('--start-checkpoint',required=True)
    ap.add_argument('--out',required=True)
    ap.add_argument('--checkpoint-out',required=True)
    args=ap.parse_args()
    bundle=Path(args.bundle_dir).resolve(); fresh=Path(args.fresh_dir).resolve()
    staging=bundle.parent
    sys.path.insert(0,str(bundle)); sys.path.insert(0,str(staging))
    import train_r4_target_sequence_teacher_v1 as v1
    import train_r4_target_sequence_teacher_v11_factorized as v11
    import train_r4_target_sequence_hazard_v1 as hz
    import r4_target_episodic_teacher_loop_selective_v3 as v3
    v3.seed_all(SEED)
    device=torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

    v1rep=json.loads((bundle/'r4_target_sequence_teacher_v1_report.json').read_text(encoding='utf-8'))
    train_ids=set(map(int,v1rep['dataset']['trainMarketIds']))

    # Old frozen teacher data provides only replay anchors / class weights.
    v1.D=bundle/'data'; hz.D=bundle/'data'
    do=v1.build_rows(); Xo,Mo,yao,yco,yfo,pho,midso,timeso,supo=v1.build_sequences(do)
    actionso=np.array([v1.ACTIONS[i] for i in yao]); keepo=np.array([not a.endswith('_FLAT') for a in actionso],bool)
    Xo=Xo[keepo]; Mo=Mo[keepo]; AMIDSo=midso[keepo]
    purposeo=np.array([1 if a.endswith('_ADD') else 0 for a in actionso[keepo]],np.int64)
    roleo=np.array([1 if a.startswith('TAKER_') else 0 for a in actionso[keepo]],np.int64)
    old_train_a=np.where(np.array([int(m) in train_ids for m in AMIDSo],bool))[0]
    HXO,HMO,HYO,HPHO,HMIDSO,HTIMESO=hz.build_grid(); HYO=HYO.astype(np.float32)
    old_train_h=np.where(np.array([int(m) in train_ids for m in HMIDSO],bool))[0]

    # Fresh chronology is built from the pre-frozen raw cohort only.
    v1.D=fresh; hz.D=fresh
    df=v1.build_rows(); Xf,Mf,yaf,ycf,yff,phf,midsf,timesf,supf=v1.build_sequences(df)
    actionsf=np.array([v1.ACTIONS[i] for i in yaf]); keepf=np.array([not a.endswith('_FLAT') for a in actionsf],bool)
    Xf=Xf[keepf]; Mf=Mf[keepf]; AMIDSf=midsf[keepf]
    purposef=np.array([1 if a.endswith('_ADD') else 0 for a in actionsf[keepf]],np.int64)
    rolef=np.array([1 if a.startswith('TAKER_') else 0 for a in actionsf[keepf]],np.int64)
    HXF,HMF,HYF,HPHF,HMIDSF,HTIMESF=hz.build_grid(); HYF=HYF.astype(np.float32)

    # Combine fresh first + old training anchors at an offset so v3 update code can sample one tensor space.
    aoff=len(Xf); hoff=len(HXF)
    X=np.concatenate([Xf,Xo[old_train_a]],axis=0); M=np.concatenate([Mf,Mo[old_train_a]],axis=0)
    purpose=np.concatenate([purposef,purposeo[old_train_a]],axis=0); role=np.concatenate([rolef,roleo[old_train_a]],axis=0)
    AMIDS=np.concatenate([AMIDSf,AMIDSo[old_train_a]],axis=0)
    HX=np.concatenate([HXF,HXO[old_train_h]],axis=0); HM=np.concatenate([HMF,HMO[old_train_h]],axis=0); HY=np.concatenate([HYF,HYO[old_train_h]],axis=0)
    HMIDS=np.concatenate([HMIDSF,HMIDSO[old_train_h]],axis=0)
    anchor_pool_a=np.arange(aoff,len(X),dtype=np.int64); anchor_pool_h=np.arange(hoff,len(HX),dtype=np.int64)

    split=json.loads((fresh/'split.json').read_text(encoding='utf-8'))
    adapt_ids=list(map(int,split['adapt80'])); guard_ids=list(map(int,split['guard20'])); final_ids=list(map(int,split['final20']))
    assert len(adapt_ids)==80 and len(guard_ids)==20 and len(final_ids)==20
    def idx_for(arr,ids,limit):
        s=set(ids); return np.where(np.array([(i<limit and int(m) in s) for i,m in enumerate(arr)],bool))[0].astype(np.int64)
    guard_a=idx_for(AMIDS,guard_ids,aoff); final_a=idx_for(AMIDS,final_ids,aoff)
    guard_h=idx_for(HMIDS,guard_ids,hoff); final_h=idx_for(HMIDS,final_ids,hoff)

    # Restore model classes to fresh-independent module globals; feature schema is identical.
    ck=torch.load(Path(args.start_checkpoint),map_location='cpu',weights_only=False)
    purpose_model=v11.SeqBinary(X.shape[-1]); purpose_model.load_state_dict(ck['states']['purposeBinary'])
    role_model=v11.SeqBinary(X.shape[-1]); role_model.load_state_dict(ck['states']['roleBinary'])
    hazard_model=hz.HazardSeq(HX.shape[-1]); hazard_model.load_state_dict(ck['states']['hazard'])
    champion={'purpose':purpose_model.to(device),'role':role_model.to(device),'hazard':hazard_model.to(device)}

    losses={
      'purpose':v3.weighted_ce(purpose,anchor_pool_a,device),
      'role':v3.weighted_ce(role,anchor_pool_a,device),
      'hazard':v3.weighted_bce(HY,anchor_pool_h,device)
    }
    rng=np.random.default_rng(SEED)
    anchor_a=rng.choice(anchor_pool_a,size=min(1024,len(anchor_pool_a)),replace=False)
    anchor_h=rng.choice(anchor_pool_h,size=min(2048,len(anchor_pool_h)),replace=False)
    baseline_guard=v3.eval_all(champion,(X,M,purpose,role),(HX,HM,HY),guard_a,guard_h,device)
    baseline_final=v3.eval_all(champion,(X,M,purpose,role),(HX,HM,HY),final_a,final_h,device)
    prev_guard=baseline_guard
    seen_a=[]; seen_h=[]; curve=[]; accepted=0

    for step,mid in enumerate(adapt_ids,1):
        new_a=np.where((AMIDS[:aoff]==mid))[0].astype(np.int64)
        new_h=np.where((HMIDS[:hoff]==mid))[0].astype(np.int64)
        rep_a=np.concatenate(seen_a).astype(np.int64) if seen_a else np.zeros(0,np.int64)
        rep_h=np.concatenate(seen_h).astype(np.int64) if seen_h else np.zeros(0,np.int64)
        before=v3.current_teacher_loss(champion,(X,M,purpose,role),(HX,HM,HY),new_a,new_h,device,losses)
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
        print(json.dumps({'step':step,'marketId':int(mid),'accepted':accept,'guardComposite':prev_guard['compositeAuc']}),flush=True)

    final_guard=v3.eval_all(champion,(X,M,purpose,role),(HX,HM,HY),guard_a,guard_h,device)
    final_final=v3.eval_all(champion,(X,M,purpose,role),(HX,HM,HY),final_a,final_h,device)
    def delta(a,b):
        out={'compositeAuc':a['compositeAuc']-b['compositeAuc']}
        for k in ['purpose','role','hazardAny','hazardTaker','hazardAdd']:
            out[k]={'auc':a[k]['auc']-b[k]['auc'],'balancedAccuracy':a[k]['balancedAccuracy']-b[k]['balancedAccuracy']}
        return out
    rep={
      'version':'R4_TARGET_EPISODIC_FRESH_STREAM_V4B_STRICT_RESULT','researchOnly':True,'actionAuthority':False,
      'device':str(device),'cudaDevice':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
      'protocol':{'startCheckpoint':str(args.start_checkpoint),'adaptMarkets':adapt_ids,'guardMarkets':guard_ids,'finalMarkets':final_ids,'onePass':True,'targetLabelsUsedOnlyAfterEpisode':True,'finalUsedDuringUpdates':False,'learningRate':3e-5,'stepsPerMarketPerHead':4,'rollback':'whole candidate accepted only if all five guard AUCs individually non-decrease','oldReplayAnchorMarkets':len(train_ids)},
      'dataset':{'freshRows':int(len(df)),'freshNonflatActionExamples':int(len(Xf)),'freshHazardExamples':int(len(HXF)),'freshActionMarkets':int(len(np.unique(AMIDSf))),'freshHazardMarkets':int(len(np.unique(HMIDSF))),'oldAnchorActionExamples':int(len(anchor_pool_a)),'oldAnchorHazardExamples':int(len(anchor_pool_h))},
      'acceptedUpdates':accepted,'rejectedUpdates':len(adapt_ids)-accepted,
      'baselineGuard':baseline_guard,'finalGuard':final_guard,'guardDelta':delta(final_guard,baseline_guard),
      'baselineFinal':baseline_final,'finalFinal':final_final,'finalDelta':delta(final_final,baseline_final),
      'learningCurve':curve,
      'guards':['fresh cohort frozen before action-label extraction','strict-past features','post-episode Target labels only','no PnL/winner/settlement training','no threshold sweep','no sizing','no order mutation','no live 8781/R3/R3.1 mutation']
    }
    out=Path(args.out); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    cp=Path(args.checkpoint_out); cp.parent.mkdir(parents=True,exist_ok=True)
    torch.save({'version':'R4_TARGET_EPISODIC_FRESH_STREAM_V4B_STRICT_CHAMPION','researchOnly':True,'actionAuthority':False,'acceptedUpdates':accepted,'states':{'purposeBinary':{k:v.detach().cpu() for k,v in champion['purpose'].state_dict().items()},'roleBinary':{k:v.detach().cpu() for k,v in champion['role'].state_dict().items()},'hazard':{k:v.detach().cpu() for k,v in champion['hazard'].state_dict().items()}},'features':v1.FEATURES,'sequenceLength':v1.SEQ},cp)
    print(json.dumps({'acceptedUpdates':accepted,'rejectedUpdates':len(adapt_ids)-accepted,'baselineFinalComposite':baseline_final['compositeAuc'],'finalFinalComposite':final_final['compositeAuc'],'delta':final_final['compositeAuc']-baseline_final['compositeAuc'],'out':str(out)},indent=2),flush=True)

if __name__=='__main__':
    main()
