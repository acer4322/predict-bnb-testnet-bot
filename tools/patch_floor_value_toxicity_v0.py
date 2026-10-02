from pathlib import Path
p=Path('tools/hftbacktest_r2_floor_value_toxicity_v0.py')
s=p.read_text(encoding='utf-8')
anchor="""    def toxicity_step(now:int):
"""
ins="""    def marginal_floor_value(side:str, price:float, qty:float):
        # Exact, winner-free terminal payoff floor change from an ACTUAL maker
        # fill candidate. This values inventory-risk relief in USDT/share units.
        cu=actual.maker_up+actual.taker_up
        cd=actual.maker_down+actual.taker_down
        cash=float(actual.cash)
        before=min(cash+cu,cash+cd)
        cash2=cash-float(price)*float(qty)
        if side=='UP': cu2=cu+float(qty); cd2=cd
        else: cu2=cu; cd2=cd+float(qty)
        after=min(cash2+cu2,cash2+cd2)
        return float(after-before)

    def toxicity_step(now:int):
"""
if anchor not in s: raise SystemExit('anchor missing')
s=s.replace(anchor,ins,1)
old="""            pred=float(tox_model.predict(frame)[0])
            if pred < 0.0:
                cur=a.bt.orders(0).get(int(num))
"""
new="""            pred=float(tox_model.predict(frame)[0])
            om=maker_meta[int(num)]
            leaves=os.get('leavesQty'); leaves=float(leaves if leaves is not None else max(0.0,float(om['qty'])-float(os.get('cumExecQty') or 0.0)))
            floor_delta=marginal_floor_value(side,float(om['price']),leaves) if leaves>EPS else 0.0
            markout_value=float(pred)*float(mod.GRID)*leaves
            joint_value=float(floor_delta+markout_value)
            # Natural common-unit economic boundary. Keep a short-horizon toxic
            # fill if its exact improvement to the portfolio's worst terminal
            # payoff compensates the predicted markout loss; otherwise V2 shade.
            if pred < 0.0 and joint_value >= 0.0:
                toxicity_events.append({'atMs':now,'side':side,'action':'FLOOR_VALUE_TOXIC_KEEP','orderNum':int(num),'predictedMarkout1sTicks':pred,'marginalFloorValueUsdt':floor_delta,'predictedMarkoutValueUsdt':markout_value,'jointValueUsdt':joint_value,'targetRevision':int(target_revision[side])})
                continue
            if pred < 0.0 and joint_value < 0.0:
                cur=a.bt.orders(0).get(int(num))
"""
if old not in s: raise SystemExit('decision missing')
s=s.replace(old,new,1)
s=s.replace("'predictedMarkout1sTicks':pred,'targetRevision':int(target_revision[side])", "'predictedMarkout1sTicks':pred,'marginalFloorValueUsdt':floor_delta,'predictedMarkoutValueUsdt':markout_value,'jointValueUsdt':joint_value,'targetRevision':int(target_revision[side])",1)
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2'", "'version':'R2_FLOOR_VALUE_TOXICITY_V0'")
s=s.replace("'version':'R2_ONE_SHOT_TOXICITY_SHADING_V2_REPORT'", "'version':'R2_FLOOR_VALUE_TOXICITY_V0_REPORT'")
p.write_text(s,encoding='utf-8')
print('patched',p)
