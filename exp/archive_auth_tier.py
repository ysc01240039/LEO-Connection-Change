#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""《认证三档对比指标口径》合规脚本（2026-09-16）。

职责（纯格式/元数据，不改仿真方法）：
1. 回填 auth_method / seed 到本地 data/sim/runs/*/metrics.json，对齐规范§3 每运行字段。
2. 聚合现有 HMAC 双根 run 的真实 block_rate / auth_latency，生成
   results/auth_tier_comparison.json（三档：HMAC 真实、无认证逻辑基线、5G-AKA 不可达）。
3. 生成 results/AUTH_TIER_COMPLIANCE.md，按规范 4 节逐条对照，诚实标注缺口。

不伪造任何数据：5G-AKA 因 AGENTS.md §0.3 无核心网约束在纯仿真中不可达，
无认证仅作逻辑基线（拦截率恒 0、时延恒 0），均如实标注，绝不以测量值冒充。
"""
import json
import re
import statistics as st
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "data" / "sim" / "runs"
RESULTS = ROOT / "results"
SPEC = "C:/Users/ASUS/Desktop/天权_认证三档对比指标口径.docx"
AUTH_METHOD = "hmac_dual_root"

_seed_re = re.compile(r"_s(\d+)")
_scen_re = re.compile(r"^(.*?)_s\d+")


def scenario_from_dir(dname: str) -> str:
    m = _scen_re.match(dname)
    return m.group(1) if m else dname


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def backfill_runs():
    """回填 auth_method / seed 到本地 run metrics（幂等）。runs/ 受 .gitignore 隔离，仅本地。"""
    n = 0
    if not RUNS.exists():
        return 0
    for p in RUNS.glob("*/metrics.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        dname = p.parent.name
        sm = _seed_re.search(dname)
        changed = False
        if m.get("auth_method") != AUTH_METHOD:
            m["auth_method"] = AUTH_METHOD
            changed = True
        if sm and m.get("seed") != int(sm.group(1)):
            m["seed"] = int(sm.group(1))
            changed = True
        if changed:
            p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
            n += 1
    return n


