from pathlib import Path
p=Path('tools/hftbacktest_r2_one_shot_toxicity_opposite_priority_v0.py')
s=p.read_text(encoding='utf-8')
s=s.replace("toxicity_events=[]", "toxicity_events=[]; priority_recovery_side=None; priority_events=[]")
needle="""    def submit_maker(side:str,now:int):
        br=toxicity_block_revision.get(side)
"""
repl="""    def submit_maker(side:str,now:int):
        br=toxicity_block_revision.get(side)
        if priority_recovery_side is not None and side != priority_recovery_side:
            priority_events.append({'atMs':now,'action':'SUPPRESS_NEW_SURPLUS_CHILD','side':side,'recoverySide':priority_recovery_side,'trackingError':actual_net()-target_net()})
            return
"""
if needle not in s: raise SystemExit('submit needle missing')
s=s.replace(needle,repl,1)
anchor="""    def toxicity_step(now:int):
"""
ins="""    def update_opposite_priority(now:int):
        nonlocal priority_recovery_side
        err=actual_net()-target_net()
        needed='DOWN' if err>EPS else 'UP' if err<-EPS else None
        if priority_recovery_side is not None and (needed is None or needed != priority_recovery_side or abs(err)<CHUNK-EPS):
            priority_events.append({'atMs':now,'action':'RELEASE_OPPOSITE_PRIORITY','recoverySide':priority_recovery_side,'trackingError':err})
            priority_recovery_side=None
        if priority_recovery_side is None and needed is not None and abs(err)>=CHUNK-EPS:
            priority_recovery_side=needed
            priority_events.append({'atMs':now,'action':'ACTIVATE_OPPOSITE_PRIORITY','recoverySide':needed,'trackingError':err})

    def toxicity_step(now:int):
"""
if anchor not in s: raise SystemExit('tox anchor missing')
s=s.replace(anchor,ins,1)
call="area(t);harvest(t);service_toxicity(t);service_replace(t);c._step(dict(s))"
if call not in s: raise SystemExit('step call missing')
s=s.replace(call,"area(t);harvest(t);service_toxicity(t);service_replace(t);update_opposite_priority(t);c._step(dict(s))")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'","'version':'R2_TOXICITY_OPPOSITE_PRIORITY_V0'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'","'version':'R2_TOXICITY_OPPOSITE_PRIORITY_V0_REPORT'")
needle2="'toxicityEvents':toxicity_events"
if needle2 in s: s=s.replace(needle2,"'toxicityEvents':toxicity_events,'oppositePriorityEvents':priority_events",1)
p.write_text(s,encoding='utf-8')
print('patched',p)
