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


def write_metrics_json(path, metrics):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)