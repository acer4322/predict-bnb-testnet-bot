"""One-time scoped fixes on new V0 files only; preserve all research originals."""
from pathlib import Path
p=Path(__file__).resolve().parent
f=p/'static/app.js';s=f.read_text(encoding='utf-8')
s=s.replace("function invalidate(){result=null;", "function invalidate(){$('actions').querySelectorAll('button').forEach(b=>b.disabled=true);$('stop').disabled=true;$('undo').disabled=true;result=null;")
s=s.replace("function compare(){guarded(async()=>{result=await", "function compare(){guarded(async()=>{paintState(await api('/api/state',body()));result=await")
s=s.replace('研究服务','研究服務').replace('已切换','已切換')
f.write_text(s,encoding='utf-8',newline='\n')
f=p/'server.py';s=f.read_text(encoding='utf-8').replace(".open('x',encoding='utf-8')", ".open('x',encoding='utf-8',newline='\\n')")
f.write_text(s,encoding='utf-8',newline='\n')
f=p/'test_playground.py';s=f.read_text(encoding='utf-8').replace("self.assertTrue((core.ROOT/z['path']).is_file())", "self.assertTrue((core.ROOT/z['path']).is_file())\n                self.assertEqual(core.sha(core.ROOT/z['path']),z['sha256'])")
f.write_text(s,encoding='utf-8',newline='\n')
print('SCOPED_UI_STALENESS_AND_EXPORT_HASH_FIX_APPLIED')
