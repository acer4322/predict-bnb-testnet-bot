from pathlib import Path
p=Path('dashboard-v2/public/echtgeld.html')
s=p.read_text(encoding='utf-8')
old='''            <option value="R2_R21_8789">8789 · R2 + R2.1 V3.5.2 Autonomous Reassess + Audit Fix</option>'''
new=old+'''\n            <option value="R3S_R31_8790">8790 · R3-S + R3.1 V1 · Dynamic Taker</option>'''
if 'R3S_R31_8790' not in s:
    s=s.replace(old,new)
s=s.replace("$('sizingPolicy').textContent=selectedSource==='R2_R21_8789'?'R2 + R2.1 V3.5.2 Autonomous Reassess + Audit Fix sizing：每張固定 10 shares、沒有 strategy notional cap；上方 Notional USDT 不限制此來源。':'此來源使用上方 Notional USDT 設定。';",
"$('sizingPolicy').textContent=selectedSource==='R3S_R31_8790'?'R3-S + R3.1 V1 sizing：Maker 每筆固定 10 shares；Taker 使用 R3 dynamic sizing，沒有固定 18-share cap；上方 Notional USDT 不限制此來源。':selectedSource==='R2_R21_8789'?'R2 + R2.1 V3.5.2 Autonomous Reassess + Audit Fix sizing：每張固定 10 shares、沒有 strategy notional cap；上方 Notional USDT 不限制此來源。':'此來源使用上方 Notional USDT 設定。';")
s=s.replace("const sizing=source==='R2_R21_8789'?'每單固定 10 shares；無 strategy notional cap':`Notional: ${$('settingNotional').value} USDT`;",
"const sizing=source==='R3S_R31_8790'?'Maker 每筆固定 10 shares；Taker R3 dynamic sizing；無固定 18-share cap；無 strategy notional cap':source==='R2_R21_8789'?'每單固定 10 shares；無 strategy notional cap':`Notional: ${$('settingNotional').value} USDT`;")
p.write_text(s,encoding='utf-8')
print('R3 option count',s.count('R3S_R31_8790'))
