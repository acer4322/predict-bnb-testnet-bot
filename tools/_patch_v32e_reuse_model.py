from pathlib import Path
p=Path('tools/audit_target_eth_repair_taker_placement_failure_escalation_v1.py')
s=p.read_text(encoding='utf-8')
needle='def train_portable():\n    z=np.load(DS);meta=json.load(open(META,encoding=\'utf-8\'));F=list(meta[\'features\']);ix={k:i for i,k in enumerate(F)}\n'
repl="def train_portable():\n    if MODEL.exists():\n        d=joblib.load(MODEL)\n        if isinstance(d,dict) and d.get('version')=='TARGET_ETH_PORTABLE_PLACEMENT_TEACHER_V1' and d.get('oldTest'):\n            return d['oldTest'],d['features'],d['hazardModel'],d['weakModel'],d['priceOffsetModel'],float(d['hazardThreshold'])\n    z=np.load(DS);meta=json.load(open(META,encoding='utf-8'));F=list(meta['features']);ix={k:i for i,k in enumerate(F)}\n"
if needle not in s:
    raise SystemExit('needle not found')
s=s.replace(needle,repl,1)
p.write_text(s,encoding='utf-8')
print('patched model reuse')
