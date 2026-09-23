"""接口契约读写（三方共用，字段写死在 docs/仿真接口约定.md）。

  Python -> ns-3 : scenario_config.json  （场景 + 真实可见窗，ns-3 侧可直接消费）
  ns-3  -> Python: access_trace.csv      （事件 trace，字段与契约一致）
  Python -> Web   : metrics.json         （统计结果，字段与 CSV 指标列对齐）

★ 审计修复（2026-09-02）★
契约由 14 列扩展为 16 列，新增 `auth_result` / `ebno_db`，
使认证判定结果与链路质量进入 trace，指标可审计（原仅在代码里判定，trace 无痕）。

★ 跨轨可比性（2026-09-23）★
契约由 16 列扩展为 17 列，新增 `service`（业务类型 voice/image/sms）。
原契约不带该列 → ns-3 侧写 trace 时业务类型丢失，`eval` 按 `sms` 回落，
T8 业务连续性无法按业务拆分（只能以 Python 轨为准）；补齐后两轨 T8 可比。
"""
import json
import csv
from datetime import datetime, timezone

# 契约 17 列（与 .ns3_ref/leo_access.cc 输出表头严格一致，勿改顺序）
TRACE_COLS = ["event_type", "terminal", "tag", "t_s", "serving_sat",
              "target_sat", "value_ms", "doppler_hz", "slant_km",
              "result", "predict_mismatch", "pingpong", "ho_el_cost_deg", "forged",
              "auth_result", "ebno_db", "service"]


def write_scenario_json(path, scenario, provenance, windows):
    obj = {"scenario": scenario, "source_provenance": provenance, "access_windows": windows}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def write_trace_csv(path, trace):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRACE_COLS, extrasaction="ignore")
        w.writeheader()
        for e in trace:
            w.writerow(e)


def write_metrics_json(path, metrics, platform=None, commit=None,
                       commit_full=None, run_id=None, scenario=None,
                       seed=None, auth_method=None):
    """写统计结果 JSON。

    ★ 存档合规（2026-09-16）★：自该日起在顶层注入《数据存档规范》§二要求的
    存档标签（platform / commit / run_id / scenario / produced_at），
    使结果文件本身即可定位仿真平台与代码版本。原有指标键完全不动，
    仅追加顶层字段，不影响任何下游读取与指标计算（纯格式，非方法修改）。

    ★ 认证三档口径合规（2026-09-16）★：追加 seed 与 auth_method 顶层字段，
    对齐《认证三档对比指标口径》§3「每次运行建议保留字段」。auth_method
    当前固定为 "hmac_dual_root"（项目仅实现本方案，见 sim/auth.py；无认证 /
    5G-AKA 未在仿真中实现，详见 results/AUTH_TIER_COMPLIANCE.md）。纯格式追加，
    不改任何仿真方法。
    """
    out = dict(metrics)  # 浅拷贝，不污染调用方
    if platform is not None:
        out["platform"] = platform
    if commit is not None:
        out["commit"] = commit          # 短哈希（≥7 位，可唯一定位）
    if commit_full is not None:
        out["commit_full"] = commit_full
    if run_id is not None:
        out["run_id"] = run_id
    if scenario is not None:
        out["scenario"] = scenario
    if seed is not None:
        out["seed"] = seed
    if auth_method is not None:
        out["auth_method"] = auth_method
    out["produced_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)