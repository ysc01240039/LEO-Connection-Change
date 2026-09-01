"""指标评估（REPLACEABLE：指标口径由人定，AI 只计算）。
消费契约 14 列 trace（见 docs/仿真接口约定.md 第 2 节），
输出命名与 ns-3 轨 sim/ns3_io.compute_ns3_metrics() 完全一致，保证双轨指标同名可比。

口径（契约 2.1）：
- 接入成功率：合法终端成功接入 / 合法终端接入事件（伪造终端不计入成功率分母，
  其拦截情况由「伪造终端数 / 伪造终端拦截率」单独汇报，避免两类指标互相污染）；
- 接入时延：用户感知端到端时延 = (GRANT 完成时刻 − 首次发起时刻)，含退避/等待与握手全程；
  仅统计成功接入的合法终端（value_ms > 0）。
"""
import statistics as st


def _p95(x):
    if not x:
        return 0.0
    s = sorted(x)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def compute_metrics(trace: list) -> dict:
    access = [e for e in trace if e["event_type"] == "ACCESS"]
    ho = [e for e in trace if e["event_type"] == "HANDOVER"]

    # 合法终端与伪造终端分流（T4 口径，与 ns-3 轨 compute_ns3_metrics 同名同口径）
    forged = [e for e in access if int(e.get("forged", 0)) == 1]
    legit = [e for e in access if int(e.get("forged", 0)) == 0]

    delays = [float(e["value_ms"]) for e in legit if float(e["value_ms"]) > 0]
    succ = sum(1 for e in legit if e["result"] == "success")
    n = len(legit)

    metrics = dict()
    metrics["接入事件数"] = n
    metrics["接入成功率"] = round(succ / n, 4) if n else 0.0
    metrics["接入时延均值_ms"] = round(st.mean(delays), 2) if delays else 0.0
    metrics["接入时延P95_ms"] = round(_p95(delays), 2)

    if ho:
        inter = sorted(float(e["value_ms"]) for e in ho)
        nh = len(inter)
        elc = [float(e.get("ho_el_cost_deg", 0) or 0) for e in ho]
        metrics["切换事件数"] = nh
        metrics["切换中断均值_ms"] = round(sum(inter) / nh, 2)
        metrics["切换中断最大_ms"] = round(max(inter), 2)
        metrics["乒乓切换率"] = round(sum(1 for e in ho if int(e["pingpong"]) == 1) / nh, 4)
        metrics["预测失配率"] = round(sum(1 for e in ho if int(e["predict_mismatch"]) == 1) / nh, 4)
        metrics["仰角代价均值_deg"] = round(sum(elc) / nh, 2)
        metrics["仰角代价最大_deg"] = round(max(elc), 2)

    # T4 认证指标（伪造终端拦截，与 ns-3 轨 compute_ns3_metrics 同名同口径）
    if forged:
        metrics["伪造终端数"] = len(forged)
        metrics["伪造终端拦截率"] = round(
            sum(1 for e in forged if e["result"] == "fail") / len(forged), 4)

    dop = [abs(float(e["doppler_hz"])) for e in trace if float(e["doppler_hz"]) != 0.0]
    metrics["多普勒最大值_Hz"] = round(max(dop), 1) if dop else 0.0
    metrics["总事件数"] = len(trace)
    return metrics