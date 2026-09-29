from pathlib import Path
p=Path('tools/hftbacktest_r2_queue_value_guarded_toxicity_v0.py')
s=p.read_text(encoding='utf-8')
# Load the already-trained strict-past two-head queue optionality bundle.
old="tox_art=joblib.load(TOX_MODEL_PATH); tox_model=tox_art['models']['markout1s']; tox_features=list(tox_art['features'])"
new="tox_art=joblib.load(TOX_MODEL_PATH); tox_model=tox_art['models']['markout1s']; tox_features=list(tox_art['features']); queue_art=joblib.load(OUT/'r2_queue_option_value_v0.joblib'); queue_useful_model=queue_art['usefulModel']; queue_toxic_model=queue_art['toxicModel']"
if old not in s: raise SystemExit('model block missing')
s=s.replace(old,new,1)
old2="""            pred=float(tox_model.predict(frame)[0])
            if pred < 0.0:
                cur=a.bt.orders(0).get(int(num))
"""
new2="""            pred=float(tox_model.predict(frame)[0])
            p_queue_useful=float(queue_useful_model.predict_proba(frame)[0,1])
            p_queue_toxic=float(queue_toxic_model.predict_proba(frame)[0,1])
            # Queue-aware natural comparison: abandon the existing queue only
            # when the working order is short-horizon adverse AND its modeled
            # toxic-fill use is more likely than its useful-fill use.
            if pred < 0.0 and p_queue_toxic <= p_queue_useful:
                toxicity_events.append({'atMs':now,'side':side,'action':'QUEUE_VALUE_KEEP','orderNum':int(num),'predictedMarkout1sTicks':pred,'pQueueUseful':p_queue_useful,'pQueueToxic':p_queue_toxic,'targetRevision':int(target_revision[side])})
                continue
            if pred < 0.0 and p_queue_toxic > p_queue_useful:
                cur=a.bt.orders(0).get(int(num))
"""
if old2 not in s: raise SystemExit('decision block missing')
s=s.replace(old2,new2,1)
s=s.replace("'predictedMarkout1sTicks':pred,'targetRevision':int(target_revision[side])", "'predictedMarkout1sTicks':pred,'pQueueUseful':p_queue_useful,'pQueueToxic':p_queue_toxic,'targetRevision':int(target_revision[side])",1)
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'", "'version':'R2_QUEUE_VALUE_GUARDED_TOXICITY_V0'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'", "'version':'R2_QUEUE_VALUE_GUARDED_TOXICITY_V0_REPORT'")
p.write_text(s,encoding='utf-8')
print('patched',p)
