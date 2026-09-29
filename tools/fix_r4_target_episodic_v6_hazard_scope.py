from pathlib import Path
p=Path('tools/worker_r4_target_episodic_class_balanced_replay_v6.py')
s=p.read_text(encoding='utf-8')
old="        hi=v3.sample_mix(rng,new_h,rep_h,anchor_h,128,64,64)\n"
new="""        def _pick(pool,n):
            return np.zeros(0,np.int64) if n<=0 or len(pool)==0 else rng.choice(pool,size=n,replace=len(pool)<n).astype(np.int64)
        hi=np.concatenate([_pick(new_h,128),_pick(rep_h,64),_pick(anchor_h,64)])
        rng.shuffle(hi)
"""
if old not in s: raise SystemExit('target line not found')
p.write_text(s.replace(old,new),encoding='utf-8')
print(p)
