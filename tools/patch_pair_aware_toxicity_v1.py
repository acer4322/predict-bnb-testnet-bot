from pathlib import Path
p=Path('tools/hftbacktest_r2_inventory_manifold_pair_aware_toxicity_v1.py')
s=p.read_text(encoding='utf-8')
s=s.replace("    def toxicity_step(now:int):\n",'''    def tracking_recovery_value(side:str, quote_px:float, remaining_qty:float):
        # Protect only fills that reduce execution tracking error toward the
        # Frozen Strategy target_net. Do NOT neutralize intentional directional
        # inventory chosen by Strategy Brain.
        err=actual_net()-target_net()
        recovery=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP')
        if not recovery or abs(err)+EPS < float(remaining_qty):
            return None
        other='UP' if side=='DOWN' else 'DOWN'
        need=float(remaining_qty); cc=cs=0.0
        for ev in reversed(fill_log):
            if ev['side']!=other or need<=EPS: continue
            q=min(need,float(ev['shares']))
            feeps=float(ev.get('fee') or 0.0)/max(float(ev['shares']),EPS)
            cc+=q*(float(ev['price'])+feeps); cs+=q; need-=q
        if cs+EPS < float(remaining_qty): return None
        opposite_avg=cc/cs
        locked_edge=1.0-opposite_avg-float(quote_px)
        return {'oppositeAvgCost':opposite_avg,'lockedPairEdgePerShare':locked_edge,'pairedShares':float(remaining_qty),'trackingErrorBefore':float(err)}

    def toxicity_step(now:int):
''')
old="""            pred=float(tox_model.predict(frame)[0])\n            if pred < 0.0:\n                cur=a.bt.orders(0).get(int(num))\n"""
new="""            pred=float(tox_model.predict(frame)[0])
            om=maker_meta[int(num)]
            leaves=os.get('leavesQty'); leaves=float(leaves if leaves is not None else max(0.0,float(om['qty'])-float(os.get('cumExecQty') or 0.0)))
            pairv=tracking_recovery_value(side,float(om['price']),leaves) if leaves>EPS else None
            pair_protected=bool(pairv is not None and float(pairv['lockedPairEdgePerShare'])>=0.0)
            if pair_protected and pred < 0.0:
                toxicity_events.append({'atMs':now,'side':side,'action':'TRACKING_PAIR_PROTECT_KEEP','orderNum':int(num),'predictedMarkout1sTicks':pred,'targetRevision':int(target_revision[side]),**pairv})
                continue
            if pred < 0.0:
                cur=a.bt.orders(0).get(int(num))
"""
if old not in s: raise SystemExit('target block not found')
s=s.replace(old,new)
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'","'version':'R2_PAIR_AWARE_TOXICITY_V1'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'","'version':'R2_PAIR_AWARE_TOXICITY_V1_REPORT'")
s=s.replace("Queue-preserving execution experiment: at most one toxicity-driven cancel/reprice per Frozen target revision; terminal ACK required before reinsert.","Target-relative pair-aware execution experiment: toxicity protection is allowed only for fills that reduce actual_net toward Frozen target_net without overshoot and lock non-negative pair edge; otherwise V2 one-shot toxicity shading is unchanged.")
p.write_text(s,encoding='utf-8')
print('patched',p)