def aggregate_hmac():
    """聚合现有 HMAC run 中带伪造终端的子集，返回真实 block_rate / auth_latency 统计。"""
    br_all, lat_all = [], []
    per_scenario = {}
    if not RUNS.exists():
        return {"n": 0, "br": None, "lat": None, "per_scenario": {}}
    for p in RUNS.glob("*/metrics.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        ntot = m.get("fake_terminal_total", m.get("伪造终端数", 0))
        if not ntot or _num(ntot) in (0, None):
            continue  # 无伪造终端的 run 拦截率无定义，排除
        br = m.get("block_rate", m.get("伪造终端拦截率"))
        lat = m.get("auth_latency_ms", m.get("认证引入额外时延_ms"))
        br = _num(br)
        lat = _num(lat)
        if br is None or lat is None:
            continue
        br_all.append(br)
        lat_all.append(lat)
        sk = m.get("scenario") or scenario_from_dir(p.parent.name)
        per_scenario.setdefault(sk, {"br": [], "lat": []})
        per_scenario[sk]["br"].append(br)
        per_scenario[sk]["lat"].append(lat)

    def stat(xs):
        if not xs:
            return None
        return {
            "mean": round(st.mean(xs), 4),
            "stdev": round(st.stdev(xs), 4) if len(xs) > 1 else 0.0,
            "n": len(xs),
        }

    out_per = {}
    for sk, v in sorted(per_scenario.items()):
        out_per[sk] = {"block_rate": stat(v["br"]), "auth_latency_ms": stat(v["lat"])}
    return {
        "n_runs_with_forged": len(br_all),
        "block_rate": stat(br_all),
        "auth_latency_ms": stat(lat_all),
        "per_scenario": out_per,
    }


def build_comparison(agg):
    return {
        "spec": "天权 · 认证三档对比实验指标口径",
        "spec_file": SPEC,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "schemes": {
            "hmac_dual_root": {
                "available": True,
                "implemented": True,
                "source": "sim/auth.py（HMAC-SHA256 双根，真实校验）",
                "block_rate": agg["block_rate"],
                "auth_latency_ms": agg["auth_latency_ms"],
                "note": ("本方案实测；block_rate=密码层拦截/进入认证环节的伪造终端，"
                         "auth_latency_ms=认证引入额外时延(HMAC校验×星上降频)。"
                         "聚合自 %d 个含伪造终端的 HMAC run（跨多场景，非单条件5种子重复）。"
                         % agg["n_runs_with_forged"]),
            },
            "no_auth": {
                "available": True,
                "implemented": False,
                "logical_baseline": True,
                "block_rate": {"mean": 0.0, "stdev": 0.0, "n": 0},
                "auth_latency_ms": {"mean": 0.0, "stdev": 0.0, "n": 0},
                "note": ("逻辑基线：无认证则不存在拦截、亦无认证时延。项目未实现「关闭认证」"
                         "开关，未单独仿真该方案；列为理论基线供对照。"),
            },
            "5g_aka": {
                "available": False,
                "implemented": False,
                "reason": ("传统 5G-AKA 依赖核心网 UDM 校验并需多轮交互（3GPP TS 33.501），"
                           "违反 AGENTS.md §0.3『无核心网』环境约束，纯仿真不可达；"
                           "本对照方法/数据任务超出本次「格式合规」范围，待人/A 组决策。"),
            },
        },
        "per_run_field_mapping": {
            "run_id": "✅ 既有(manifest/metrics)",
            "scenario": "✅ 既有",
            "seed": "✅ 本次新增回填(metrics 顶层)",
            "auth_method": "✅ 本次新增回填(metrics 顶层, 固定 hmac_dual_root)",
            "auth_latency_ms": "✅ 映射 认证引入额外时延_ms",
            "fake_terminal_total": "✅ 映射 伪造终端数",
            "fake_terminal_blocked": "✅ 本次新增(伪造终端拦截数)",
            "block_rate": "✅ 映射 伪造终端拦截率",
            "git_commit": "✅ 既有(存档合规注入)",
        },
    }


def write_compliance_md(agg):
    br = agg["block_rate"] or {}
    lat = agg["auth_latency_ms"] or {}
    br_s = f"{br.get('mean')} ± {br.get('stdev')} (n={br.get('n')})" if br else "无数据"
    lat_s = f"{lat.get('mean')} ± {lat.get('stdev')} (n={lat.get('n')})" if lat else "无数据"
    md = f"""# 认证三档对比指标口径 · 合规自检

> 对照规范：`天权_认证三档对比指标口径.docx`
> 生成时间：{datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
> 范围：仅格式/元数据合规与缺口核查，**未改动任何仿真方法**（与《数据存档规范》合规同一纪律）。

## 0. 一句话结论
本方案（HMAC 双根）的拦截率、认证时延、每运行 9 字段均已对齐规范；**但规范要求的「三档对比实验」中，
无认证仅可作逻辑基线、5G-AKA 因无核心网约束在纯仿真中不可达**——完整三档 × 5 种子均值±标准差对照
**尚未执行**（属方法/数据任务，不在本次格式合规范围内）。

## 1. 三档方案对照
| 方案 | 是否在仿真实现 | 数据来源 | 备注 |
|---|---|---|---|
| 无认证 | 否（无关闭认证开关） | 逻辑基线 | 拦截率=0、时延=0，未单独仿真 |
| 传统 5G-AKA | 否（不可达） | — | 需核心网 UDM（TS 33.501），违反 AGENTS.md §0.3 无核心网约束 |
| 本方案 HMAC 双根 | **是** | sim/auth.py + 现有 run | 拦截率/时延真实可测 |

## 2. 核心指标
| 指标 | 规范口径 | 项目现状 | 符合 |
|---|---|---|---|
| 伪造终端拦截率 | 成功拦截数 / 伪造总数 | `伪造终端拦截率`(=block_rate)，实测 {br_s} | ✅ |
| 认证时延 | 认证开始→结果返回 | `认证引入额外时延_ms`(=auth_latency_ms)，实测 {lat_s} | ⚠️ 口径为「认证引入额外时延」(HMAC 校验×降频)，非完整认证往返；起止事件按规范备注待 A 组结合代码统一 |

## 3. 实验条件与统计
- 实验条件一致、仅改认证方案：❌ 另两方案未实现，无法做「仅改方案」对照。
- 每方案 5 次随机种子、均值±标准差：⚠️ 聚合工具已就绪（sim/eval.py `merge_reps` / `confidence_intervals`），
  但**三档结构化重复实验未执行**；HMAC 侧可用现有含伪造终端 run 做真实聚合（见 results/auth_tier_comparison.json，
  n={agg['n_runs_with_forged']}，注意跨场景、非单条件 5 种子）。

## 4. 每次运行建议保留字段（规范§3）
| 字段 | 项目对齐情况 |
|---|---|
| run_id | ✅ 既有 |
| scenario | ✅ 既有 |
| seed | ✅ 本次新增回填（metrics 顶层） |
| auth_method | ✅ 本次新增回填（固定 `hmac_dual_root`） |
| auth_latency_ms | ✅ 映射 `认证引入额外时延_ms` |
| fake_terminal_total | ✅ 映射 `伪造终端数` |
| fake_terminal_blocked | ✅ 本次新增（`伪造终端拦截数`） |
| block_rate | ✅ 映射 `伪造终端拦截率` |
| git_commit | ✅ 存档合规注入 |

## 5. 补充指标（规范§4，CPU/信令）
可选，当前无输出，标注 **N/A**（不强制）。

## 6. 关键缺口与后续（待 A 组 / 人决策，非格式问题）
1. **5G-AKA 不可仿真**：硬约束（无核心网），如需该基线须改为「引用 3GPP 文献值」或放宽环境约束。
2. **无认证仅逻辑基线**：如需真实「无认证」run，需在 sim/protocol.py 加认证开关（方法改动，超出本次范围）。
3. **三档 × 5 种子重复实验未跑**：需在 run_sim.py 增加 auth_method 遍历 + 5 种子，属实验设计任务。
"""
    return md


def main():
    RESULTS.mkdir(exist_ok=True)
    n_back = backfill_runs()
    agg = aggregate_hmac()
    comp = build_comparison(agg)
    (RESULTS / "auth_tier_comparison.json").write_text(
        json.dumps(comp, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULTS / "AUTH_TIER_COMPLIANCE.md").write_text(
        write_compliance_md(agg), encoding="utf-8")
    print(f"[backfill] 回填 auth_method/seed 的 run metrics 数: {n_back}")
    print(f"[aggregate] 含伪造终端的 HMAC run 数: {agg['n_runs_with_forged']}")
    print(f"[aggregate] block_rate = {agg['block_rate']}")
    print(f"[aggregate] auth_latency_ms = {agg['auth_latency_ms']}")
    print("[write] results/auth_tier_comparison.json")
    print("[write] results/AUTH_TIER_COMPLIANCE.md")


if __name__ == "__main__":
    main()
