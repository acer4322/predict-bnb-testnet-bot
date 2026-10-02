from __future__ import annotations
import json,time,psutil
ps=[]
for p in psutil.process_iter(["pid","ppid","name","create_time","cmdline"]):
    try:
        if "python" in (p.info.get("name") or "").lower():
            p.cpu_percent(None); ps.append(p)
    except Exception:
        pass
time.sleep(1.0)
rows=[]
now=time.time()
for p in ps:
    try:
        i=p.as_dict(attrs=["pid","ppid","name","create_time","cmdline","memory_info"]); rows.append({"pid":i["pid"],"ppid":i["ppid"],"age_sec":round(now-i["create_time"],1),"cpu_pct":p.cpu_percent(None),"rss_mb":round(i["memory_info"].rss/1048576,1),"cmdline":i.get("cmdline") or []})
    except Exception:
        pass
print(json.dumps({"pythonCount":len(rows),"rows":sorted(rows,key=lambda x:x["cpu_pct"],reverse=True)},ensure_ascii=False))
