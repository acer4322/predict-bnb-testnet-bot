from pathlib import Path
p=Path('src/predict_bot/predict_fun_observer.py')
s=p.read_text(encoding='utf-8')
old='''    return {\n        "marketId": _positive_int(data.get("marketId")),\n        "updateTimestampMs": _positive_int(data.get("updateTimestampMs")),\n        "orderCount": _positive_int(data.get("orderCount")) or 0,\n        "upBid": up_bid,'''
new='''    settlements_pending = _record(data.get("settlementsPending"))\n    return {\n        "marketId": _positive_int(data.get("marketId")),\n        "updateTimestampMs": _positive_int(data.get("updateTimestampMs")),\n        "orderCount": _positive_int(data.get("orderCount")) or 0,\n        "lastOrderSettled": _record(data.get("lastOrderSettled")) or None,\n        "settlementsPending": {\n            "asks": settlements_pending.get("asks") if isinstance(settlements_pending.get("asks"), list) else [],\n            "bids": settlements_pending.get("bids") if isinstance(settlements_pending.get("bids"), list) else [],\n        },\n        "upBid": up_bid,'''
if old not in s: raise SystemExit('parse block missing')
s=s.replace(old,new)
s=s.replace('''                            "sourceTimestampMs": None, "receivedTimestampMs": None,\n                            "orderCount": 0,\n                        }''','''                            "sourceTimestampMs": None, "receivedTimestampMs": None,\n                            "orderCount": 0, "lastOrderSettled": None,\n                            "settlementsPending": {"asks": [], "bids": []},\n                        }''')
old='''                    "orderCount": book.get("orderCount"),\n                    "trajectory": list(state["trajectory"]),'''
new='''                    "orderCount": book.get("orderCount"),\n                    "lastOrderSettled": book.get("lastOrderSettled"),\n                    "settlementsPending": book.get("settlementsPending"),\n                    "trajectory": list(state["trajectory"]),'''
if old not in s: raise SystemExit('snapshot block missing')
s=s.replace(old,new)
p.write_text(s,encoding='utf-8')
print('patched',p)
