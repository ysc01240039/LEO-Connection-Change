"""从 logs/refresh/sens_*.log 解析「扫描参数值 ↔ run 目录」映射，重建 results/敏感性分析_20260903.json。

映射来源：run_sim.py 的「参数覆盖: {...}」行 + 「[5/6] 写接口 -> <rundir>」行。
"""
import json, glob, os, re, io
from datetime import datetime, timezone

PARAMS = {"ho_lead": "ho_lead_s", "ephem_err": "ephem_err_s", "compromised": "compromised_share"}
sweeps = {v: [] for v in PARAMS.values()}

for f in sorted(glob.glob("logs/refresh/sens_*.log")):
    txt = open(f, encoding="utf-8", errors="replace").read()
    m1 = re.search(r"参数覆盖:\s*\{([^}]*)\}", txt)
    m2 = re.search(r"写接口\s*->\s*(.+?)\s*$", txt, re.M)
    if not (m1 and m2):
        print(f"  !! 跳过 {os.path.basename(f)}（缺参数/目录）")
        continue
    ov = eval("{" + m1.group(1) + "}")  # 仅含数字/字符串，安全
    rundir = m2.group(1).strip().replace("\\", "/")
    named = [(PARAMS[k], v) for k, v in ov.items() if k in PARAMS]
    if not named:
        continue
    key, val = named[0]
    mp = os.path.join(rundir, "metrics.json")
    if not os.path.exists(mp):
        print(f"  !! 缺 metrics: {rundir}")
        continue
    met = json.load(open(mp, encoding="utf-8"))
    el = {"value": val}
    el.update(met)
    sweeps[key].append(el)
    print(f"  {key}={val}  <- {os.path.basename(rundir)}")

for k in sweeps:
    # 同值去重（保留最后写入）
    seen = {}
    for el in sweeps[k]:
        seen[el["value"]] = el
    sweeps[k] = [seen[v] for v in sorted(seen)]

print("\n点数：", {k: [e["value"] for e in v] for k, v in sweeps.items()})

old = json.load(open("results/敏感性分析_20260903.json", encoding="utf-8"))
out = dict(sweeps)
for k in ("platform", "commit", "commit_full", "scenario", "_archive"):
    if k in old:
        out[k] = old[k]
out["scenario"] = "sensitivity"
out["produced_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
with io.open("results/敏感性分析_20260903.json", "w", encoding="utf-8", newline="\n") as fh:
    json.dump(out, fh, ensure_ascii=False, indent=2)
print("已重建 results/敏感性分析_20260903.json")
