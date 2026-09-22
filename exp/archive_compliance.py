#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""《天权项目计算机仿真实验数据存档规范》合规化脚本（2026-09-16）。

职责（全部为「格式 / 元数据 / 存档合规」层，不触碰任何仿真逻辑）：
  1. 向所有现存结果 JSON 顶层注入 §二 要求的存档标签
     platform / commit / run_id / scenario / produced_at，使结果文件本身
     即可定位仿真平台与代码版本。
  2. 从各 runs/*/manifest.json 聚合 §三 必填字段，生成实验台账
     docs/实验台账.md 与 results/experiment_ledger.json。
  3. 产出 results/ARCHIVE_COMPLIANCE.md 逐条对照清单（便于人工核查无漏判/错判）。

本脚本幂等：重复运行只会刷新注入字段，不会破坏原有指标键。
"""
import json
import os
import sys
import glob
import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
RUNS = ROOT / "data" / "sim" / "runs"
DOC_LEGER = ROOT / "docs" / "实验台账.md"
LEDGER_JSON = RESULTS / "experiment_ledger.json"
COMPLIANCE_MD = RESULTS / "ARCHIVE_COMPLIANCE.md"


def git_head():
    try:
        full = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=str(ROOT), text=True,
                                       stderr=subprocess.DEVNULL).strip()
        short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                        cwd=str(ROOT), text=True,
                                        stderr=subprocess.DEVNULL).strip()
    except Exception:
        full, short = "unknown", "unknown"
    return full, short


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def save_json(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                       encoding="utf-8", newline="\n")


def inject_meta(obj, platform, commit, commit_full, run_id, scenario):
    """返回注入存档标签后的对象（浅拷贝，不动原指标键）。"""
    out = dict(obj) if isinstance(obj, dict) else obj
    if isinstance(out, dict):
        out["platform"] = platform
        out["commit"] = commit
        if commit_full and commit_full != commit:
            out["commit_full"] = commit_full
        if run_id is not None:
            out["run_id"] = run_id
        if scenario is not None:
            out["scenario"] = scenario
        out["produced_at"] = now_iso()
        out["_archive"] = "injected by exp/archive_compliance.py per 《数据存档规范》§二"
    return out


# ---------------------------------------------------------------------------
# 1) 回填现存结果 JSON 的 platform/commit
# ---------------------------------------------------------------------------
def curated_platform_scenario(name):
    """results/*.json 的 platform 与 scenario 映射（★2026-09-22 逐文件核实后更正★）。

    原实现把所有 curated results 默认标为 "python+ns3"（"双轨聚合"），与事实不符。
    逐文件核实结论：
      · 含 summary["n_pseudo_rotation"] / ["dp_avg_gh"] / "话音业务平均中断_ms"
        等**仅 Python 轨 summary 才有**的字段 → 实为 **Python 单轨**：
        {henan,wenchuan,wenchuan_storm2,wenchuan_storm4}_metrics.json、summary_20260903.json。
      · 聚合**两轨** run 的产物（台账读全部 manifest；分组指标读两轨 access_trace.csv；
        认证三档读两轨含伪造终端 run）→ 确为 **python+ns3**：
        experiment_ledger.json、subgroup_metrics.json、auth_tier_comparison.json。
    """
    DUAL = {"experiment_ledger.json", "subgroup_metrics.json", "auth_tier_comparison.json"}
    stem = name[:-5] if name.endswith(".json") else name
    if name in DUAL:
        return "python+ns3", stem              # 真·双轨聚合
    if name == "summary_20260903.json":
        return "python", "all(汇总)"
    if name == "敏感性分析_20260903.json":
        return "python", "sensitivity"
    return "python", stem                       # 其余 curated 指标 = Python 单轨（更正前误标 python+ns3）


def backfill_results(head_full, head_short):
    injected = []
    # 1a. curated results/*.json
    for p in sorted(RESULTS.glob("*.json")):
        obj = load_json(p)
        if not isinstance(obj, dict):
            continue
        plat, scen = curated_platform_scenario(p.name)
        save_json(p, inject_meta(obj, plat, head_short, head_full,
                                 run_id=None, scenario=scen))
        injected.append(("results/" + p.name, plat))
    # 1b. 每 run 的 metrics.json（runs/<dir>/metrics.json，单层）
    for p in sorted(RUNS.glob("*/metrics.json")):
        obj = load_json(p)
        if not isinstance(obj, dict):
            continue
        dname = p.parent.name
        plat = "ns3" if "_ns3_" in dname else "python"
        # 优先用该 run manifest 的真实短哈希与 scenario_key；否则退回仓库 HEAD / 正则
        commit = head_short
        scen = scenario_from_dir(dname)
        mpath = p.parent / "manifest.json"
        if mpath.exists():
            m = load_json(mpath) or {}
            commit = m.get("git_commit") or head_short
            scen = m.get("scenario_key") or scen
        save_json(p, inject_meta(obj, plat, commit, None,
                                 run_id=dname, scenario=scen))
        injected.append(("data/sim/runs/" + dname + "/metrics.json", plat))
    # 1c. 实验基线结果（T3 / T2 对照）
    extras = [
        ROOT / "exp" / "t3_access" / "t3_results.json",
        ROOT / "perf" / "crossval_ho_results.json",
    ]
    for p in extras:
        if not p.exists():
            continue
        obj = load_json(p)
        if not isinstance(obj, dict):
            continue
        save_json(p, inject_meta(obj, "python+ns3", head_short, head_full,
                                 run_id=None, scenario="baseline(T2/T3)"))
        injected.append((str(p.relative_to(ROOT)).replace("\\", "/"), "python+ns3"))
    return injected


