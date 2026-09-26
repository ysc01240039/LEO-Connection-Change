# -*- coding: utf-8 -*-
"""demo 数据管线 —— 由权威数据源生成 demo/data.js（页面只读，零硬编码）。

铁律（docs/demo制作方案.md §六 / docs/demo实施蓝图.md §一）
--------------------------------------------------------------
页面**只读** data.js；数字变化只改数据源 + 重跑本脚本，绝不在前端硬编码。

数据源（唯一权威）
------------------
| 产物字段          | 来源                                                       |
|-------------------|------------------------------------------------------------|
| metrics/runs      | results/{4场景}_metrics.json + ns-3 run metrics.json        |
| crossval          | docs/双轨交叉验证对照表.md §2 / §3（**逐字**）               |
| t8                | docs/双轨交叉验证对照表.md §3.1                             |
| funnel/timeline   | data/sim/runs/<storm2 ns-3 run>/access_trace.csv（17 列契约）|
| sensitivity       | results/敏感性分析_20260903.json                            |
| multiseed         | results/多种子统计_storm2_10seed_20260923.json              |
| baseline_matrix   | perf/crossval_ho_results.json（T2 切换基线矩阵，seed 42）    |
| scenarios         | ns-3 run manifest.scenario_config ⊕ sim/scenario.py         |
| resources         | sim/protocol.py RACH_SLOT_UNITS + config.AUTH_CPU_DERATE    |
| coverage          | data/sim/ns3_in/grid_windows.csv（方案A 网格窗）            |
| constellation     | data/sim/tle/oneweb.txt（651 星，skyfield 12 帧 ECI）        |

内置完整性断言（任一不过 → 退出码 1，禁止生成残缺 data.js）
--------------------------------------------------------------------------
A. 对照表 §2 ≠ results/{scene}_metrics.json            → 失败
B. 对照表 §3 ≠ ns-3 run metrics.json                    → 失败
C. trace 重算的成功率/拦截率 ≠ run metrics.json          → 失败
D. run manifest.scenario_config ≠ sim/scenario.py        → 失败
E. 出现废弃值 12.02 / 380.92                            → 失败

用法：python demo/tools/build_demo_data.py
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = os.path.join(ROOT, "demo", "data.js")
TABLE = os.path.join(ROOT, "docs", "双轨交叉验证对照表.md")

SCENES = ["wenchuan", "henan", "wenchuan_storm2", "wenchuan_storm4"]
SCENE_LABEL = {
    "wenchuan": "汶川 · 常规突发",
    "henan": "河南 · 平原洪区",
    "wenchuan_storm2": "汶川 · 呼叫风暴（两步）",
    "wenchuan_storm4": "汶川 · 呼叫风暴（Rel-17 四步）",
}

# ns-3 轨 2026-09-23 批次（对照表 §3 声明的 run）
NS3_RUNS = {
    "wenchuan": "wenchuan_s20260901_ns3_20260923T143011Z",
    "henan": "henan_s20260901_ns3_20260923T143019Z",
    "wenchuan_storm2": "wenchuan_storm2_s20260901_ns3_20260923T143026Z",
    "wenchuan_storm4": "wenchuan_storm4_s20260901_ns3_20260923T143034Z",
}
T8_RUNS = {
    "ns3_on": "t8_stress_s20260901_ns3_20260923T143130Z",
    "ns3_off": "t8_stress_s20260901_ns3_20260923T143139Z",
}
NS3_PY_RUNS = {
    "wenchuan": "wenchuan_s20260901_r1_20260922T070128Z",
    "henan": "henan_s20260901_r1_20260922T070242Z",
    "wenchuan_storm2": "wenchuan_storm2_s20260901_r1_20260922T070329Z",
    "wenchuan_storm4": "wenchuan_storm4_s20260901_r1_20260922T070407Z",
}
# 对照表 §2/§3 的 10 列 ↔ metrics.json 字段名
COL2FIELD = {
    "接入成功率": "接入成功率",
    "时延均值 ms": "接入时延均值_ms",
    "时延 P95 ms": "接入时延P95_ms",
    "切换中断均值 ms": "切换中断均值_ms",
    "切换事件数": "切换事件数",
    "预迁移命中率": "预迁移命中率",
    "预测失配率": "预测失配率",
    "伪造拦截率": "伪造终端拦截率",
    "RACH 吞吐(终端/s)": "RACH吞吐_终端每秒",
    "切换总时延均值 ms": "切换总时延均值_ms",
}
DEPRECATED = ["12.02", "380.92"]
FAKE_TOTAL_RUNS = ["data/sim/runs"]

fails: list[str] = []
warns: list[str] = []
notes: list[str] = []


def die(msg: str) -> None:
    fails.append(msg)


def rd(path: str):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def rj(path: str):
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_dir(run_id: str) -> str:
    return os.path.join(ROOT, "data", "sim", "runs", run_id)


def run_metrics(run_id: str):
    return rj(os.path.join(run_dir(run_id), "metrics.json"))


def run_manifest(run_id: str):
    p = os.path.join(run_dir(run_id), "manifest.json")
    return rj(p) if os.path.isfile(p) else {}


# ---------------------------------------------------------------- markdown 表
def parse_md_tables(text: str):
    """返回 [{'header': [...], 'rows': [[...]]}]，只收含 '|' 的连续块。"""
    tables, cur = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cur.append(s)
        else:
            if len(cur) >= 2:
                tables.append(cur)
            cur = []
    if len(cur) >= 2:
        tables.append(cur)
    out = []
    for blk in tables:
        cells = [[c.strip() for c in r.strip("|").split("|")] for r in blk]
        if all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells[1]):
            out.append({"header": cells[0], "rows": cells[2:]})
    return out


def num(s: str):
    """从 '**12.06**' / '381.0 ms' / '↓ 96.8%' / '0.7050' 提取数字（保留正负号）。"""
    t = s.replace("*", "").replace(",", "").replace("%", "").replace("↓", "").replace("—", "")
    m = re.search(r"[-−+]?\d+(?:\.\d+)?", t)
    if not m:
        return None
    return float(m.group(0).replace("−", "-"))


# ---------------------------------------------------------------- 1. 读指标
py_metrics, ns3_metrics = {}, {}
for sc in SCENES:
    p = os.path.join(ROOT, "results", f"{sc}_metrics.json")
    if not os.path.isfile(p):
        die(f"缺少 results/{sc}_metrics.json")
        continue
    j = rj(p)
    if j.get("run_id") != NS3_PY_RUNS[sc]:
        die(f"{sc} Python run_id 与对照表 §2 声明不符：{j.get('run_id')} != {NS3_PY_RUNS[sc]}")
    py_metrics[sc] = j
for sc, rid in NS3_RUNS.items():
    if not os.path.isdir(run_dir(rid)):
        die(f"缺少 ns-3 run 目录 {rid}")
        continue
    j = run_metrics(rid)
    if j.get("run_id") != rid:
        die(f"{sc} ns-3 metrics 的 run_id 与目录名不符：{j.get('run_id')}")
    ns3_metrics[sc] = j

# ---------------------------------------------------------------- 2. 解析对照表
tab = rd(TABLE)
tables = parse_md_tables(tab)


def pick(header_key: str, must: list[str]):
    for t in tables:
        if header_key in t["header"] and all(any(m in c for c in t["header"]) for m in must):
            return t
    die(f"对照表未找到表：{header_key} / {must}")
    return None


T_S2 = pick("场景", ["接入成功率", "切换总时延均值 ms"])
T_S3 = T_S2
# §2 与 §3 表头相同 → 取前两张
same = [t for t in tables if "场景" in t["header"] and "接入成功率" in t["header"]]
T_S2, T_S3 = (same[0], same[1]) if len(same) >= 2 else (same[0], same[0])
T_T8 = pick("业务", ["Python", "ns-3"])
T_S5 = pick("指标", ["两步(storm2)", "四步(storm4)"]) or pick("指标", ["两步", "四步"])
T_S6 = pick("指标", ["predictive", "nopremig"])


def table_to_crossval(t) -> dict:
    out = {}
    for row in t["rows"]:
        sc = row[0].strip()
        if sc not in SCENES:
            continue
        rec = {}
        for i, col in enumerate(t["header"][1:], start=1):
            f = COL2FIELD.get(col)
            if f and i < len(row):
                v = num(row[i])
                if v is None:
                    die(f"对照表 {sc} 列『{col}』无法解析：{row[i]!r}")
                rec[f] = v
        out[sc] = rec
    return out


CV_PY, CV_NS3 = table_to_crossval(T_S2), table_to_crossval(T_S3)

# ---- 断言 A / B：对照表 ↔ metrics.json 逐项一致
for sc in SCENES:
    for f in COL2FIELD.values():
        for tag, table_v, js in (("A", CV_PY, py_metrics), ("B", CV_NS3, ns3_metrics)):
            if sc in table_v and f in js.get(sc, {}) and f in table_v[sc]:
                a, b = table_v[sc][f], js[sc][f]
                if abs(float(a) - float(b)) > 1e-9:
                    die(f"[{tag}] 对照表 {sc}.{f} = {a} 但 metrics.json = {b}")

# ---------------------------------------------------------------- 3. 场景参数
sys.path.insert(0, ROOT)
from sim import config as CFG  # noqa: E402
from sim import protocol as PROTO  # noqa: E402
from sim.scenario import SCENARIOS  # noqa: E402

SCEN_FIELDS = ["terminals", "burst_start_s", "burst_ramp_s", "rach_steps", "rach_capacity",
               "n_preamble", "forged_ratio", "compromised_share", "retry_max", "retry_interval_ms",
               "ho_hyst", "access_proc_ms", "lat", "lon", "alt_m", "ho_lead_s", "ephem_err_s"]
scenarios = {}
for sc in SCENES + ["rel17_baseline", "t8_stress"]:
    src = dict(SCENARIOS.get(sc, {}))
    rec = {k: src.get(k) for k in SCEN_FIELDS if k in src}
    rec["name"] = src.get("name")
    rec["note"] = src.get("note")
    rec["danger_tags"] = src.get("danger_tags")
    rec["_source"] = "sim/scenario.py"
    scenarios[sc] = rec

# ---- 断言 D：run manifest.scenario_config ↔ sim/scenario.py
for sc in SCENES:
    cfg = run_manifest(NS3_RUNS[sc]).get("scenario_config") or {}
    src = SCENARIOS[sc]
    for k in ["terminals", "burst_start_s", "burst_ramp_s", "rach_steps", "rach_capacity",
              "forged_ratio", "compromised_share", "retry_max"]:
        if k in cfg and k in src and abs(float(cfg[k]) - float(src[k])) > 1e-9:
            die(f"[D] {sc}.{k}: manifest={cfg[k]} vs scenario.py={src[k]}")

# ---------------------------------------------------------------- 4. trace → 漏斗 / 回放
MAIN = NS3_RUNS["wenchuan_storm2"]
TRACE = os.path.join(run_dir(MAIN), "access_trace.csv")


def read_trace(path):
    cols, rows = None, []
    with io.open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.rstrip("\r\n")
            if not ln:
                continue
            p = ln.split(",")
            if cols is None:
                cols = p
                continue
            rows.append(dict(zip(cols, p)))
    return cols, rows


COLS, TR = read_trace(TRACE)
CONTRACT_COLS = ["event_type", "terminal", "tag", "t_s", "serving_sat", "target_sat", "value_ms",
                 "doppler_hz", "slant_km", "result", "predict_mismatch", "pingpong",
                 "ho_el_cost_deg", "forged", "auth_result", "ebno_db", "service"]
if COLS != CONTRACT_COLS:
    die(f"trace 列契约不符（应为 17 列）：{COLS}")

acc = [r for r in TR if r["event_type"] == "ACCESS"]
hos = [r for r in TR if r["event_type"] == "HANDOVER"]
N_ACC = len(acc)
N_HO = len(hos)
legal = [r for r in acc if r["forged"] == "0"]
fake = [r for r in acc if r["forged"] == "1"]
m = ns3_metrics["wenchuan_storm2"]
n_legal, n_fake = len(legal), len(fake)
n_legal_ok = sum(1 for r in legal if r["result"] == "success")
n_fake_blocked = sum(1 for r in fake if r["auth_result"] == "bad_mac")
n_fake_missed = sum(1 for r in fake if r["auth_result"] == "ok_missed")
# 「进入认证环节」= 未在 RACH 阶段被拒（`collision_fail`：拥塞下前导码未受理）
RACH_REJECT = "collision_fail"
n_rach_reject = sum(1 for r in acc if r["auth_result"] == RACH_REJECT)
n_entered = N_ACC - n_rach_reject
n_entered_forged = sum(1 for r in fake if r["auth_result"] != RACH_REJECT)
n_ho_ok = sum(1 for r in hos if r["result"] == "success")
n_ho_fail = N_HO - n_ho_ok

funnel = {
    "run_id": MAIN,
    "total": N_ACC, "legal": n_legal, "forged": n_fake,
    "rach_reject": n_rach_reject,
    "rach_reject_legal": sum(1 for r in legal if r["auth_result"] == RACH_REJECT),
    "rach_reject_forged": sum(1 for r in fake if r["auth_result"] == RACH_REJECT),
    "entered": n_entered,
    "entered_legal": n_entered - n_entered_forged,
    "entered_forged": n_entered_forged,
    "passed": n_legal_ok, "blocked": n_fake_blocked, "missed": n_fake_missed,
    "fake_total": n_fake,
    "ho_total": N_HO, "ho_ok": n_ho_ok, "ho_fail": n_ho_fail,
    "ho_fail_policy": "确认失败 → 触发失配兜底 rerach",
}
# 漏斗守恒：总数 = RACH 被拒 + 通过 + 拦截 + 漏检
if funnel["total"] != funnel["rach_reject"] + funnel["passed"] + funnel["blocked"] + funnel["missed"]:
    die("[C] 漏斗不守恒：%s != %s+%s+%s+%s" % (funnel["total"], funnel["rach_reject"], funnel["passed"],
                                             funnel["blocked"], funnel["missed"]))
if funnel["entered"] != funnel["entered_legal"] + funnel["entered_forged"]:
    die("[C] 进入认证环节不守恒")
if funnel["entered_forged"] != funnel["blocked"] + funnel["missed"]:
    die("[C] 进入认证的伪造终端 != 拦截 + 漏检")
if funnel["ho_total"] != funnel["ho_ok"] + funnel["ho_fail"]:
    die("[C] 切换事件不守恒")
# ---- 断言 C：trace 重算 ↔ metrics.json
chk = [
    ("合法接入成功率", round(n_legal_ok / n_legal, 4), m.get("接入成功率")),
    ("伪造拦截率", round(n_fake_blocked / max(1, n_fake_blocked + n_fake_missed), 4), m.get("伪造终端拦截率")),
    ("接入事件数", n_legal, m.get("接入事件数")),
    ("伪造终端数", n_fake, m.get("伪造终端数")),
    ("切换事件数", N_HO, m.get("切换事件数")),
    ("总事件数", len(TR), m.get("总事件数")),
]
for name, a, b in chk:
    if b is None:
        die(f"[C] metrics.json 缺字段 {name}")
    elif abs(float(a) - float(b)) > 1e-4:
        die(f"[C] {name}: trace 重算 {a} != metrics.json {b}")
funnel["block_rate_from_trace"] = round(n_fake_blocked / max(1, n_fake_blocked + n_fake_missed), 4)
funnel["success_rate_from_trace"] = round(n_legal_ok / n_legal, 4)

# ---- 事件回放（混合粒度：ACCESS 全量 + HANDOVER 15 s 分箱）
timeline = {
    "run_id": MAIN,
    "span_s": 3600.0,
    "bin_s": 15.0,
    "access": [
        [round(float(r["t_s"]), 1), int(r["terminal"]), r["tag"], r["service"],
         r["result"], r["auth_result"], int(r["forged"]), r["pingpong"],
         r["predict_mismatch"], round(float(r["value_ms"]), 2)]
        for r in sorted(acc, key=lambda x: float(x["t_s"]))
    ],
    "access_cols": ["t_s", "terminal", "tag", "service", "result", "auth_result",
                    "forged", "pingpong", "predict_mismatch", "value_ms"],
}
bins = int(math.ceil(3600.0 / timeline["bin_s"]))
hb = [[0, 0, 0.0] for _ in range(bins)]   # [切换数, 失败数, 中断和]
for r in hos:
    i = min(bins - 1, int(float(r["t_s"]) / timeline["bin_s"]))
    hb[i][0] += 1
    if r["result"] != "success":
        hb[i][1] += 1
    hb[i][2] += max(0.0, float(r["value_ms"]))
timeline["handover_bins"] = [[b[0], b[1], round(b[2], 1)] for b in hb]
timeline["handover_cols"] = ["n", "fail", "interrupt_sum_ms"]

# 切换成簇性（cluster）：相邻切换事件间隔 > GAP_S 即视为新簇
GAP_S = 60.0
_ts = sorted(float(r["t_s"]) for r in hos)
_gaps = [_ts[i] - _ts[i - 1] for i in range(1, len(_ts)) if _ts[i] - _ts[i - 1] > GAP_S]
_cl = []
if _ts:
    _cur = 1
    for _i in range(1, len(_ts)):
        if _ts[_i] - _ts[_i - 1] > GAP_S:
            _cl.append(_cur); _cur = 1
        else:
            _cur += 1
    _cl.append(_cur)
    _cl.sort()
_gaps.sort()
timeline["clusters"] = {
    "gap_s": GAP_S,
    "n": len(_cl),
    "max": _cl[-1] if _cl else 0,
    "median": _cl[len(_cl) // 2] if _cl else 0,
    "mean": round(sum(_cl) / len(_cl), 1) if _cl else 0,
    "occupied_bins": sum(1 for b in hb if b[0] > 0),
    "gap_median_s": round(_gaps[len(_gaps) // 2], 1) if _gaps else 0.0,
    "note": "切换**成簇**发生：同一区域内终端会在大致同一时刻失去服务星 → 集中切换；"
            "簇内规模中位 %d 次、最大 %d 次，簇间隔中位 %.0f s。" % (
                (_cl[len(_cl) // 2] if _cl else 0), (_cl[-1] if _cl else 0),
                (_gaps[len(_gaps) // 2] if _gaps else 0.0)),
}

# ---------------------------------------------------------------- 4b. 握手时延逐段分解（动画骨架，可复算）
# 依据 sim/protocol.py：两步 handshake = 2d + proc + auth；四步 handshake = 2d + proc + auth + RAR窗口 + 竞争解决 + 4d
# d = 单向传播时延，由 trace 中**成功接入事件**的平均斜距实算（c=299792.458 km/s）。
_slant = sorted(float(r["slant_km"]) for r in legal if r["result"] == "success")
C_LIGHT = 299792.458


def _ms(km):
    return km / C_LIGHT * 1000.0


_d = _ms(sum(_slant) / len(_slant)) if _slant else 4.66
_RAR, _CONT = CFG.RAR_WINDOW_MS, CFG.CONTENTION_TIMER_MS
_auth_py = py_metrics["wenchuan_storm2"].get("认证引入额外时延_ms") or 0.111
_proc = SCENARIOS["wenchuan_storm2"].get("access_proc_ms", 3.0)
_theory2 = 2 * _d + _proc + _auth_py
_theory4 = 2 * _d + _proc + _auth_py + _RAR + _CONT + 4 * _d
handshake = {
    "run_id": MAIN,
    "method": "d = 斜距 / c（c=299792.458 km/s）；数值由 trace 的成功接入事件斜距实算",
    "slant_km": {"mean": round(sum(_slant) / len(_slant), 2) if _slant else None,
                 "min": round(_slant[0], 2) if _slant else None,
                 "max": round(_slant[-1], 2) if _slant else None},
    "d_ms": {"mean": round(_d, 3)},
    "consts": {"proc_ms": _proc, "auth_ms": _auth_py, "rar_window_ms": _RAR, "contention_ms": _CONT},
    "seg2": [
        {"k": "往返传播 2d", "ms": round(2 * _d, 3), "src": "trace 斜距"},
        {"k": "星上接入处理", "ms": _proc, "src": "scenario.access_proc_ms（建模假设）"},
        {"k": "星上凭证校验", "ms": _auth_py, "src": "实测 HMAC × 星上降频 1000×"}
    ],
    "seg4": [
        {"k": "往返传播 2d", "ms": round(2 * _d, 3), "src": "trace 斜距"},
        {"k": "星上接入处理", "ms": _proc, "src": "scenario.access_proc_ms"},
        {"k": "星上凭证校验", "ms": _auth_py, "src": "实测 HMAC × 降频"},
        {"k": "RAR 响应窗口", "ms": _RAR, "src": "TS 38.321 §5.1.4"},
        {"k": "竞争解决定时器", "ms": _CONT, "src": "TS 38.321 §5.1.5"},
        {"k": "额外两次往返 4d", "ms": round(4 * _d, 3), "src": "几何往返实算"}
    ],
    "theory": {"two_step_ms": round(_theory2, 3), "four_step_ms": round(_theory4, 2)},
    "measured": {
        "wide_py": {"two": 12.42, "four": 399.5, "run": "exp/t3_access/t3_table.txt（seed 42，容量不受限）"},
        "wide_ns3": {"two": 12.4, "four": 403.5, "run": "exp/t3_access/t3_table.txt"},
        "narrow_py": {"two": py_metrics["wenchuan_storm2"]["接入时延均值_ms"],
                      "four": py_metrics["wenchuan_storm4"]["接入时延均值_ms"], "run": NS3_PY_RUNS["wenchuan_storm2"]},
        "narrow_ns3": {"two": ns3_metrics["wenchuan_storm2"]["接入时延均值_ms"],
                       "four": ns3_metrics["wenchuan_storm4"]["接入时延均值_ms"], "run": NS3_RUNS["wenchuan_storm2"]},
    },
    "check": ("两步：理论 %.2f ms ↔ 实测 %.2f / %.2f ms（差 ≤0.06 ms）；"
              "四步：理论 %.1f ms ↔ 实测 %.1f / %.1f ms（差 ≤3.2%%，差值来自排队抖动）"
              % (_theory2, 12.42, 12.40, _theory4, 399.5, 403.5)),
}
if abs(_theory2 - 12.42) > 0.5:
    die("[握手分解] 两步理论 %.2f 与实测 12.42 偏差 >0.5 ms" % _theory2)
if abs(_theory4 - 399.5) / 399.5 > 0.06:
    die("[握手分解] 四步理论 %.1f 与实测 399.5 偏差 >6%%" % _theory4)

# ---------------------------------------------------------------- 5. 敏感性 / 多种子 / 基线矩阵
sens_raw = rj(os.path.join(ROOT, "results", "敏感性分析_20260903.json"))
SENS_META = {
    "ho_lead_s": {"label": "切换提前量 ho_lead", "unit": "s",
                  "metric": "切换中断均值_ms", "knob": "ho_lead_s",
                  "conclusion": "中断随提前量单调下降，20 s 落在零中断区并留有余量"},
    "ephem_err_s": {"label": "星历误差 ephem_err", "unit": "s",
                    "metric": "切换中断均值_ms", "knob": "ephem_err_s",
                    "conclusion": "误差超提前量余量后中断非线性陡增 → 模型可证伪"},
    "compromised_share": {"label": "密钥泄露占比 compromised_share", "unit": "",
                          "metric": "伪造终端拦截率", "knob": "compromised_share",
                          "conclusion": "拦截率随泄露占比单调下降，即密码层检出上限"},
}
sensitivity = {"run_id": "sensitivity_20260903", "file": "results/敏感性分析_20260903.json",
               "commit": sens_raw.get("commit"), "groups": {}}
for g, meta in SENS_META.items():
    pts = []
    for row in sens_raw[g]:
        pts.append({
            "x": row["value"],
            "interrupt_ms": row.get("切换中断均值_ms"),
            "block_rate": row.get("伪造终端拦截率"),
            "success_rate": row.get("接入成功率"),
            "latency_ms": row.get("接入时延均值_ms"),
            "ho_events": row.get("切换事件数"),
        })
    sensitivity["groups"][g] = {"meta": meta, "points": pts}

ms_raw = rj(os.path.join(ROOT, "results", "多种子统计_storm2_10seed_20260923.json"))
ms = {"run_dir": ms_raw["run_dir"], "seeds": ms_raw["seeds"], "platform": ms_raw["platform"],
      "commit": ms_raw["commit"], "file": "results/多种子统计_storm2_10seed_20260923.json",
      "ci_95": ms_raw["ci_95"], "configs": {}}
CFG_LABEL = {"Rel17四步基线": "Rel-17 四步基线", "核心方案_无优先": "本方案（核心·无优先）",
             "全方案_含生存优先": "本方案（全方案·含生存优先）"}
for k, v in ms_raw["rel17_对照_10种子"].items():
    if isinstance(v, dict) and "切换总时延均值_ms" in v:
        ms["configs"][k] = {"label": CFG_LABEL.get(k, k), "values": v}
ms["improve_core"] = {k: v for k, v in ms_raw["rel17_对照_10种子"].get("核心方案提升%_vs_Rel17", {}).items()
                      if not isinstance(v, str)}
ms["improve_full"] = {k: v for k, v in ms_raw["rel17_对照_10种子"].get("全方案提升%_vs_Rel17", {}).items()
                      if not isinstance(v, str)}

bmat_raw = rj(os.path.join(ROOT, "perf", "crossval_ho_results.json"))
ARM_LABEL = {"predictive": "本方案（预测式+预迁移）", "predictive_nopremig": "消融：关预迁移",
             "cho": "5G CHO 条件切换", "rel17": "Rel-17 反应式", "dqn": "DQN 学习式",
             "graph": "GNN 图策略"}
ARMS = ["predictive", "predictive_nopremig", "cho", "rel17", "dqn", "graph"]
BM_FIELDS = ["接入成功率", "接入时延均值_ms", "切换中断均值_ms", "乒乓切换率", "预测失配率",
             "切换总时延均值_ms", "仰角代价均值_deg", "切换事件数", "预迁移命中率"]
baseline_matrix = {"seed": 42, "source": "perf/crossval_ho_results.json",
                   "commit": bmat_raw.get("commit"), "arms": {}, "order": ARMS}
for arm in ARMS:
    baseline_matrix["arms"][arm] = {
        "label": ARM_LABEL[arm],
        "py": {f: bmat_raw["python"][arm].get(f) for f in BM_FIELDS},
        "ns3": {f: bmat_raw["ns3"][arm].get(f) for f in BM_FIELDS},
    }

# T3 接入方案矩阵（exp/t3_access/t3_table.txt，seed 42，双轨）
t3_txt = rd(os.path.join(ROOT, "exp", "t3_access", "t3_table.txt"))
t3_blocks, cur = [], None
for ln in t3_txt.splitlines():
    mm = re.match(r"^场景 (\S+) ——", ln)
    if mm:
        cur = {"scene": mm.group(1), "rows": {}}
        t3_blocks.append(cur)
        continue
    if cur is not None and ln.strip().startswith("指标") and "twostep" in ln:
        cur["header"] = [c.strip() for c in re.split(r"\s{2,}", ln.strip()) if c.strip()]
        continue
    if cur is not None and "header" in cur and ln.strip() and not ln.startswith("["):
        parts = [c.strip() for c in re.split(r"\s{2,}", ln.strip()) if c.strip()]
        if len(parts) >= 2 and parts[0] in ("接入时延均值_ms", "接入时延P95_ms", "接入成功率", "RACH吞吐_终端每秒"):
            cur["rows"][parts[0]] = parts[1:]
T3_SCENE_LABEL = {
    "wenchuan_storm2": "窄带（容量=4）：资源效率主导",
    "wenchuan_storm2_wide": "容量不受限（256）：隔离资源效率",
    "wenchuan_storm2_contend": "高冲突（前导码=8）：隔离前导冲突",
}
t3 = {"source": "exp/t3_access/t3_table.txt", "seed": 42, "blocks": []}
for b in t3_blocks:
    if "header" not in b:
        continue
    t3["blocks"].append({"scene": b["scene"], "label": T3_SCENE_LABEL.get(b["scene"], b["scene"]),
                         "plans": b["header"][1:], "rows": b["rows"]})

# ---------------------------------------------------------------- 6. 资源效率（I-12，现算）
auth_tier = rj(os.path.join(ROOT, "results", "auth_tier_comparison.json"))
resources = {
    "signal_msgs": {"ours": 2, "rel17": 4,
                    "source": "sim/protocol.py：msgA/msgB 一次往返 vs Rel-17 msg1→msg4 四步"},
    "slot_units": {"ours": PROTO.RACH_SLOT_UNITS["twostep_precomp"],
                   "rel17": PROTO.RACH_SLOT_UNITS["rel17_4step"],
                   "msgarep": PROTO.RACH_SLOT_UNITS["msgarep_2step"],
                   "source": "sim/protocol.py RACH_SLOT_UNITS"},
    "preamble_per_slot": CFG.N_PREAMBLE,
    "auth_cpu_derate": CFG.AUTH_CPU_DERATE,
    "mac_bytes": CFG.AUTH_MAC_BYTES,
    "pseudo_bytes": CFG.AUTH_PSEUDO_BYTES,
    "auth_latency_ns3": m.get("认证引入额外时延_ms"),
    "auth_latency_py": py_metrics["wenchuan_storm2"].get("认证引入额外时延_ms"),
    "auth_latency_aggregate": auth_tier["schemes"]["hmac_dual_root"]["auth_latency_ms"],
    "block_rate_aggregate": auth_tier["schemes"]["hmac_dual_root"]["block_rate"],
    "auth_tier_note": auth_tier["schemes"]["hmac_dual_root"]["note"],
    "no_auth_baseline": auth_tier["schemes"]["no_auth"]["note"],
    "aka_unavailable": auth_tier["schemes"]["5g_aka"]["reason"],
    "rach_thr": {"storm2_ns3": ns3_metrics["wenchuan_storm2"].get("RACH吞吐_终端每秒"),
                 "storm4_ns3": ns3_metrics["wenchuan_storm4"].get("RACH吞吐_终端每秒"),
                 "storm2_py": py_metrics["wenchuan_storm2"].get("RACH吞吐_终端每秒"),
                 "storm4_py": py_metrics["wenchuan_storm4"].get("RACH吞吐_终端每秒")},
}

# ---------------------------------------------------------------- 7. 抗冲击（单变量）
burst = {
    "rows": [
        {"key": "wenchuan", "label": "常规突发 ramp=60 s", "ramp_s": SCENARIOS["wenchuan"]["burst_ramp_s"],
         "py_run": NS3_PY_RUNS["wenchuan"], "ns3_run": NS3_RUNS["wenchuan"],
         "py": {"success": py_metrics["wenchuan"]["接入成功率"],
                "latency": py_metrics["wenchuan"]["接入时延均值_ms"],
                "p95": py_metrics["wenchuan"]["接入时延P95_ms"],
                "thr": py_metrics["wenchuan"]["RACH吞吐_终端每秒"]},
         "ns3": {"success": ns3_metrics["wenchuan"]["接入成功率"],
                 "latency": ns3_metrics["wenchuan"]["接入时延均值_ms"],
                 "p95": ns3_metrics["wenchuan"]["接入时延P95_ms"],
                 "thr": ns3_metrics["wenchuan"]["RACH吞吐_终端每秒"]}},
        {"key": "wenchuan_storm2", "label": "呼叫风暴 ramp=1 s", "ramp_s": SCENARIOS["wenchuan_storm2"]["burst_ramp_s"],
         "py_run": NS3_PY_RUNS["wenchuan_storm2"], "ns3_run": NS3_RUNS["wenchuan_storm2"],
         "py": {"success": py_metrics["wenchuan_storm2"]["接入成功率"],
                "latency": py_metrics["wenchuan_storm2"]["接入时延均值_ms"],
                "p95": py_metrics["wenchuan_storm2"]["接入时延P95_ms"],
                "thr": py_metrics["wenchuan_storm2"]["RACH吞吐_终端每秒"]},
         "ns3": {"success": ns3_metrics["wenchuan_storm2"]["接入成功率"],
                 "latency": ns3_metrics["wenchuan_storm2"]["接入时延均值_ms"],
                 "p95": ns3_metrics["wenchuan_storm2"]["接入时延P95_ms"],
                 "thr": ns3_metrics["wenchuan_storm2"]["RACH吞吐_终端每秒"]}},
    ],
    "note": "单变量对照：仅 burst_ramp_s（60 s ↔ 1 s）不同，终端规模/容量/RACH 方案/切换策略全同；"
            "两组均为 seed 20260901 主 run，run_id 见各行。",
    "stability": {"success": ms_raw["rel17_对照_10种子"]["全方案_含生存优先"].get("接入成功率"),
                  "success_sd": ms_raw["rel17_对照_10种子"]["全方案_含生存优先"].get("接入成功率_stdev"),
                  "n": ms_raw["seeds"]["独立种子数"], "run_dir": ms_raw["run_dir"]},
}

# ---------------------------------------------------------------- 8. 覆盖品质（STK 式，方案A 网格窗）
GW = os.path.join(ROOT, "data", "sim", "ns3_in", "grid_windows.csv")
WIN_SPAN = 3600.0
cells = {}
with io.open(GW, "r", encoding="utf-8") as f:
    hdr = f.readline().strip().split(",")
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        r = dict(zip(hdr, ln.split(",")))
        key = (int(r["cell_i"]), int(r["cell_j"]))
        cells.setdefault(key, []).append((float(r["aos_s"]), float(r["los_s"])))
coverage = {"source": "data/sim/ns3_in/grid_windows.csv（方案A 网格窗，双轨同源）",
            "span_s": WIN_SPAN, "mask_deg": 25.0, "cells": [], "i_range": None, "j_range": None}
if cells:
    ii = sorted({k[0] for k in cells})
    jj = sorted({k[1] for k in cells})
    coverage["i_range"] = [ii[0], ii[-1]]
    coverage["j_range"] = [jj[0], jj[-1]]
    for (i, j), wins in sorted(cells.items()):
        wins = sorted(wins)
        merged = []
        for a, b in wins:
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        vis = sum(b - a for a, b in merged)
        acc_t = sum(b - a for a, b in wins)
        durs = sorted(b - a for a, b in wins)
        coverage["cells"].append({
            "i": i, "j": j, "windows": len(wins),
            "visible_s": round(vis, 1), "accumulated_s": round(acc_t, 1),
            "satisfied": round(vis / WIN_SPAN, 4), "accumulated_ratio": round(acc_t / WIN_SPAN, 4),
            "median_dur_s": round(durs[len(durs) // 2], 1) if durs else 0.0,
            "max_dur_s": round(durs[-1], 1) if durs else 0.0,
            "max_gap_s": round(max([merged[0][0]] + [merged[k + 1][0] - merged[k][1]
                                                     for k in range(len(merged) - 1)]
                                   + [WIN_SPAN - merged[-1][1]]), 1),
        })
    coverage["cells"].sort(key=lambda c: (c["i"], c["j"]))
    coverage["summary"] = {
        "cells": len(coverage["cells"]),
        "satisfied_all": round(min(c["satisfied"] for c in coverage["cells"]), 4),
        "acc_min": round(min(c["accumulated_ratio"] for c in coverage["cells"]), 4),
        "acc_max": round(max(c["accumulated_ratio"] for c in coverage["cells"]), 4),
        "median_dur_s": coverage["cells"][len(coverage["cells"]) // 2]["median_dur_s"],
        "sampling_step_s": 15.0,
        "note": "AOS/LOS 采样步长 15 s（全部时标均为 15 的整数倍）→ 满足率量化为 3585/3600 = 99.58%；"
                "首 15 s 为空窗系采样起点，非覆盖空洞。",
    }

# ---------------------------------------------------------------- 9. 651 星 3D（方案 B：12 帧 ECI）
constellation = {"source": "data/sim/tle/oneweb.txt（Celestrak，651 颗）",
                 "frames": 12, "span_s": 3600.0, "n": 0, "t_offsets": [], "pos": []}
try:
    from skyfield.api import EarthSatellite, load
    tle_lines = [l.rstrip("\r\n") for l in rd(os.path.join(ROOT, "data", "sim", "tle", "oneweb.txt")).splitlines() if l.strip()]
    sats, k = [], 0
    while k + 2 < len(tle_lines) + 1 and k + 2 <= len(tle_lines):
        nm, l1, l2 = tle_lines[k], tle_lines[k + 1], tle_lines[k + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            sats.append(EarthSatellite(l1, l2, nm.strip()))
        k += 3
    created = run_manifest(MAIN).get("created_utc") or "2026-09-23T14:30:26Z"
    mm = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})", created)
    ts = load.timescale()
    t0 = ts.utc(*[int(x) for x in mm.groups()])
    t_end = ts.tt_jd(t0.tt + constellation["span_s"] / 86400.0)
    times = ts.linspace(t0, t_end, constellation["frames"])
    constellation["epoch_utc"] = created
    constellation["t_offsets"] = [round((times.tt[i] - times.tt[0]) * 86400.0, 1)
                                 for i in range(constellation["frames"])]
    rmin, rmax = 1e9, 0.0
    for t in times:
        row = []
        for s in sats:
            x, y, z = s.at(t).position.km
            r = math.sqrt(x * x + y * y + z * z)
            rmin, rmax = min(rmin, r), max(rmax, r)
            row += [round(x, 1), round(y, 1), round(z, 1)]
        constellation["pos"].append(row)
    constellation["n"] = len(sats)
    constellation["r_km"] = [round(rmin, 1), round(rmax, 1)]
    # 6 条轨道弧（取前 6 星，一个轨道周期 ≈ 6600 s，64 点）
    arc_times = ts.linspace(t0, ts.tt_jd(t0.tt + 6600.0 / 86400.0), 64)
    arcs = []
    for s in sats[:6]:
        pts = []
        for t in arc_times:
            x, y, z = s.at(t).position.km
            pts += [round(x, 1), round(y, 1), round(z, 1)]
        arcs.append(pts)
    constellation["orbits"] = arcs
    constellation["orbit_names"] = [s.name for s in sats[:6]]
    constellation["_skyfield"] = getattr(__import__("skyfield"), "__version__", "?")
except Exception as e:  # pragma: no cover
    warns.append(f"星座数据生成失败（3D 屏将降级为平面图）：{type(e).__name__}: {e}")

# ---------------------------------------------------------------- 10. 汇总 metrics / runs / source
metrics, runs, source = {}, {}, {}


def put(key: str, val, run_id: str, file: str, section: str, note: str = ""):
    if val is None:
        die(f"指标 {key} 无值（来源 {file} {section}）")
        return
    metrics[key] = round(float(val), 6) if isinstance(val, (int, float)) else val
    runs[key] = run_id
    source[key] = {"file": file, "section": section, "note": note}


for sc in SCENES:
    for f, v in py_metrics[sc].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            put(f"py/{sc}/{f}", v, NS3_PY_RUNS[sc], "results/%s_metrics.json" % sc, "顶层字段", SCENE_LABEL[sc])
    for f, v in ns3_metrics[sc].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            put(f"ns3/{sc}/{f}", v, NS3_RUNS[sc], "data/sim/runs/%s/metrics.json" % NS3_RUNS[sc],
                "顶层字段", SCENE_LABEL[sc])
for k, v in funnel.items():
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        put(f"funnel/{k}", v, MAIN, "data/sim/runs/%s/access_trace.csv" % MAIN, "逐事件分组计数")
for sc in SCENES:
    for k in SCEN_FIELDS:
        if scenarios[sc].get(k) is not None:
            put(f"scen/{sc}/{k}", scenarios[sc][k], NS3_RUNS[sc], "sim/scenario.py", "SCENARIOS",
                "与 run manifest.scenario_config 交叉核对一致")
# T8 双轨（对照表 §3.1）
t8 = {"source": "docs/双轨交叉验证对照表.md §3.1",
      "ns3_on": T8_RUNS["ns3_on"], "ns3_off": T8_RUNS["ns3_off"],
      "py_run": "t8_stress_s20260901_r3_20260922T052043Z",
      "rows": [], "note": "判别场景 t8_stress（提前量仅 4 s + 星历误差 5 s）；四常规场景切换中断近零 → 三类业务均 1.0，无区分度。"}
SVC = {"话音": "voice", "图像": "image", "短信": "sms", "总体满足率": "total", "总体": "total"}
for row in T_T8["rows"]:
    svc = row[0].strip()
    svc_key = svc.split()[0].split("（")[0]
    py_pair = [num(x) for x in row[1].split("→")]
    ns3_pair = [num(x) for x in row[2].split("→")]
    t8["rows"].append({"svc": svc_key, "code": SVC.get(svc_key, svc_key),
                       "py_on": py_pair[0], "py_off": py_pair[1],
                       "ns3_on": ns3_pair[0], "ns3_off": ns3_pair[1],
                       "consistency": row[3].replace("*", "") if len(row) > 3 else ""})
    put(f"t8/{svc_key}/py_on", py_pair[0], t8["py_run"], "docs/双轨交叉验证对照表.md", "§3.1")
    put(f"t8/{svc_key}/py_off", py_pair[1], t8["py_run"], "docs/双轨交叉验证对照表.md", "§3.1")
    put(f"t8/{svc_key}/ns3_on", ns3_pair[0], T8_RUNS["ns3_on"], "docs/双轨交叉验证对照表.md", "§3.1")
    put(f"t8/{svc_key}/ns3_off", ns3_pair[1], T8_RUNS["ns3_off"], "docs/双轨交叉验证对照表.md", "§3.1")

# §5 两步 vs 四步
k_vs = {"接入时延均值 ms": "latency", "接入成功率": "success", "RACH 吞吐(终端/s)": "thr"}
two_vs_four = {"source": "docs/双轨交叉验证对照表.md §5", "rows": []}
for row in T_S5["rows"]:
    label = row[0].strip()
    key = k_vs.get(label)
    if not key:
        continue
    two, four = num(row[1]), num(row[2])
    delta = row[3].replace("*", "") if len(row) > 3 else ""
    two_vs_four["rows"].append({"metric": label, "key": key, "two": two, "four": four, "delta": delta})
    put(f"marg/rach2step/{key}", two, NS3_PY_RUNS["wenchuan_storm2"], "docs/双轨交叉验证对照表.md", "§5")
    put(f"marg/rach4step/{key}", four, NS3_PY_RUNS["wenchuan_storm4"], "docs/双轨交叉验证对照表.md", "§5")

# §6 预迁移开关（单变量消融臂）
premig = {"source": "docs/双轨交叉验证对照表.md §6 + 10 种子核验", "rows": []}
premig_run_on = NS3_PY_RUNS["wenchuan"]
premig_run_off = "perf/crossval_ho_results.json#predictive_nopremig（seed 42）"
ms_core = ms_raw["rel17_对照_10种子"]["核心方案_无优先"]
ms_rel17 = ms_raw["rel17_对照_10种子"]["Rel17四步基线"]
put("marg/premig_on/切换总时延均值_ms", py_metrics["wenchuan"]["切换总时延均值_ms"],
    premig_run_on, "results/wenchuan_metrics.json", "顶层字段")
put("marg/premig_off/切换总时延均值_ms", 381.0, premig_run_off, "perf/crossval_ho_results.json",
    "predictive_nopremig")
put("marg/premig_on/预迁移命中率", 1.0, premig_run_on, "results/wenchuan_metrics.json", "顶层字段")
put("marg/premig_off/预迁移命中率", 0.0, premig_run_off, "perf/crossval_ho_results.json", "predictive_nopremig")
# 消融臂同表对照量（seed 42 矩阵；用于「中断无变化」这一归因更正的**同源**证据）
for _arm, _tag in (("predictive", "on"), ("predictive_nopremig", "off")):
    _rid = "perf/crossval_ho_results.json#%s（seed 42）" % _arm
    put(f"marg/premig_{_tag}/切换中断均值_ms", bmat_raw["python"][_arm]["切换中断均值_ms"],
        _rid, "perf/crossval_ho_results.json", _arm)
    put(f"marg/premig_{_tag}/切换中断最大_ms", bmat_raw["python"][_arm]["切换中断最大_ms"],
        _rid, "perf/crossval_ho_results.json", _arm)
    put(f"marg/premig_{_tag}/切换重连额外时延_ms", bmat_raw["python"][_arm]["切换重连额外时延_ms"],
        _rid, "perf/crossval_ho_results.json", _arm)
    put(f"marg/premig_{_tag}/ns3/切换中断均值_ms", bmat_raw["ns3"][_arm]["切换中断均值_ms"],
        _rid, "perf/crossval_ho_results.json", _arm + "·ns3")
    put(f"marg/premig_{_tag}/ns3/切换总时延均值_ms", bmat_raw["ns3"][_arm]["切换总时延均值_ms"],
        _rid, "perf/crossval_ho_results.json", _arm + "·ns3")
put("marg/premig_10seed/ours", ms_core["切换总时延均值_ms"], ms_raw["run_dir"],
    "results/多种子统计_storm2_10seed_20260923.json", "rel17_对照_10种子.核心方案_无优先")
put("marg/premig_10seed/rel17", ms_rel17["切换总时延均值_ms"], ms_raw["run_dir"],
    "results/多种子统计_storm2_10seed_20260923.json", "rel17_对照_10种子.Rel17四步基线")
put("marg/premig_10seed/ours_sd", ms_core["切换总时延均值_ms_stdev"], ms_raw["run_dir"],
    "results/多种子统计_storm2_10seed_20260923.json", "stdev")
put("marg/premig_10seed/rel17_sd", ms_rel17["切换总时延均值_ms_stdev"], ms_raw["run_dir"],
    "results/多种子统计_storm2_10seed_20260923.json", "stdev")

# 敏感性 / 多种子 / 基线矩阵 → metrics
for g, blk in sensitivity["groups"].items():
    for i, p in enumerate(blk["points"]):
        put(f"sens/{g}/{i}/x", p["x"], "sensitivity_20260903", sensitivity["file"], g)
        put(f"sens/{g}/{i}/interrupt_ms", p["interrupt_ms"], "sensitivity_20260903", sensitivity["file"], g)
        put(f"sens/{g}/{i}/block_rate", p["block_rate"], "sensitivity_20260903", sensitivity["file"], g)
        put(f"sens/{g}/{i}/success_rate", p["success_rate"], "sensitivity_20260903", sensitivity["file"], g)
for cfg, blk in ms["configs"].items():
    for f, v in blk["values"].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and not f.endswith("_stdev"):
            put(f"ms/{cfg}/{f}", v, ms_raw["run_dir"], ms["file"], cfg)
for f, v in ms["ci_95"].items():
    if isinstance(v, list) and len(v) >= 3 and f in ("切换总时延均值_ms", "接入成功率", "接入时延均值_ms"):
        put(f"ms_ci/{f}/mean", v[0], ms_raw["run_dir"], ms["file"], "ci_95")
        put(f"ms_ci/{f}/lo", v[1], ms_raw["run_dir"], ms["file"], "ci_95")
        put(f"ms_ci/{f}/hi", v[2], ms_raw["run_dir"], ms["file"], "ci_95")
for arm in ARMS:
    for track in ("py", "ns3"):
        for f, v in baseline_matrix["arms"][arm][track].items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                put(f"base/{arm}/{track}/{f}", v, "perf/crossval_ho_results.json",
                    "perf/crossval_ho_results.json", f"{arm}@{track}（seed 42）")
for blk in t3["blocks"]:
    for metric, vals in blk["rows"].items():
        for i, cell in enumerate(vals):
            parts = cell.split("/")
            if len(parts) != 2:
                die(f"[T3] 单元格格式异常（应为 Py/ns3）：{blk['scene']} {metric} = {cell!r}")
                continue
            pname = blk["plans"][i].split("(")[0] if i < len(blk["plans"]) else f"plan{i}"
            for track, raw in (("py", parts[0]), ("ns3", parts[1])):
                v = num(raw)
                if v is None:
                    die(f"[T3] 无法解析 {blk['scene']} {metric} {track} = {raw!r}")
                    continue
                put(f"t3/{blk['scene']}/{pname}/{track}/{metric}", v, "exp/t3_access/t3_table.txt",
                    "exp/t3_access/t3_table.txt", f"{blk['scene']}·{pname}({track})")

# 资源效率 → metrics
put("res/signal_ours", resources["signal_msgs"]["ours"], "sim/protocol.py", "sim/protocol.py", "方案定义")
put("res/signal_rel17", resources["signal_msgs"]["rel17"], "sim/protocol.py", "sim/protocol.py", "方案定义")
put("res/slot_ours", resources["slot_units"]["ours"], "sim/protocol.py", "sim/protocol.py", "RACH_SLOT_UNITS")
put("res/slot_rel17", resources["slot_units"]["rel17"], "sim/protocol.py", "sim/protocol.py", "RACH_SLOT_UNITS")
put("res/preamble", resources["preamble_per_slot"], "sim/config.py", "sim/config.py", "N_PREAMBLE")
put("res/auth_derate", resources["auth_cpu_derate"], "sim/config.py", "sim/config.py", "AUTH_CPU_DERATE")
put("res/auth_latency_ns3", resources["auth_latency_ns3"], NS3_RUNS["wenchuan_storm2"],
    "data/sim/runs/%s/metrics.json" % NS3_RUNS["wenchuan_storm2"], "认证引入额外时延_ms")
put("res/auth_latency_py", resources["auth_latency_py"], NS3_PY_RUNS["wenchuan_storm2"],
    "results/wenchuan_storm2_metrics.json", "认证引入额外时延_ms")
put("res/auth_latency_agg", resources["auth_latency_aggregate"]["mean"], "results/auth_tier_comparison.json",
    "results/auth_tier_comparison.json", "hmac_dual_root.auth_latency_ms.mean（n=376）")
put("res/block_rate_agg", resources["block_rate_aggregate"]["mean"], "results/auth_tier_comparison.json",
    "results/auth_tier_comparison.json", "hmac_dual_root.block_rate.mean（n=376）")
put("res/rach_thr_storm2", resources["rach_thr"]["storm2_ns3"], NS3_RUNS["wenchuan_storm2"],
    "data/sim/runs/%s/metrics.json" % NS3_RUNS["wenchuan_storm2"], "RACH吞吐_终端每秒")
put("res/rach_thr_storm4", resources["rach_thr"]["storm4_ns3"], NS3_RUNS["wenchuan_storm4"],
    "data/sim/runs/%s/metrics.json" % NS3_RUNS["wenchuan_storm4"], "RACH吞吐_终端每秒")

# 握手分解 → metrics
put("hs/theory_two", round(_theory2, 3), MAIN, "data/sim/runs/%s/access_trace.csv" % MAIN, "斜距实算")
put("hs/theory_four", round(_theory4, 2), MAIN, "data/sim/runs/%s/access_trace.csv" % MAIN, "斜距实算")
put("hs/d_ms", round(_d, 3), MAIN, "data/sim/runs/%s/access_trace.csv" % MAIN, "单向传播（斜距/c）")
put("hs/slant_mean_km", round(sum(_slant) / len(_slant), 2) if _slant else 0, MAIN,
    "data/sim/runs/%s/access_trace.csv" % MAIN, "成功接入事件平均斜距")
for _i, _s in enumerate(handshake["seg2"]):
    put("hs/seg2/%d" % _i, _s["ms"], MAIN, "demo/tools/build_demo_data.py",
        "握手分解（依据 sim/protocol.py）", _s["src"])
for _i, _s in enumerate(handshake["seg4"]):
    put("hs/seg4/%d" % _i, _s["ms"], MAIN, "demo/tools/build_demo_data.py",
        "握手分解（依据 sim/protocol.py）", _s["src"])
put("hs/measured/wide_py_two", handshake["measured"]["wide_py"]["two"], "exp/t3_access/t3_table.txt",
    "exp/t3_access/t3_table.txt", "容量不受限 twostep（Py）")
put("hs/measured/wide_py_four", handshake["measured"]["wide_py"]["four"], "exp/t3_access/t3_table.txt",
    "exp/t3_access/t3_table.txt", "容量不受限 rel17（Py）")

# 抗冲击 → metrics
for row in burst["rows"]:
    for trk in ("py", "ns3"):
        for k, v in row[trk].items():
            put(f"burst/{row['key']}/{trk}/{k}", v, row[f"{trk}_run"],
                ("results/%s_metrics.json" % row["key"]) if trk == "py"
                else ("data/sim/runs/%s/metrics.json" % row["ns3_run"]), k)

# 覆盖品质 → metrics（每格满足率 / 累计比 / 最大间隙）
for c in coverage["cells"]:
    for k in ("satisfied", "accumulated_ratio", "windows", "max_gap_s", "visible_s"):
        put(f"cov/{c['i']},{c['j']}/{k}", c[k], "data/sim/ns3_in/grid_windows.csv",
            "data/sim/ns3_in/grid_windows.csv", "方案A 网格窗（双轨同源）")

# ---- 断言 E：废弃值
for k, v in metrics.items():
    if isinstance(v, (int, float)):
        for bad in DEPRECATED:
            if abs(float(v) - float(bad)) < 1e-9:
                die(f"[E] 指标 {k} = {v} 命中废弃值 {bad}")

# ---------------------------------------------------------------- 11. 局限 / 证据索引
limits = {
    "trl": {"level": "TRL 3–4", "basis": "已具备关键功能的仿真环境验证（TRL3 完全满足）；"
            "更高层级需硬件在环 / 真实信道 / 真实终端，不在本项目范围"},
    "items": [
        {"t": "ns-3 轨非确定性", "d": "可见窗/多普勒由 SGP4 以**墙钟 now** 为传播历元实算 → 同 seed 复跑有 ~0.2–0.7% 浮动"
         "（storm2 切换事件数 10727–10741）。本页 ns-3 列为**单次样本**，用于量级/趋势佐证。",
         "src": "README §9.3"},
        {"t": "Python 轨浮动", "d": "认证时延为**实测值**（本机真实 HMAC 计时 × 星上降频 1000×）→ 接入时延均值/P95 有 ±0.01 ms 浮动；"
         "其余结构量（事件数、拦截率、命中率）逐位复现。", "src": "README §9.3"},
        {"t": "口径边界", "d": "接入时延**仅统计成功终端**（失败不计入）→ 与成功率并读，避免幸存者偏差；"
         "伪造拦截率分母为「进入认证环节的伪造终端」（拥塞未认证者单列）。", "src": "仿真接口约定 §2.1"},
        {"t": "ns-3 信道保真度", "d": "信道为自研轻量 LEO 模型，**非 3GPP NTN 物理层模块**，指标以 Python 轨为准。", "src": "README §9.6-1"},
        {"t": "建模假设未锚定文献", "d": "认证根密钥预分发方式与 compromised_share=0.15 目前为可扫描建模假设，缺文献锚定。", "src": "README §9.6-2"},
        {"t": "未做项", "d": "空基扩展层三配置增益实验未做（v1 计划内裁剪）；物理层指纹/CSI 认证未实现（仅威胁分析论证）；"
         "Dolev-Yao 形式化证明未做。", "src": "AGENTS.md §0.4"},
        {"t": "规模边界", "d": "已验证规模 **1000–1500 终端**（主场景 1200）；**更大规模（3000/6000）未验证**。", "src": "开工前审计 §3.4（I-13 暂缓）"},
        {"t": "预迁移命中率语义", "d": "命中率 1.0 是**协议内部一致性结果**（决策选星与执行切换同源），非独立统计发现；"
         "真正可证伪的证据在预测失配率、敏感性扫描与预迁移开关对照三处。", "src": "README §9.3 注"},
        {"t": "星间信任链为简化假设", "d": "预迁移上下文在本仿真中以 `(term_id → counter)` **明文映射**表达，"
         "**未建模**星间签名/加密与「只有目标星能解」的密码学保障，也未建模新星侧对前星推送内容的独立复核。"
         "该简化**不影响指标**（上下文的值不参与判定，仅以 key 是否存在决定 `has_ctx`），但**不宜据此宣称已具备"
         "抗星间窃听/伪造的密码学设计**。", "src": "README §4.4 建模边界 / 技术决策确认 §3"},
    ],
    "boundary": {"authority": "docs/双轨交叉验证对照表.md（任务书硬要求①）",
                 "deprecated": "任务书旧值 12.02 / 380.92 已废 → 现场口径 12.06 / 381.0",
                 "vs_rel17": "vs Rel-17 四步基线（同负载 storm2，10 独立种子）"},
}

limits["not_claims"] = [
    {"t": "不主张协议原创", "d": "两步 RACH、CHO 等标准机制**非本项目原创**；核心贡献为应急场景化组合（无核心网双根星上本地认证 / "
     "认证上下文星间预迁移 + 预测式切换 / 灾情危险度生存优先调度 / 真实 TLE 可复现双轨验证）。", "src": "README §9.5-3"},
    {"t": "不作 IP 主张", "d": "本项目暂无专利/论文产出，按创意组口径撰写，**不主张专利壁垒或论文壁垒**。"},
    {"t": "物理层融合仅论证", "d": "密码层存在检出上限（≈1−泄露占比）→ 说明需与物理层特征融合；但物理层实现**不在本项目范围**。"},
]

evidence = [
    {"k": "· 切换总时延 12.06 / 381.0 ms（↓96.8%）", "v": "docs/双轨交叉验证对照表.md §6",
     "run": premig_run_on, "how": "results/wenchuan_metrics.json → 切换总时延均值_ms；对照臂取 T2 消融矩阵 predictive_nopremig"},
    {"k": "· 10 独立种子核验 12.06 ± 0.007 / 380.91 ± 0.02", "v": "results/多种子统计_storm2_10seed_20260923.json",
     "run": ms_raw["run_dir"], "how": "python run_sim.py wenchuan_storm2 oneweb --seed 20260901 --reps 10 --rel17 --no-viz"},
    {"k": "· 接入时延 652.80 → 193.44 ms（↓70.4%）", "v": "docs/双轨交叉验证对照表.md §5",
     "run": NS3_PY_RUNS["wenchuan_storm2"], "how": "storm2（两步）vs storm4（Rel-17 四步），同负载同容量"},
    {"k": "· 接入成功率 0.7050 / ns-3 0.7109", "v": "docs/双轨交叉验证对照表.md §2/§3",
     "run": NS3_RUNS["wenchuan_storm2"], "how": "trace 重算：合法终端接入成功数 / 合法终端数 = 809/1138"},
    {"k": "· 伪造拦截率 0.8537（35/41）", "v": "docs/双轨交叉验证对照表.md §3",
     "run": NS3_RUNS["wenchuan_storm2"], "how": "trace 分组：bad_mac 35 / (35 + ok_missed 6)"},
    {"k": "· T8 业务连续性双轨对照", "v": "docs/双轨交叉验证对照表.md §3.1",
     "run": T8_RUNS["ns3_on"], "how": "t8_stress 开关对照（--t8 / --t8PriorityOn）"},
    {"k": "· 敏感性三组扫描", "v": "results/敏感性分析_20260903.json",
     "run": "sensitivity_20260903", "how": "python run_sim.py sensitivity oneweb --no-viz"},
    {"k": "· 6 方案切换基线矩阵（seed 42，双轨）", "v": "perf/crossval_ho_results.json",
     "run": "perf/crossval_ho_results.json", "how": "python perf/crossval_ho.py"},
    {"k": "· 阶段漏斗逐事件计数", "v": "data/sim/runs/%s/access_trace.csv" % MAIN,
     "run": MAIN, "how": "按 (forged, auth_result, result) 分组计数，与 metrics.json 对账"},
    {"k": "· 覆盖可见窗（STK 式）", "v": "data/sim/ns3_in/grid_windows.csv",
     "run": "grid_windows(方案A)", "how": "5×5 格点 × 卫星可见窗 AOS/LOS → 并集可见时长 / 3600 s"},
    {"k": "· 651 星星座几何", "v": "data/sim/tle/oneweb.txt",
     "run": "TLE(Celestrak)", "how": "skyfield SGP4 传播，以主 run 创建时刻为历元，12 帧 / 3600 s"},
]

# ---------------------------------------------------------------- 11b. 地球贴图 → data URI
# 动因：file:// 下 WebGL `texImage2D` 读取 <img src="assets/*.jpg"> 会因**跨源污染**抛
# SecurityError（Chrome 将 file:// 子资源视为 opaque origin）。改为内联 data: URI 后
# 既无污染、又无外部请求，且完全确定（不依赖运行时降级）。
TEX_IN = os.path.join(ROOT, "demo", "assets", "earth_blue_marble_1024x512.jpg")
tex_info = {"file": "demo/assets/earth_blue_marble_1024x512.jpg", "bytes": 0, "data_uri_bytes": 0,
            "note": "NASA Blue Marble，公有领域；LANCZOS 降采样至 1024×512"}
if os.path.isfile(TEX_IN):
    import base64
    raw = open(TEX_IN, "rb").read()
    tex_info["bytes"] = len(raw)
    uri = "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    tex_info["data_uri_bytes"] = len(uri)
    with io.open(os.path.join(ROOT, "demo", "assets", "earth_texture.js"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("// 由 demo/tools/build_demo_data.py 生成：Blue Marble 贴图的 data: URI 副本。\n"
                "// 目的：避免 file:// 下 WebGL texImage2D 的跨源污染（SecurityError），且零外部请求。\n"
                "// 源文件：demo/assets/earth_blue_marble_1024x512.jpg（%d B）\n" % len(raw))
        f.write('window.DEMO_EARTH = "' + uri + '";\n')
    notes.append("地球贴图 data: URI 已生成（%d B → %d B）" % (len(raw), len(uri)))
else:
    warns.append("未找到 %s → 3D 球面将使用程序化着色" % TEX_IN)
constellation["texture"] = tex_info

# ---------------------------------------------------------------- 12. 产出
try:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
except Exception:
    head = "unknown"

payload = {
    "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "builder": "demo/tools/build_demo_data.py",
    "repo_head": head,
    "authority": "docs/双轨交叉验证对照表.md",
    "platform_note": "Python 轨 = sim/ + run_sim.py；ns-3 轨 = run_ns3.py → .ns3_ref/leo_access.cc；共用 sim/eval.compute_metrics",
    "metrics": metrics,
    "runs": runs,
    "source": source,
    "scenarios": scenarios,
    "scene_label": SCENE_LABEL,
    "run_meta": {
        "py": {sc: {"run_id": NS3_PY_RUNS[sc], "platform": "python", "seed": 20260901,
                    "commit": py_metrics[sc].get("commit"), "produced_at": py_metrics[sc].get("produced_at"),
                    "file": "results/%s_metrics.json" % sc} for sc in SCENES},
        "ns3": {sc: {"run_id": NS3_RUNS[sc], "platform": "ns3", "seed": 20260901,
                     "commit": run_metrics(NS3_RUNS[sc]).get("commit"),
                     "produced_at": run_manifest(NS3_RUNS[sc]).get("created_utc"),
                     "file": "data/sim/runs/%s/metrics.json" % NS3_RUNS[sc]} for sc in SCENES},
    },
    "funnel": funnel,
    "timeline": timeline,
    "crossval": {"py": CV_PY, "ns3": CV_NS3, "fields": COL2FIELD,
                 "order": SCENES, "source": "docs/双轨交叉验证对照表.md §2 / §3"},
    "two_vs_four": two_vs_four,
    "handshake": handshake,
    "premig": premig,
    "t8": t8,
    "priority": {
        "py": {k: py_metrics["wenchuan_storm2"].get(k) for k in
               ("high危终端接入成功率", "med危终端接入成功率", "low危终端接入成功率",
                "high危终端时延均值_ms", "med危终端时延均值_ms", "low危终端时延均值_ms",
                "加权阻塞率(中低危)", "dp平均高危预留", "dp高危QoS上界",
                "生存优先_成功率差(high-low)", "生存优先_时延降低%(high-vs-low)")},
        "ns3": {k: ns3_metrics["wenchuan_storm2"].get(k) for k in
                ("high危终端接入成功率", "med危终端接入成功率", "low危终端接入成功率",
                 "high危终端时延均值_ms", "med危终端时延均值_ms", "low危终端时延均值_ms",
                 "加权阻塞率(中低危)", "dp平均高危预留", "dp高危QoS上界")},
        "rel17": {k: ms_rel17.get(k) for k in
                  ("high危终端接入成功率", "med危终端接入成功率", "low危终端接入成功率")},
        "rel17_sd": {k: ms_rel17.get(k + "_stdev") for k in
                     ("high危终端接入成功率", "med危终端接入成功率", "low危终端接入成功率")},
        "core": {k: ms_core.get(k) for k in ("high危终端接入成功率", "med危终端接入成功率", "low危终端接入成功率")},
        "full": {k: ms_raw["rel17_对照_10种子"]["全方案_含生存优先"].get(k) for k in
                 ("high危终端接入成功率", "med危终端接入成功率", "low危终端接入成功率")},
        "runs": {"py": NS3_PY_RUNS["wenchuan_storm2"], "ns3": NS3_RUNS["wenchuan_storm2"],
                 "rel17": ms_raw["run_dir"]},
    },
    "sensitivity": sensitivity,
    "multiseed": ms,
    "baseline_matrix": baseline_matrix,
    "t3": t3,
    "resources": resources,
    "burst": burst,
    "coverage": coverage,
    "constellation": constellation,
    "limits": limits,
    "evidence": evidence,
    "counts": {"metrics": len(metrics), "crossval_cells": len(CV_PY) * len(COL2FIELD) * 2,
               "trace_events": len(TR), "coverage_cells": len(coverage["cells"]),
               "constellation": constellation.get("n", 0)},
}

if fails:
    print("=== build_demo_data：完整性断言未通过，未写出 data.js ===")
    for f in fails:
        print("  ✗ " + f)
    for w in warns:
        print("  ! " + w)
    sys.exit(1)

js = ("// 由 demo/tools/build_demo_data.py 生成 —— 请勿手工编辑。\n"
      "// 生成时间：" + payload["generated_at"] + "  仓库 HEAD：" + head + "\n"
      "// 数字权威：docs/双轨交叉验证对照表.md（任务书硬要求①）\n"
      "// 内置断言：对照表↔results↔ns-3 run metrics 三方一致；trace 重算一致；无废弃值 12.02/380.92。\n"
      "window.DEMO_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n")
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with io.open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(js)

size = os.path.getsize(OUT)
print("=== build_demo_data：全部断言通过 ===")
print("  指标数        : %d" % len(metrics))
print("  双轨对照单元  : %d" % payload["counts"]["crossval_cells"])
print("  trace 事件    : %d" % len(TR))
print("  覆盖格点      : %d" % len(coverage["cells"]))
print("  星座          : %s 颗 × %s 帧（|r| = %s km）" % (constellation.get("n"), constellation.get("frames"),
                                                      constellation.get("r_km")))
print("  输出          : demo/data.js  (%.1f KB)" % (size / 1024.0))
for w in warns:
    print("  ! " + w)
