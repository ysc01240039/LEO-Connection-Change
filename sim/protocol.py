"""协议参考实现（★SWAP POINT★：后续用 ns-3 模块整体替换本文件）。

设计（与 ns-3 leo_access.cc 逐条对应，保证双轨同口径）：
- 接入：两步接入握手，时延 = 2×传播时延(实时斜距/光速，skyfield) + 星上处理时延；
  选星 = 到达时刻瞬时仰角最高星（同 ns-3 AttemptAccess）。
- 切换：预测式切换，判决规则统一为「驻留稳定性优先 + 仰角兜底」：
    1) 决策时刻 t_ho = max(LOS − ho_lead, 连接建立时刻)；
    2) 候选池 = 全部在预测 LOS 之后仍可见的其他星（必须晚于服务星 LOS 才入选，
       否则候选先丢失 = 预测失败，直接排除）；
    3) 选优指标 = 未来驻留时长（los − t_ho）最大者（稳定优先，抑制乒乓）；
    4) 无晚于 LOS 的候选时，退化为「最早升起者」（产生中断，如实记录）。
- 指标口径（契约 2.1）：
    乒乓切换率 = 所选目标星剩余可见 < 60s（决策时刻起算）的事件占比；
    预测失配率 = 决策时刻选中的目标星 ≠ 服务星 LOS 时刻瞬时仰角最优可见星
                 的事件占比（度量"稳定优先"与"仰角最优"的系统性差异，诚实报告代价）；
    切换中断    = max(0, 目标星可连时刻 − 服务星 LOS)（先建后断 → 通常为 0）。
"""
import datetime as _dt
import random
import numpy as np
from skyfield.api import EarthSatellite, wgs84

from .config import (SIM_START_UTC, CARRIER_FREQ_HZ, SPEED_OF_LIGHT,
                     ACCESS_PROC_MS, HO_LEAD_S)

C_KM_S = SPEED_OF_LIGHT / 1000.0  # 299792.458 km/s


