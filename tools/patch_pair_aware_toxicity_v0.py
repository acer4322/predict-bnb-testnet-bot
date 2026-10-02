from pathlib import Path
p=Path('tools/hftbacktest_r2_inventory_manifold_pair_aware_toxicity_v0.py')
s=p.read_text(encoding='utf-8')
s=s.replace("    def toxicity_step(now:int):\n",'''    def pair_completion_value(side:str, quote_px:float, remaining_qty:float):
        # Natural economic boundary: if the whole remaining child would reduce an
        # already-owned opposite residual, measure the locked binary pair value
        # from ACTUAL historical fills only. No winner/future information.
        net=actual_net()
        recovery=(net>EPS and side=='DOWN') or (net<-EPS and side=='UP')
        residual=abs(net)
        if (not recovery) or residual+EPS < float(remaining_qty):
            return None
        other='UP' if side=='DOWN' else 'DOWN'
        need=float(remaining_qty); cc=cs=0.0
        for ev in reversed(fill_log):
            if ev['side']!=other or need<=EPS: continue
            q=min(need,float(ev['shares']))
            feeps=float(ev.get('fee') or 0.0)/max(float(ev['shares']),EPS)
            cc+=q*(float(ev['price'])+feeps); cs+=q; need-=q
        if cs+EPS < float(remaining_qty):
            return None
        opposite_avg=cc/cs
        locked_edge=1.0-opposite_avg-float(quote_px)
        return {'oppositeAvgCost':opposite_avg,'lockedPairEdgePerShare':locked_edge,'pairedShares':float(remaining_qty)}

    def toxicity_step(now:int):
''')
old="""            pred=float(tox_model.predict(frame)[0])\n            if pred < 0.0:\n                cur=a.bt.orders(0).get(int(num))\n"""
new="""            pred=float(tox_model.predict(frame)[0])
            om=maker_meta[int(num)]
            leaves=os.get('leavesQty'); leaves=float(leaves if leaves is not None else max(0.0,float(om['qty'])-float(os.get('cumExecQty') or 0.0)))
            pairv=pair_completion_value(side,float(om['price']),leaves) if leaves>EPS else None
            # If this entire child locks a non-negative completed pair, its
            # settlement payoff is already fixed; short-horizon markout alone
            # must not push the recovery liquidity away.
            pair_protected=bool(pairv is not None and float(pairv['lockedPairEdgePerShare'])>=0.0)
            if pair_protected and pred < 0.0:
                toxicity_events.append({'atMs':now,'side':side,'action':'PAIR_VALUE_PROTECT_KEEP','orderNum':int(num),'predictedMarkout1sTicks':pred,'targetRevision':int(target_revision[side]),**pairv})
                continue
            if pred < 0.0:
                cur=a.bt.orders(0).get(int(num))
"""
if old not in s: raise SystemExit('target block not found')
s=s.replace(old,new)
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'","'version':'R2_PAIR_AWARE_TOXICITY_V0'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'","'version':'R2_PAIR_AWARE_TOXICITY_V0_REPORT'")
s=s.replace("Queue-preserving execution experiment: at most one toxicity-driven cancel/reprice per Frozen target revision; terminal ACK required before reinsert.","Pair-aware queue-preserving execution experiment: protects economically non-negative full pair-completion children from short-horizon toxicity shading; otherwise preserves V2 one-shot cancel/reprice and terminal-ACK invariant.")
p.write_text(s,encoding='utf-8')
print('patched',p)
