from pathlib import Path
p=Path('tools/hftbacktest_r2_execution_school_v0.py')
s=p.read_text(encoding='utf-8')
# Make per-call cap local inside taker wrapper.
old='''    def taker_wrap(side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:\n        nonlocal post_add_ep\n        if disable_taker:'''
new='''    def taker_wrap(side: str, price: float, now: int, decision_id: str, snapshot: dict[str, Any], raw: dict[str, Any], p1: float, p3: float, ppass: float, pred_effect: str) -> bool:\n        nonlocal post_add_ep\n        maker_first_residual_cap = None\n        if disable_taker:'''
assert old in s
s=s.replace(old,new,1)
# Young carrier should not suppress active fallback in V2.
old='''                    if age < int(maker_first_wait_ms):\n                        ev["result"]="YOUNG_CARRIER_CONTINUE"; maker_first_handoff_events.append(ev); return False'''
new='''                    if age < int(maker_first_wait_ms):\n                        ev["result"]="YOUNG_CARRIER_HANDOFF_TAKER"; maker_first_handoff_events.append(ev)'''
assert old in s
s=s.replace(old,new,1)
# Need wrap following near-touch/else so young doesn't continue into cancellation. Convert if->elif.
old='''                    # If the carrier is already at/inside the target 1T zone and still stalled, passive reachability\n                    # has already been tested. Hand off to Taker rather than resetting queue priority.\n                    if math.isfinite(off) and off <= 1.5:'''
new='''                    # If the carrier is already at/inside the target 1T zone and still stalled, passive reachability\n                    # has already been tested. Hand off to Taker rather than resetting queue priority.\n                    elif math.isfinite(off) and off <= 1.5:'''
assert old in s
s=s.replace(old,new,1)
# Replace progress suppression logic with residual reconciliation.
old='''                                        ev.update({"result":"PROGRESS_CONTINUE" if progress>EPS else ("RESOLVED_CONTINUE" if not still else "NO_PROGRESS_HANDOFF_TAKER"),"newOrderId":oid,"newPrice":float(px),"newQty":float(rem),"progressShares":float(progress),"postGap":abs(postnet),"progressWindowEndMs":target})\n                                        maker_first_handoff_events.append(ev)\n                                        if progress>EPS or not still: return False\n                                        # Handoff occurs at the end of the bounded Maker progress window using current public ask.\n                                        now=target; bf1=mod.outcome_book(a.book,None) or {}; fresh=bf1.get("up_ask") if side=="UP" else bf1.get("down_ask")\n                                        if fresh is not None and math.isfinite(float(fresh)): price=float(fresh)'''
new='''                                        weak_now = "DOWN" if postnet>EPS else "UP" if postnet<-EPS else None\n                                        ev.update({"result":"PROGRESS_RESIZE_HANDOFF" if progress>EPS and still else ("RESOLVED_CONTINUE" if not still else "NO_PROGRESS_HANDOFF_TAKER"),"newOrderId":oid,"newPrice":float(px),"newQty":float(rem),"progressShares":float(progress),"postGap":abs(postnet),"weakSideNow":weak_now,"progressWindowEndMs":target})\n                                        maker_first_handoff_events.append(ev)\n                                        if not still or weak_now != side:\n                                            # Execution revision changed enough that the original Repair intent is obsolete.\n                                            ev["result"] = "RESOLVED_CONTINUE" if weak_now is None else "REPAIR_SIDE_CHANGED_REDECIDE"\n                                            return False\n                                        # Preserve active fallback, but size it from the latest authoritative residual obligation.\n                                        maker_first_residual_cap = abs(postnet)\n                                        now=target; bf1=mod.outcome_book(a.book,None) or {}; fresh=bf1.get("up_ask") if side=="UP" else bf1.get("down_ask")\n                                        if fresh is not None and math.isfinite(float(fresh)): price=float(fresh)'''
assert old in s
s=s.replace(old,new,1)
# Add residual cap after r3 rawq/other sizing, just before submit. Find target-size block end => num, rc.
needle='''        num, rc = a.submit_taker(side, max_price, taker_qty)'''
repl='''        if maker_first_residual_cap is not None:\n            # Anti-overshoot is latest Repair obligation, never a fixed 18-share ceiling.\n            taker_qty = min(float(taker_qty), max(0.0, float(maker_first_residual_cap)))\n            if taker_qty <= EPS:\n                maker_first_handoff_events.append({"atMs":int(now),"side":side,"mode":str(maker_first_repair_mode),"result":"ZERO_RESIDUAL_NO_TAKER","residualCap":float(maker_first_residual_cap)})\n                return False\n        max_price = min(0.99, float(price) + 0.02)\n        num, rc = a.submit_taker(side, max_price, taker_qty)'''
assert needle in s
s=s.replace(needle,repl,1)
# Config semantic version marker.
old='''            "makerFirstRepairMode": str(maker_first_repair_mode),'''
new='''            "makerFirstRepairMode": str(maker_first_repair_mode),\n            "makerFirstRepairSemantics": "V2_LATEST_RESIDUAL_RECONCILE",'''
assert old in s
s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')

# Maker10 adapter: expose the optional Maker-first research args while preserving r3_rawq.
p=Path('tools/hftbacktest_r3_r31_maker10_adapter_v1.py');s=p.read_text(encoding='utf-8')
old='''def run_market(mid:int):''';new='''def run_market(mid:int, maker_first_repair_mode:str="NONE", maker_first_wait_ms:int=2200):'''
assert old in s;s=s.replace(old,new,1)
old='''        rep=r3ctl.run_market(int(mid),True)''';new='''        rep=r3ctl.run_market(int(mid),True,maker_first_repair_mode=str(maker_first_repair_mode),maker_first_wait_ms=int(maker_first_wait_ms))'''
assert old in s;s=s.replace(old,new,1)
p.write_text(s,encoding='utf-8')
print('patched v2')
