#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§三 指标分组统计（全局 / 高危区域 / 盲区终端）。

只读现有 runs/*/access_trace.csv 与 manifest.json，**不改动任何 sim/ 代码**。
- 全局：全部 ACCESS 事件。
- 高危区域：按规范定义「同时刻接入请求数 > 单星容量 80%」——以
  (floor(t_s), serving_sat) 为时间-卫星分箱，容量取该 run manifest 的
  rach_capacity；落入超阈分箱的事件记为高危。
- 盲区终端：规范按 elevation_deg < 25 判定；本工程 Python 轨未在 trace 中
  持久化逐终端仰角，故按《规范》§二「缺失值规则」标 null，并在 README 说明。

输出：results/subgroup_metrics.json（供台账与前端消费）。
"""
import json
import csv
import glob
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "sim" / "runs"
OUT = ROOT / "results" / "subgroup_metrics.json"


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def scenario_from_dir(dname):
    """从目录名提取 scenario_key。

    目录格式 <scenario_key>_s<seed>[_ns3]_<ts>，scenario_key 自身可能含下划线
    （如 wenchuan_storm2）。用 lazy + 数字约束的 regex，避免把
    `wenchuan_storm2_s...` 错拆成 `wenchuan`。优先以 manifest.scenario_key 为准。
    """
    import re
    m = re.match(r"^(.*?)_s\d+", dname)
    return m.group(1) if m else dname


def access_events(trace_path):
    out = []
    with open(trace_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "ACCESS":
                continue
            try:
                out.append({
                    "t_s": float(row["t_s"]),
                    "sat": row["serving_sat"],
                    "result": row["result"],
                    "value_ms": float(row["value_ms"]),
                    "forged": int(row.get("forged", "0") or 0),
                })
            except (ValueError, KeyError):
                continue
    return out


def summarize(events):
    """§三 接入实验指标：平均/P95时延、成功率、RACH吞吐、伪造拦截率、认证开销。"""
    succ = [e for e in events if e["result"] == "success"]
    fails = [e for e in events if e["result"] != "success"]
    lat = [e["value_ms"] for e in succ if e["value_ms"] > 0]
    total = len(events)
    forged = [e for e in events if e["forged"] == 1]
    forged_blocked = [e for e in forged if e["result"] != "success"]
    if lat:
        p95 = sorted(lat)[max(0, min(len(lat) - 1, int(math.ceil(0.95 * len(lat))) - 1))]
    else:
        p95 = None
    ts = [e["t_s"] for e in events]
    span = (max(ts) - min(ts)) if ts else 0
    return {
        "接入事件数": total,
        "接入成功率": round(len(succ) / total, 4) if total else None,
        "接入时延均值_ms": round(statistics.mean(lat), 3) if lat else None,
        "接入时延P95_ms": round(p95, 3) if p95 is not None else None,
        "RACH吞吐_成功每秒": round(len(succ) / span, 4) if span > 0 else None,
        "伪造终端数": len(forged),
        "伪造终端拦截率": round(len(forged_blocked) / len(forged), 4) if forged else None,
        "认证开销_ms": None,  # 逐事件认证开销未落盘，标 null（README 说明）
    }


def main():
    # 收集各场景的全部 run trace
    scen_traces = defaultdict(list)
    for mp in sorted(RUNS.glob("*/access_trace.csv")):
        dname = mp.parent.name
        m = load_json(mp.parent / "manifest.json") or {}
        scen = m.get("scenario_key") or scenario_from_dir(dname)
        plat = "ns3" if "_ns3_" in dname else "python"
        m = load_json(mp.parent / "manifest.json") or {}
        cap = (m.get("scenario_config") or {}).get("rach_capacity", 64)
        scen_traces[scen].append((mp, plat, cap))

    result = {"_meta": {
        "spec": "§三 指标按模块保存（全局/高危区域/盲区终端）",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "high_risk_def": "同时刻(floor t_s)接入请求数 > 单星容量(rach_capacity)80%",
        "blind_zone_def": "elevation_deg < 25；本工程未持久化逐终端仰角→null",
        "note": "仅统计 ACCESS 事件；时延仅计成功终端(value_ms>0)。",
    }, "scenarios": {}}

    for scen in sorted(scen_traces):
        all_ev = []
        for tp, plat, cap in scen_traces[scen]:
            evs = access_events(tp)
            # 高危分箱
            bin_count = defaultdict(int)
            for e in evs:
                if e["sat"] not in ("-1", "", None):
                    bin_count[(math.floor(e["t_s"]), e["sat"])] += 1
            thr = 0.8 * cap
            for e in evs:
                e["_hr"] = (e["sat"] not in ("-1", "", None)
                            and bin_count[(math.floor(e["t_s"]), e["sat"])] > thr)
            all_ev.extend(evs)
        hr_ev = [e for e in all_ev if e.get("_hr")]
        result["scenarios"][scen] = {
            "global": summarize(all_ev),
            "high_risk_by_load": summarize(hr_ev),
            "blind_zone": None,
            "_counts": {
                "runs": len(scen_traces[scen]),
                "total_access": len(all_ev),
                "high_risk_access": len(hr_ev),
            },
        }

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                   encoding="utf-8", newline="\n")
    print(f"[subgroups] 场景数={len(result['scenarios'])} -> {OUT}")
    for s, v in result["scenarios"].items():
        c = v["_counts"]
        print(f"  {s:24s} runs={c['runs']:3d} access={c['total_access']:5d} "
              f"high_risk={c['high_risk_access']:4d} "
              f"global_succ={v['global']['接入成功率']}")


if __name__ == "__main__":
    main()