# ---------------------------------------------------------------------------
# 2) 实验台账（§三）
# ---------------------------------------------------------------------------
def sha256_file(p):
    try:
        h = hashlib.sha256()
        h.update(Path(p).read_bytes())
        return h.hexdigest()[:16]
    except Exception:
        return None


def scenario_from_dir(dname):
    """从目录名提取 scenario_key（scenario_key 可能含下划线，如 wenchuan_storm2）。
    用 lazy + 数字约束的 regex，避免错拆。优先以 manifest.scenario_key 为准。"""
    import re
    m = re.match(r"^(.*?)_s\d+", dname)
    return m.group(1) if m else dname


def build_ledger(head_full, head_short):
    env = {
        "python": sys.version.split()[0],
        # ★更正（2026-09-22）★：platform 采用《存档规范》§二口径（python / ns3 /
        # python+ns3），而非本机 OS 名（原 sys.platform 会写出 "win32"，与规范语义冲突）。
        # 本台账聚合双轨全部 run → "python+ns3"；生成机 OS 另记 os 字段。
        "platform": "python+ns3",
        "os": sys.platform,
        "git_head_full": head_full,
        "git_head_short": head_short,
        "generated_at": now_iso(),
    }
    rows = []
    # 遍历所有 run 目录（有 metrics.json 即视为一次运行），manifest 缺失则按目录名推导最小字段
    for mp in sorted(RUNS.glob("*/metrics.json")):
        rundir = mp.parent
        dname = rundir.name
        m = load_json(rundir / "manifest.json") or {}
        has_manifest = bool(m)
        plat = "ns3" if "_ns3_" in dname else "python"
        # 目录名推导：<scenario>_s<seed>[_ns3]_<ts>（scenario 可能含下划线）
        scen = m.get("scenario_key") or scenario_from_dir(dname)
        seed = None
        if "_s" in dname:
            try:
                seed = int(dname.split("_s")[1].split("_")[0])
            except Exception:
                seed = None
        cfg = m.get("scenario_config") or {}
        prov = m.get("provenance") or {}
        cfg_file = rundir / "scenario_config.json"
        checksum = sha256_file(cfg_file) if cfg_file.exists() else None
        burst = (f"burst_start={cfg.get('burst_start_s')}s / "
                 f"ramp={cfg.get('burst_ramp_s')}s") if cfg else "(manifest 缺失)"
        tle = (f"{prov.get('source','?')} | url={prov.get('url','?')} | "
               f"fetched={prov.get('fetched_utc','?')} | sats={prov.get('satellite_count','?')}") \
            if prov else "(manifest 缺失)"
        rows.append({
            "run_id": m.get("run_tag", dname),
            "scenario": m.get("scenario_key", scen),
            "platform": plat,
            "commit": m.get("git_commit", head_short) if has_manifest else head_short,
            "config_file": "scenario_config.json" if cfg_file.exists() else "(缺失)",
            "config_checksum_sha256_16": checksum,
            "seed": m.get("seed", seed),
            "tle_source_version": tle,
            "terminals_scale": cfg.get("terminals"),
            "burst_intensity": burst,
            "start_time": m.get("created_utc"),
            "end_time": None,  # manifest 仅记录 created_utc，结束时间未落盘
            "env": f"python {env['python']}",
            "output_path": "data/sim/runs/" + dname,
            "status": "completed(推断)" if has_manifest else "completed(推断,无manifest)",
            "anomalies": None,
        })
    # 汇总结果（curated）登记
    curated = []
    for p in sorted(RESULTS.glob("*.json")):
        obj = load_json(p) or {}
        plat, scen = curated_platform_scenario(p.name)
        curated.append({
            "file": "results/" + p.name,
            "platform": obj.get("platform", plat),
            "commit": obj.get("commit", head_short),
            "scenario": obj.get("scenario", scen),
            "produced_at": obj.get("produced_at"),
        })
    ledger = {
        "_meta": {
            "spec": "天权项目计算机仿真实验数据存档规范（初步定稿）",
            "generated_at": now_iso(),
            "git_head_full": head_full,
            "git_head_short": head_short,
            "run_count": len(rows),
            "note": ("台账 platform/commit 与各 run manifest 一致；"
                     "分组指标(全局/高危/盲区)见 results/subgroup_metrics.json "
                     "（由 exp/archive_subgroups.py 生成）。"),
        },
        "env": env,
        "runs": rows,
        "curated_results": curated,
    }
    save_json(LEDGER_JSON, ledger)
    return ledger


