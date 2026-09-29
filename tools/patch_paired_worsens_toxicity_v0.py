from pathlib import Path
p=Path('tools/hftbacktest_r2_paired_worsens_toxicity_v0.py')
s=p.read_text(encoding='utf-8')
old="tox_art=joblib.load(TOX_MODEL_PATH); tox_model=tox_art['models']['markout1s']; tox_features=list(tox_art['features'])"
new="tox_art=joblib.load(TOX_MODEL_PATH); tox_model=tox_art['models']['markout1s']; pair_model=tox_art['models']['paired_worsens']; tox_features=list(tox_art['features'])"
if old not in s: raise SystemExit('model load block missing')
s=s.replace(old,new,1)
old2="""            pred=float(tox_model.predict(frame)[0])
            if pred < 0.0:
                cur=a.bt.orders(0).get(int(num))
"""
new2="""            pred=float(tox_model.predict(frame)[0])
            p_pair_worsens=float(pair_model.predict_proba(frame)[0,1])
            # Natural joint boundary: short-horizon adverse markout AND the
            # fill is more likely than not to worsen reconstructed Maker pairing.
            # A negative-markout fill that is expected to improve pairing keeps
            # its existing queue; no cancellation/reprice is introduced there.
            if pred < 0.0 and p_pair_worsens <= 0.5:
                toxicity_events.append({'atMs':now,'side':side,'action':'PAIR_IMPROVING_TOXIC_KEEP','orderNum':int(num),'predictedMarkout1sTicks':pred,'pPairedWorsens':p_pair_worsens,'targetRevision':int(target_revision[side])})
                continue
            if pred < 0.0 and p_pair_worsens > 0.5:
                cur=a.bt.orders(0).get(int(num))
"""
if old2 not in s: raise SystemExit('toxicity decision block missing')
s=s.replace(old2,new2,1)
# Add pair probability to cancellation diagnostics as well.
s=s.replace("'predictedMarkout1sTicks':pred,'targetRevision':int(target_revision[side])", "'predictedMarkout1sTicks':pred,'pPairedWorsens':p_pair_worsens,'targetRevision':int(target_revision[side])",1)
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'", "'version':'R2_PAIRED_WORSENS_TOXICITY_V0'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'", "'version':'R2_PAIRED_WORSENS_TOXICITY_V0_REPORT'")
p.write_text(s,encoding='utf-8')
print('patched',p)
