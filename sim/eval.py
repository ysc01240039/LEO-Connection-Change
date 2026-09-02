"""指标评估（REPLACEABLE：指标口径由人定，AI 只计算）。

★ 审计修复（2026-09-02）★
1. **单一实现**：原 `eval.compute_metrics` 与 `ns3_io.compute_ns3_metrics` 为两套近乎逐行的
   重复实现，靠注释「同名同口径」维系，实际已出现不一致（C++ 对 elCost 做 `<0→0` 截断，
   Python 未做）。现统一为本文件唯一实现，`ns3_io` 转类型后调用本函数。
2. **新增可证伪指标**：
     · 伪造终端漏检率 = 通过校验的伪造终端 / 伪造终端总数（原恒为 0，因无漏检建模）
     · 合法终端虚警率 = 被误拒的合法终端 / 合法终端总数（由信道误码引起）
     · 认证引入额外时延_ms = HMAC 实测 × 星上降频系数（原为无源常量 1.0）
     · 切换中断非零比例 / 最大值（原恒 0，现可非零）
3. 类型宽容：接受 ns-3 CSV 读出的字符串与 Python 轨的原生类型。

口径（契约 2.1）：
- 接入成功率：合法终端成功接入 / 合法终端接入事件；
- 接入时延：端到端 = (GRANT 完成时刻 − 首次发起时刻)，含退避/等待与握手全程，仅计成功事件。
"""
import statistics as st
from .config import PRIO_WEIGHTS


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _i(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _p95(x):
    if not x:
        return 0.0
    s = sorted(x)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def compute_metrics(trace: list, summary: dict | None = None) -> dict:
    """唯一指标实现。trace: list[dict]，字段见 interfaces.TRACE_COLS。"""
    access = [e for e in trace if e.get("event_type") == "ACCESS"]
    ho = [e for e in trace if e.get("event_type") == "HANDOVER"]

    forged = [e for e in access if _i(e.get("forged", 0)) == 1]
    legit = [e for e in access if _i(e.get("forged", 0)) == 0]

    delays = [_f(e["value_ms"]) for e in legit if _f(e["value_ms"]) > 0]
    succ = sum(1 for e in legit if e.get("result") == "success")
    n = len(legit)

    m = {}
    m["接入事件数"] = n
    m["接入成功率"] = round(succ / n, 4) if n else 0.0
    m["接入时延均值_ms"] = round(st.mean(delays), 2) if delays else 0.0
    m["接入时延P95_ms"] = round(_p95(delays), 2)

    if ho:
        inter = [_f(e["value_ms"]) for e in ho]
        nh = len(inter)
        elc = [_f(e.get("ho_el_cost_deg", 0)) for e in ho]
        m["切换事件数"] = nh
        m["切换中断均值_ms"] = round(sum(inter) / nh, 2)
        m["切换中断最大_ms"] = round(max(inter), 2)
        m["切换中断非零比例"] = round(sum(1 for x in inter if x > 0) / nh, 4)
        m["乒乓切换率"] = round(sum(1 for e in ho if _i(e.get("pingpong", 0)) == 1) / nh, 4)
        m["预测失配率"] = round(sum(1 for e in ho if _i(e.get("predict_mismatch", 0)) == 1) / nh, 4)
        m["仰角代价均值_deg"] = round(sum(elc) / nh, 2)
        m["仰角代价最大_deg"] = round(max(elc), 2)
        m["重连确认失败率"] = round(sum(1 for e in ho if e.get("result") == "confirm_fail") / nh, 4)
        eb = [_f(e.get("ebno_db", 0)) for e in ho if _f(e.get("ebno_db", 0)) != 0.0]
        if eb:
            m["切换目标星EbN0均值_dB"] = round(sum(eb) / len(eb), 2)
        # ---- D3 认证上下文预迁移量化 ----
        if summary:
            m["切换总时延均值_ms"] = round(summary.get("ho_total_ms_sum", 0.0) / nh, 2)
            m["切换重连额外时延_ms"] = round(summary.get("rerach_extra_ms", 0.0) / nh, 2)
            tot = summary.get("n_premig_hit", 0) + summary.get("n_premig_miss", 0)
            if tot:
                m["预迁移命中率"] = round(summary.get("n_premig_hit", 0) / tot, 4)

    # ---- T4 认证：可证伪指标（★审计修复★）----
    if forged:
        nf = len(forged)
        # 漏检 = 密钥泄露型伪造终端通过校验入网（auth_result=ok_missed）
        missed = sum(1 for e in forged if e.get("auth_result") == "ok_missed")
        # 拦截 = MAC 校验失败或重放被拒
        blocked = sum(1 for e in forged if e.get("auth_result") in ("bad_mac", "replay"))
        m["伪造终端数"] = nf
        m["伪造终端拦截率"] = round(blocked / nf, 4)
        m["伪造终端漏检率"] = round(missed / nf, 4)
    if legit:
        fr = sum(1 for e in legit if e.get("result") == "fail"
                 and e.get("auth_result") in ("bad_mac", "replay", "false_reject"))
        m["合法终端虚警率"] = round(fr / len(legit), 4)

    if summary:
        m["认证引入额外时延_ms"] = summary.get("auth_extra_ms", 0.0)
        if summary.get("n_forged"):
            m["伪造终端总数(抽样)"] = summary["n_forged"]
        if summary.get("geom_fail"):
            m["几何计算失败次数"] = summary["geom_fail"]

    dop = [abs(_f(e["doppler_hz"])) for e in trace if _f(e["doppler_hz"]) != 0.0]
    m["多普勒最大值_Hz"] = round(max(dop), 1) if dop else 0.0
    m["总事件数"] = len(trace)

    # ---- ② 生存优先分级调度：按 tier 的成功率/时延/拒绝数（★计划书「温度」创新点★）----
    m.update(tier_metrics(trace))

    # ---- 科学版 dp 调度指标：加权阻塞率 + 高危预留回收 ----
    ms = m.get("med危终端接入成功率")
    ls = m.get("low危终端接入成功率")
    if ms is not None and ls is not None:
        bm, bl = 1.0 - ms, 1.0 - ls
        wm, wl = PRIO_WEIGHTS[0], PRIO_WEIGHTS[1]
        m["加权阻塞率(中低危)"] = round((wm * bm + wl * bl) / (wm + wl), 4)
    if summary:
        if summary.get("dp_reclaim_total"):
            m["高危预留回收次数"] = summary["dp_reclaim_total"]
        if "dp_avg_gh" in summary:
            m["dp平均高危预留"] = summary["dp_avg_gh"]
        if summary.get("dp_eps") is not None:
            m["dp高危QoS上界"] = summary["dp_eps"]
    return m


def tier_metrics(trace: list) -> dict:
    """按危险度 tier（high/med/low）统计接入成功率、时延、拒绝数，
    并给出「生存优先增益」（high 相对 low 的成功率提升与时延降低%）。"""
    access = [e for e in trace if e.get("event_type") == "ACCESS"]
    legit = [e for e in access if _i(e.get("forged", 0)) == 0]
    out = {}
    for tier in ("high", "med", "low"):
        grp = [e for e in legit if e.get("tag") == tier]
        n = len(grp)
        if not n:
            continue
        succ = [e for e in grp if e.get("result") == "success"]
        delays = [_f(e["value_ms"]) for e in succ if _f(e["value_ms"]) > 0]
        rej = n - len(succ)
        out[f"{tier}危终端接入成功率"] = round(len(succ) / n, 4)
        out[f"{tier}危终端时延均值_ms"] = round(st.mean(delays), 2) if delays else 0.0
        out[f"{tier}危终端拒绝数"] = rej
    if "high危终端接入成功率" in out and "low危终端接入成功率" in out:
        hs, ls = out["high危终端接入成功率"], out["low危终端接入成功率"]
        hd, ld = out["high危终端时延均值_ms"], out["low危终端时延均值_ms"]
        out["生存优先_成功率差(high-low)"] = round(hs - ls, 4)
        out["生存优先_时延降低%(high-vs-low)"] = round((ld - hd) / ld * 100, 1) if ld else 0.0
    return out


def rel17_improvement(base: dict, prop: dict) -> dict:
    """计算本方案相对 Rel-17 基线的提升%（★计划书 §4.1/§十二 量化要求★）。

    比较项：(指标, 方向)；提升% 含义统一为「方案相对基线变好多少，正值=方案更优」：
    · 方向=降低（时延/中断/乒乓）：方案越低越好 → (基线−方案)/基线×100；
    · 方向=提升（成功率）：方案越高越好 → (方案−基线)/基线×100；
    · 方向=持平（认证拦截率，与接入范式无关）：标「持平」。
    基线为 0（如中断基线恰为 0）时无法算相对值，标「—」。

    ★ 修复 2026-09-02（第 3 轮）★：原实现对所有方向统一用 (基线−方案)/基线，
    导致「提升」类指标符号反转——方案成功率越高结果越负，把损失伪装成收益
    （如 storm2 真实为 −23.4%，旧公式错显 +23.4%）。现按方向分别取符号。
    """
    spec = [
        ("接入时延均值_ms", "降低"),
        ("接入成功率", "提升"),
        ("high危终端接入成功率", "提升"),
        ("切换中断均值_ms", "降低"),
        ("乒乓切换率", "降低"),
        ("伪造终端拦截率", "持平"),
    ]
    out = {}
    for k, direction in spec:
        b, p = base.get(k), prop.get(k)
        if b is None or p is None:
            out[k] = "—"
            continue
        if direction == "持平":
            out[k] = "持平"
            continue
        if not isinstance(b, (int, float)) or b == 0:
            out[k] = "—"
            continue
        if direction == "提升":
            out[k] = round((p - b) / b * 100, 1)
        else:
            out[k] = round((b - p) / b * 100, 1)
    return out


def merge_reps(all_metrics: list) -> dict:
    """多种子汇总：均值 + 标准差（原实现散落在 run_sim.py，现收敛于此）。"""
    if not all_metrics:
        return {}
    if len(all_metrics) == 1:
        return all_metrics[0]
    keys = [k for k in all_metrics[0]]
    out = {}
    for k in keys:
        vals = [m[k] for m in all_metrics if k in m]
        out[k] = round(st.mean(vals), 4) if vals else 0.0
        out[k + "_stdev"] = round(st.stdev(vals), 4) if len(vals) > 1 else 0.0
    return out