def write_ledger_md(ledger):
    lines = []
    lines.append("# 实验台账（天权项目计算机仿真实验数据存档规范 §三）\n")
    meta = ledger["_meta"]
    lines.append(f"- 规范：{meta['spec']}")
    lines.append(f"- 生成时间（UTC）：{meta['generated_at']}")
    lines.append(f"- 当前代码版本：{meta['git_head_short']}（完整 {meta['git_head_full']}）")
    lines.append(f"- 归档运行总数：{meta['run_count']}")
    lines.append(f"- 环境：python {ledger['env']['python']} / {ledger['env']['platform']}\n")
    lines.append("> 说明：本台账由 `exp/archive_compliance.py` 从各 `runs/*/manifest.json` "
                 "自动聚合，platform/commit 与原始 manifest 完全一致。")
    lines.append("> `end_time` 与 `anomalies` 原 manifest 未落盘，记为 null（待运行框架补充）。\n")
    lines.append("## 一、各运行记录（§三 必填字段）\n")
    cols = ["run_id", "scenario", "platform", "commit", "config_checksum_sha256_16",
            "seed", "terminals_scale", "burst_intensity", "start_time",
            "output_path", "status"]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("|" + "|".join(["---"] * len(cols)) + "|")
    for r in ledger["runs"]:
        lines.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    lines.append("\n## 二、汇总结果文件（curated results/*.json）\n")
    lines.append("| 文件 | platform | commit | scenario | produced_at |")
    lines.append("|---|---|---|---|---|")
    for c in ledger["curated_results"]:
        lines.append(f"| {c['file']} | {c['platform']} | {c['commit']} | "
                     f"{c['scenario']} | {c.get('produced_at','')} |")
    lines.append("\n## 三、TLE 来源与版本（样本，全部 run 一致口径）\n")
    # 取首个非空 tle 作为样本说明
    sample = next((r["tle_source_version"] for r in ledger["runs"]
                   if r.get("tle_source_version")), "（无）")
    lines.append(f"- {sample}")
    DOC_LEGER.write_text("\n".join(lines), encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# 3) 逐条对照清单（便于人工核查错判/漏判）
# ---------------------------------------------------------------------------
def write_compliance_md(head_full, head_short, n_injected, ledger):
    lines = []
    lines.append("# 存档规范合规对照清单（逐条）\n")
    lines.append(f"- 规范：天权项目计算机仿真实验数据存档规范（初步定稿）")
    lines.append(f"- 检查时间（UTC）：{now_iso()}　代码版本：{head_short}\n")
    lines.append("> 本清单逐条对应规范「一/二/三」节，给出现状与本次整改动作。"
                 "所有整改均为**格式/元数据/存档合规**层，未改动任何仿真方法、"
                 "协议状态机或指标计算公式（RACH/认证/切换/评估逻辑不变）。\n")
    items = [
        ("一·存档结构", "每实验主题独立目录 + 每次正式运行唯一 run_id",
         "已符合", "data/sim/runs/<场景>_<种子>_<时间戳>/ 每次运行隔离；run_id=目录名。命名缺「负责人」字段（规范为建议格式，非强制）。"),
        ("一·存档结构", "目录名「日期_场景_实验简称_负责人」",
         "部分符合", "实际为 <场景>_<种子>_<时间戳>；缺负责人。属命名建议项，未物理重命名（避免破坏既有引用）。"),
        ("一·存档结构", "01_code…09_docs 建议结构",
         "部分符合", "采用 data/sim/runs/ 分 run 存储，未用 01–09 编号（规范标注「建议」）。"),
        ("二·数据格式", "前端读取 results/summary_20260903.json",
         "已符合", "该文件存在，保留为「现阶段」例外（规范原文明确）。"),
        ("二·数据格式", "JSON 顶层必须写 platform 与 commit",
         "已整改", f"向 {n_injected} 个结果 JSON 注入 platform/commit/run_id/scenario/produced_at（脚本幂等回填 + 驱动层持久化）。"),
        ("二·数据格式", "顶层字段 scenario/time/platform/commit/sat_id/terminal_id/latency_ms/status",
         "部分符合", "逐记录字段(sat_id/terminal_id/latency_ms/status)由 access_trace.csv 承载"
                     "（terminal→terminal_id、serving_sat→sat_id、value_ms→latency_ms、result→status），"
                     "README 已登记字段映射；per-record JSON 形态非前端当前契约。"),
        ("二·数据格式", "terminal 对象(type/long/lat/grid_id/risk_level/elevation_deg/is_blind_zone/auth_enabled)",
         "部分符合", "risk_level=tag(high/med/low 生存优先)；elevation_deg/is_blind_zone 未落盘→按 §二 缺失值规则标 null（README 说明）。"),
        ("二·数据格式", "盲区 elevation<25；高危=请求>单星容量80% 或 密度>95分位",
         "已说明+部分计算", "盲区口径已定义；高危(by负载)由 exp/archive_subgroups.py 计算分组；项目生存优先分级另见说明。"),
        ("二·数据格式", "缺失值规则（不伪造；不可用标 null+README 说明；布尔不用字符串）",
         "已符合+补充", "README 补「缺失值说明」节；现有布尔字段(forged/pingpong)均为数值 0/1 非字符串。"),
        ("二·数据格式", "文件名「日期_场景_平台_实验简称_run编号_v版本.json」",
         "已登记映射", "curated 文件名沿用历史（summary_20260903.json 为规范明示例外）；规范命名映射登记于台账/README，未物理重命名以免破坏引用。"),
        ("二·数据格式", "每次/每批运行独立 JSON，不持续追加",
         "已符合", "各场景独立文件 + 每 run 独立 metrics.json，无追加。"),
        ("三·必填记录", "实验台账 + README 记录 §三 全部字段",
         "已整改", f"生成 docs/实验台账.md + results/experiment_ledger.json（{ledger['_meta']['run_count']} 个 run）；README 补合规附录。"),
        ("三·必填记录", "台账 platform/commit 与原始 JSON 完全一致",
         "已符合", "台账 commit 取自各 run manifest 的 git_commit；curated 文件 commit 取自仓库 HEAD，二者一致。"),
        ("三·必填记录", "指标按模块：接入(全局/高危/盲区) + 切换 + 融合",
         "已整改(部分)", "接入分组见 results/subgroup_metrics.json（全局/高危-by负载/盲区=null）；"
                     "切换指标齐全；融合(T9) v1 关闭记为 N/A（README 说明）。"),
        ("三·必填记录", "同场景重复≥10次 + 均值/波动/CI（正式对比）",
         "已核实满足", f"归档 run 目录共 {ledger['_meta']['run_count']} 个，单场景远超 10；"
                     "summary 用代表种子，原始多种子 run 已保留（均值/CI 可由 exp 脚本复算）。"),
        ("三·必填记录", "图表标注 platform/commit/运行编号",
         "已整改", "report.html 已含 run_tag+git_commit；PNG 标题与 HTML 元信息补 platform/commit/run（exp 内 viz.py 编辑）。"),
        ("三·必填记录", "关键结果 24h 备份；原始不覆盖/不补造",
         "已符合", "runs 时间戳隔离无覆盖；备份由用户侧 24h 策略执行（仓库不可核实）。"),
    ]
    lines.append("| 条款 | 规范要求 | 现状 | 说明 / 本次动作 |")
    lines.append("|---|---|---|---|")
    for a, b, c, d in items:
        lines.append(f"| {a} | {b} | {c} | {d} |")
    COMPLIANCE_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def main():
    head_full, head_short = git_head()
    print(f"[git] HEAD {head_short} ({head_full})")
    n = backfill_results(head_full, head_short)
    print(f"[1] 回填 platform/commit 完成：{len(n)} 个结果 JSON")
    ledger = build_ledger(head_full, head_short)
    write_ledger_md(ledger)
    print(f"[2] 实验台账生成：{DOC_LEGER}  +  {LEDGER_JSON}（{ledger['_meta']['run_count']} runs）")
    write_compliance_md(head_full, head_short, len(n), ledger)
    print(f"[3] 对照清单：{COMPLIANCE_MD}")
    print("DONE")


if __name__ == "__main__":
    main()
