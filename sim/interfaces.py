"""接口契约读写（三方共用，字段写死在 docs/仿真接口约定.md）。

  Python -> ns-3 : scenario_config.json  （场景 + 真实可见窗，ns-3 侧可直接消费）
  ns-3  -> Python: access_trace.csv      （事件 trace，字段与契约一致）
  Python -> Web   : metrics.json         （统计结果，字段与 CSV 指标列对齐）

★ 审计修复（2026-09-02）★
契约由 14 列扩展为 16 列，新增 `auth_result` / `ebno_db`，
使认证判定结果与链路质量进入 trace，指标可审计（原仅在代码里判定，trace 无痕）。
"""
import json
import csv
from datetime import datetime, timezone

# 契约 16 列（与 .ns3_ref/leo_access.cc 输出表头严格一致，勿改顺序）
TRACE_COLS = ["event_type", "terminal", "tag", "t_s", "serving_sat",
              "target_sat", "value_ms", "doppler_hz", "slant_km",
              "result", "predict_mismatch", "pingpong", "ho_el_cost_deg", "forged",
              "auth_result", "ebno_db"]


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
                       commit_full=None, run_id=None, scenario=None):
    """写统计结果 JSON。

    ★ 存档合规（2026-09-16）★：自该日起在顶层注入《数据存档规范》§二要求的
    存档标签（platform / commit / run_id / scenario / produced_at），
    使结果文件本身即可定位仿真平台与代码版本。原有指标键完全不动，
    仅追加顶层字段，不影响任何下游读取与指标计算（纯格式，非方法修改）。
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
    out["produced_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)