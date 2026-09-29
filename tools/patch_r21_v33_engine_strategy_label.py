from pathlib import Path
p=Path('src/predict_bot/echtgeld_engine_v23.py')
s=p.read_text(encoding='utf-8')
s=s.replace('R2_R21_STRATEGY = "R2_R21_SEMANTIC_COOPERATION_V1"','R2_R21_STRATEGY = "R2_R21_V33_INFORMATION_ONLY_10SHARE_NO_NOTIONAL_CAP"')
p.write_text(s,encoding='utf-8')
print('patched engine strategy label')