def run_protocol(access_windows, scenario, sats=(), ts=None, rng_seed: int = 20260901):
    """输入：access_windows(每星真实可见窗) + scenario(终端/突发/危险度) + sats/ts(实时几何源)。
    输出：trace 事件列表（契约 12 列，类型 ACCESS / HANDOVER）。
    """
    random.seed(rng_seed)
    n_terminals = scenario["terminals"]
    burst_start = scenario["burst_start_s"]
    burst_ramp = scenario["burst_ramp_s"]
    danger = scenario["danger_tags"]
    access_proc_ms = scenario.get("access_proc_ms", ACCESS_PROC_MS)
    # ---- T4 认证 / RACH 基线 / 突发碰撞参数（与 scenario.py 及 ns-3 同参）----
    forged_ratio = scenario.get("forged_ratio", 0.0)
    auth_extra_ms = scenario.get("auth_extra_ms", 0.0)
    rach_steps = scenario.get("rach_steps", 2)              # 2=两步预补偿; 4=Rel-17 四步基线
    step4_extra_ms = scenario.get("step4_extra_ms", 400.0)  # 四步附加时延（RAR+竞争解决）
    collision_on = scenario.get("collision_on", False)
    rach_capacity = scenario.get("rach_capacity", 1)        # 每 10ms 时间片星上可受理上限
    retry_interval_ms = scenario.get("retry_interval_ms", 500.0)
    retry_max = scenario.get("retry_max", 20)               # 碰撞重试上限（超限判失败）
    extra_delay_ms = auth_extra_ms + (step4_extra_ms if rach_steps >= 4 else 0.0)

    # ---- 卫星对象缓存 + 单点几何计算（与 orbit.py 同源公式）----
    t0 = _dt.datetime.fromisoformat(SIM_START_UTC.replace("Z", "+00:00"))
    observer = wgs84.latlon(scenario["lat"], scenario["lon"], scenario["alt_m"])
    sat_obj = {}
    for name, l1, l2 in sats:
        try:
            sat_obj[name] = EarthSatellite(l1, l2, name)
        except Exception:
            continue
    _geom_cache = {}

    def _time_at(rel_s):
        return ts.from_datetime(t0 + _dt.timedelta(seconds=float(rel_s)))

    def _geom_core(name, rel_s):
        sat = sat_obj.get(name)
        if sat is None or ts is None:
            return (0.0, 0.0, 0.0)
        try:
            diff = sat - observer
            g = diff.at(_time_at(rel_s))
            p = np.array(g.position.km)
            v = np.array(g.velocity.km_per_s)
            slant = float(np.linalg.norm(p))
            if slant == 0:
                return (0.0, 0.0, 0.0)
            radial = float(np.dot(p, v) / slant)
            dop = -CARRIER_FREQ_HZ * (radial * 1000.0) / SPEED_OF_LIGHT
            delay_ms = slant / C_KM_S * 1000.0
            return (dop, slant, delay_ms)
        except Exception:
            return (0.0, 0.0, 0.0)

    def _geom(name, rel_s):
        key = (name, round(float(rel_s), 1))
        v = _geom_cache.get(key)
        if v is None:
            v = _geom_core(name, key[1])
            _geom_cache[key] = v
        return round(v[0], 1), round(v[1], 3), round(v[2], 3)

    _el_cache = {}

    def _el_deg(name, rel_s):
        """时刻瞬间仰角（skyfield altaz，与 ns-3 leo_access.cc 的 ECEF elevationDeg 同秩）。
        契约 2.1 中「选星」与「预测失配」判据均使用此刻瞬时仰角（而非整窗峰值），
        确保与 ns-3 轨完全同口径。"""
        key = (name, round(float(rel_s), 1))
        v = _el_cache.get(key)
        if v is None:
            sat = sat_obj.get(name)
            if sat is None or ts is None:
                v = 0.0
            else:
                try:
                    v = float((sat - observer).at(_time_at(key[1])).altaz()[0].degrees)
                except Exception:
                    v = 0.0
            _el_cache[key] = v
        return v

    segs = sorted(access_windows, key=lambda w: w["aos_s"])
    total_dur = max((w["los_s"] for w in segs), default=0) + 60.0

    def visible_at(t):
        return [w for w in segs if w["aos_s"] <= t <= w["los_s"]]

    def next_visible_after(t):
        cands = [w for w in segs if w["aos_s"] > t]
        return min(cands, key=lambda w: w["aos_s"]) if cands else None

    def _access_geom(name, t):
        dop, slant, d_ms = _geom(name, t)
        delay_ms = 2.0 * d_ms + access_proc_ms
        return dop, slant, round(delay_ms, 3)
    _load = {}  # (sat, slot) -> 已受理请求数

    def _slot_ok(t, sat):
        if not collision_on:
            return True
        key = (sat, int(t / 0.01))
        n = _load.get(key, 0)
        if n >= rach_capacity:
            return False
        _load[key] = n + 1
        return True

    n_forged = 0
    n_forged_blocked = 0

    def _mk_access(k, tag, t, sat, value_ms, doppler, slant, result, forged=False):
        return {"event_type": "ACCESS", "terminal": k, "tag": tag,
                "t_s": round(t, 3),
                "serving_sat": sat, "target_sat": sat,
                "value_ms": value_ms, "doppler_hz": doppler, "slant_km": slant,
                "result": result, "predict_mismatch": 0, "pingpong": 0,
                "ho_el_cost_deg": 0.0, "forged": 1 if forged else 0}

    def arr_of(k):
        """终端到达时刻：突发窗口 [burst_start, burst_start+burst_ramp] 内均匀抽样
        （与 ns-3 主程序 burstT = burstStart + U(0,burstWin) 同口径）。"""
        return burst_start + random.uniform(0, burst_ramp)

    trace = []
    # ---- 接入阶段：时间有序离散事件队列（与 ns-3 Simulator 调度同序）----
    # 终端所有随机量（tag / 到达时刻 / 伪造标记）仍在第一遍按终端序号抽取，
    # 保证抽样统计同源；接入尝试与碰撞重试按「时刻」入队处理，消除旧实现
    # 「按终端序号顺序处理」与真实时间序不一致造成的拥塞统计偏差。
    import heapq
    events = []                     # 小顶堆：(t, seq, kind, k, tag, arr0)
    term_attempts = {}              # k -> 已碰撞次数（超 retry_max 判失败）
    term_failed = set()             # 已终结（成功/失败）终端，防御重复事件
    seq = 0

    # 第一遍：抽取每终端的 tag / 到达时刻 / 伪造标记（随机流与旧实现一致）
    first_pass = []
    for k in range(n_terminals):
        r = random.random()
        if r < danger.get("high", 0):
            tag = "high"
        elif r < danger.get("high", 0) + danger.get("med", 0):
            tag = "med"
        else:
            tag = "low"
        arr0 = arr_of(k)
        first_pass.append((k, tag, arr0))

    # 第二遍：伪造终端立即拦截（不占信道，时间=发起时刻）；合法终端入队
    for k, tag, arr0 in first_pass:
        is_forged = random.random() < forged_ratio
        if is_forged:
            n_forged += 1
            n_forged_blocked += 1
            trace.append(_mk_access(k, tag, arr0, -1, -1.0, 0.0, 0.0,
                                    "fail", forged=True))
            term_failed.add(k)
        else:
            events.append((arr0, seq, "acc", k, tag, arr0))
            term_attempts[k] = 0
            seq += 1

    # 第三遍：按时间序弹事件（接入尝试 / 碰撞重试 / 可见性等待）
    while events:
        t, _, typ, k, tag, arr0 = heapq.heappop(events)
        if k in term_failed:
            continue
        if typ == "acc":
            # 可见性：当前不可见 → 等待下一颗最近升起星（等待计入端到端时延）
            vis = visible_at(t)
            if not vis:
                nx = next_visible_after(t)
                if nx is None or nx["aos_s"] >= total_dur:
                    term_failed.add(k)
                    trace.append(_mk_access(k, tag, t, -1, -1.0, 0.0, 0.0, "fail"))
                    continue
                events.append((nx["aos_s"], seq, "acc", k, tag, arr0)); seq += 1
                continue
            # 选到达时刻瞬时仰角最高星（与 ns-3 AttemptAccess 同口径）
            best = max(vis, key=lambda w: _el_deg(w["sat"], t))
            if not _slot_ok(t, best["sat"]):
                # 碰撞/拥塞：重试超限判失败；否则均匀退避后重试（同 ns-3）
                if term_attempts[k] >= retry_max:
                    term_failed.add(k)
                    trace.append(_mk_access(k, tag, t, -1, -1.0, 0.0, 0.0, "fail"))
                    continue
                term_attempts[k] += 1
                backoff = random.uniform(0, retry_interval_ms) / 1000.0
                events.append((t + backoff, seq, "acc", k, tag, arr0)); seq += 1
                continue
            # 受理成功（占用该时隙容量）→ 握手
            term_failed.add(k)
            dop, slant, handshake = _access_geom(best["sat"], t)
            handshake = round(handshake + extra_delay_ms, 3)
            grant_t = t + handshake / 1000.0
            value_ms = round((t - arr0) * 1000.0 + handshake, 3)
            acc_event = _mk_access(k, tag, grant_t, best["sat"], value_ms,
                                   dop, slant, "success")
            trace.append(acc_event)
            connect_sat, connect_t = best["sat"], grant_t
        else:
            continue

        # ---- 预测式切换（与 ns-3 leo_access.cc 同一判决规则）----
        # 当前服务窗
        cur = next((w for w in segs if w["sat"] == connect_sat
                    and w["aos_s"] <= connect_t <= w["los_s"]), None)
        while cur is not None:
            los_t = cur["los_s"]
            t_ho = max(los_t - HO_LEAD_S, connect_t)  # 决策时刻（剩余 ≤ ho_lead 触发）

            # 候选 = 决策时刻已可见（aos ≤ t_ho，同 ns-3 候选 A 条件）+ LOS 晚于当前星
            #         的重叠覆盖星（先建后断，中断≈0）
            #         + 最早升起的下一颗星（兜底，产生中断则如实记录）
            overlap = [w for w in visible_at(los_t)
                       if w["sat"] != cur["sat"] and w["aos_s"] <= t_ho + 1e-9]
            nxt = next_visible_after(los_t)

            if overlap:
                # 稳定优先：重叠候选中驻留最长者（los 最晚 → 未来切换最少）
                best = max(overlap, key=lambda w: w["los_s"])
                start_connect = los_t
                interrupt = 0.0
                pingpong = 1 if (best["los_s"] - t_ho) < 60.0 else 0
            elif nxt is not None:
                # 覆盖盲区：等最早升起星，中断如实记录
                best = nxt
                start_connect = nxt["aos_s"]
                interrupt = max(nxt["aos_s"] - los_t, 0)
                pingpong = 1 if (best["los_s"] - los_t) < 60.0 else 0
            else:
                break  # 全仿真无后续可见星

            # 防御：候选 LOS 不晚于当前星 LOS → 空转，终止链
            if best["los_s"] <= los_t + 1e-9:
                break

            # 预测失配（契约 2.1）：决策选中 vs 执行时刻（LOS）瞬时仰角最优（与 ns-3 同口径）
            vis_los = [w for w in visible_at(los_t) if w["sat"] != cur["sat"]]
            mismatch = 0
            el_cost = 0.0
            if vis_los:
                exec_best = max(vis_los, key=lambda w: _el_deg(w["sat"], los_t))
                mismatch = 1 if exec_best["sat"] != best["sat"] else 0
                # 仰角代价：仰角最优星瞬时仰角 − 选中星瞬时仰角（量化“稳定优先”的实质牺牲）
                el_cost = round(_el_deg(exec_best["sat"], los_t) - _el_deg(best["sat"], los_t), 2)

            dop, slant, _d = _geom(best["sat"], t_ho)
            trace.append({"event_type": "HANDOVER", "terminal": k, "tag": tag,
                          "t_s": round(t_ho, 3), "serving_sat": cur["sat"],
                          "target_sat": best["sat"],
                          "value_ms": round(interrupt * 1000.0, 3),
                          "doppler_hz": dop, "slant_km": slant,
                          "result": "success",
                          "predict_mismatch": mismatch, "pingpong": pingpong,
                          "ho_el_cost_deg": el_cost, "forged": 0})
            # 推进：新服务星
            cur = best
            connect_sat, connect_t = best["sat"], start_connect
    return trace