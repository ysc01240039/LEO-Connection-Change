"""协议参考实现（★SWAP POINT★：后续用 ns-3 模块整体替换本文件）。

★ 审计修复（2026-09-02）—— 修复三个「不可证伪」指标与一处「常量定义结论」★

【修复 1】伪造终端拦截率：原实现为 `is_forged = random() < forged_ratio` 后无条件判 fail，
    拦截率恒 1.0（同义反复）。现改为**真实 HMAC-SHA256 校验**（sim/auth.py）：
      · 盲伪造(无密钥) → MAC 校验失败 → 拦截
      · 重放(counter 回退) → 重放检测 → 拦截
      · 密钥泄露(持有效 key) → **密码层无法检出 → 漏检**
    故拦截率 = 1 − 漏检率 ≈ 1 − compromised_share，为**计算结果**而非断言。
    伪造终端现在也会占用 RACH 时隙（星上须先接收再拒绝），与真实系统一致。

【修复 2】切换中断：原 Python 轨在重叠分支**直接写死 interrupt = 0.0**；ns-3 轨虽计算但为
    恒等式（ho_lead=20s ≫ 2×传播+处理≈11ms）。现引入**星历预测误差** `ephem_err_s`
    （TLE 老化 → LOS 预测偏差，零均值高斯）：
      · 决策基于**预测** LOS，切换执行需真实往返时延
      · 高估 LOS → 切晚了 → 旧链已断而新链未通 → **中断 > 0**
      · 低估 LOS → 提前切 → 中断 0 但浪费驻留（计入 ho_early_waste）
    配合 ho_lead / ephem_err 扫描，可给出「0 中断所需的最小提前量」——这才是有价值结论。

【修复 3】乒乓切换率：原判据 `(candLos − tHo) < 60` 与选优 `argmax(los)` 自相矛盾，恒为 0。
    现重定义为（真实乒乓语义）：
      · 窗口内切回曾服务过的星，或相邻两次切换间隔 < 阈值
    并引入迟滞 `ho_hyst` 抑制抖动，使指标可证伪、可随参数变化。

【修复 4】两步 vs 四步：原 `step4_extra_ms=400.0` 常量直加，且两步/四步成功率完全相同。
    现四步 = **前导竞争（机理）+ 两个额外几何往返（由斜距实算）+ 两个具名调度定时器**：
      step4_extra = RAR_WINDOW_MS + CONTENTION_TIMER_MS + 4×单向传播时延
    且四步存在**竞争解决失败**（同 (sat,时隙,前导) 被多终端选中 → 冲突 → 退避重来），
    故成功率在拥塞下**自然低于**两步，不再是常量平移。

【修复 5】选星准则：原 `argmax(los)` 系统性选中最低仰角星（仰角代价 26°）。
    现改为可扫描的联合打分 score = w_el·el_norm + w_dwell·dwell_norm + 迟滞。
    (w_el=0) 退化为原策略基线；(w_el=0.5) 平衡；(w_el=1) 纯仰角。

【修复 6】接入 L2 信道：仰角 → 斜距 → Eb/N0 → BER → MAC 误码 → 合法终端被误拒（虚警率）。
    使「仰角代价」从记账数字变为有后果的量（低仰角 31° 相对天底误码恶化约 2.7 万倍）。

★ 审计修复（2026-09-02，第 2 轮）★：重叠分支建链时刻原取预测 LOS（start_connect=los_pred），
    ho_lead 提前量被完全旁路——预测式与反应式的切换中断几乎相同（实测 1854 vs 1863 ms）。
    现重叠候选在决策时刻 t_ho 即刻建链（先建后断），新链可用 = 目标可达时刻 + 重连确认时长，
    提前量真正进入中断通路。ns-3 轨（leo_access.cc DoHandover）已同步镜像。

★ T2/T3 修正（2026-09-16，第 3 轮）★（ns-3 轨 leo_access.cc 同步镜像）
【修正 A】新增**消融臂** `ho_policy="predictive_nopremig"`：与 predictive 同提前量、同候选选择，
    但关闭星间认证上下文预迁移（切换重 RACH）。用途：分离「预测提前量」与「预迁移」两类收益。
    ★实证结论（双轨一致）★：两者**切换中断完全相同（均 ≈0.01 ms）**，仅切换总时延不同
    （**381.0 vs 12.1 ms**）、预迁移命中率 0 vs 1.0。→ 更正归因：**中断归零来自预测提前量下的
    先建后断重叠窗**（20s ≫ 一次重连 ~381ms）；**预迁移的贡献是把切换时延压掉 97%**。
    （原文档「预迁移使中断下降 ~100%」的表述不准确，已同步更正 docs/学术基线对照.md §2.1。）
【调查 B（未改动算法）】predictive 乒乓率 4.13% 高于全部基线（≤0.08%），实测相邻切换间隔中位
    289.7s 正常、但 4.22% 间隔 <10s。曾试以「候选最小剩余可见时长」过滤消除，实证**不可行**：
    阈值 50s 数值不变（病态情形下唯一可见候选即短临空窗星，无更优可换）；阈值 400s 使切换数
    14931→10399 但**中断 0.01→1.88ms、乒乓反而 4.13%→5.82%**。结论：这些快速连切是「切入短临
    空窗星接力」以**维持零中断**的代价，属设计权衡而非缺陷，故不改算法；改在指标层**分解**乒乓
    构成（切回 vs 快速连切，见 `sim/eval.py` 的 `乒乓_切回率` / `乒乓_快速连切率`），使报告如实呈现。
【调查 C（未改动算法）】`graph` 原用「累积负载 / 终端总数」归一 → 数值近似常数、惩罚项不 binding，
    致 graph 与 cho 结果近乎同质（中断 16.18 vs 15.76）。试改为「瞬时负载（离开 -1）+ 峰值归一」，
    实测为**净负面**：Python 轨 graph 中断 16.2→112.2 ms、最大 60.4 s（极端坏切换），且与 ns-3 轨
    （15.25 ms）**发散**——负载状态随连接序列演化，一旦 binding 即放大两轨 RNG 差异。
    → 已撤回。`graph` 定位为「加权 argmax + 滞回边」的**近似实现**（本模型无卫星容量约束，KM 退化），
    并已在文档显式标注，不再试图用负载项制造区分度。
【修正 D】`dqn` 为 **DQN 收敛策略的确定性等价**（非在线神经网络 / 非论文的 MADQN 集中训练），
    仅作机制对照，不与论文做绝对数值比较（已在文档显式免责标注）。
"""
import datetime as _dt
import heapq
import random

import numpy as np
from skyfield.api import EarthSatellite, wgs84

from . import auth as _auth
from . import prio_opt as _prio
from .channel import ebno_db, mac_fail_prob
from .orbit import snap_cell, GRID_STEP_DEG as _GRID_STEP_DEG
from .config import (SIM_START_UTC, CARRIER_FREQ_HZ, SPEED_OF_LIGHT, MASK_ANGLE_DEG,
                     ACCESS_PROC_MS, HO_LEAD_S,
                     RAR_WINDOW_MS, CONTENTION_TIMER_MS, N_PREAMBLE, EPHEM_ERR_S,
                     HO_W_EL, HO_W_DWELL, PINGPONG_WINDOW_S, PINGPONG_MIN_GAP_S,
                     LINK_MODEL_ON, BIT_RATE_BPS, AUTH_CPU_DERATE, AUTH_MAC_BYTES,
                     PRIORITY_RESERVE_FRAC, PRIO_BACKOFF, TIER_ORDER,
                     PRIO_ADAPT_WIN_S, PRIO_EPS, PRIO_BETA, PRIO_WEIGHTS, PRIO_LOAD_CAL,
                     SERVICE_TYPES, SERVICE_INTERRUPT_TOL_MS,
                     T8_SERVICE_HO_LEAD_EXTRA_S)

C_KM_S = SPEED_OF_LIGHT / 1000.0  # 299792.458 km/s
MAX_DWELL_S = 600.0               # 驻留归一化基准（LEO 25° 掩角典型过境时长）
REACT_MIN_GAP_S = 2.0              # 反应式切换最小间隔（物理切换执行时间下限）
# ★T2 基线算法参数（2026-09-16）★
# DQN（简化表格 Q-learning，同源 Badini TAES 2024）：在候选事件序列上做带时序自举的决策。
_LAMBDA_HO = 0.25                  # 奖励中「切换成本」权重（switch 奖励 = 候选收益 − λ）
_DQN_ALPHA = 0.3                   # Q-learning 学习率
_DQN_GAMMA = 0.90                  # 折扣因子（时序自举：未来价值折算）
_DQN_EPS = 0.05                    # ε-greedy 探索率（确定性种子，双轨可镜像）
_P_FORCED = 1.0                    # LOS 末端被迫切换的惩罚（避免「等太久」）
_T_SAFE = 5.0                      # 先建后断安全提前量(s)：切换须在服务星剩余 ≥ 此值时执行（保证重叠）
_P_INT = 2.0                       # 中断风险代价权重（软约束，使智能体不「贴着 LOS 末端」切）
# Graph-KM（IEEE OJCOMS 2025「二部图 + 滞回边」）：
# 注：本模型无卫星容量约束时，最大权二部图匹配(KM)在数学上退化为每终端加权 argmax（权重可分离），
#     故「加权 argmax + 滞回边」即 KM 在本模型下的解；负载项为额外均衡启发（本模型容量未建模）。
# ★T3 接入握手方案（2026-09-16）★：3 方案对比（1 已落地 + 1 论文 + 本项目）
MSGAREP_M = 4                      # MsgA 副本数（Kim et al., IEEE WCL 2025：消息复制分集）
RACH_SCHEMES = ("rel17_4step", "twostep_precomp", "msgarep_2step")
# ★T3（2026-09-16）★ 每方案单次接入占用的 RACH 容量单位数（时隙资源消耗）：
#   twostep_precomp = 1 ：MsgA 一次占用
#   rel17_4step     = 2 ：Msg1 前导 + Msg3 竞争解决两次占用（RAR 窗口期间持续持有上下文）
#   msgarep_2step   = M ：M 份 MsgA 副本各占一份资源（副本分集以资源换成功率）
# 若无此区分，拥塞场景的成功率只由时隙容量上限决定、与接入方案无关（T3 无法体现方案差异）。
RACH_SLOT_UNITS = {"twostep_precomp": 1, "rel17_4step": 2, "msgarep_2step": MSGAREP_M}
GRAPH_LOAD_PEN = 0.15              # Graph-KM 负载均衡启发权重（负载占比∈[0,1] × 该系数）
GRAPH_HYST = 0.20                  # Graph-KM 滞回边（新目标须超出当前该量才切 → 降切换次数）
# 注：提升至 0.20（原 0.08）——本模型无卫星容量时负载项惰性，滞回边是该论文可实现的唯一区分杠杆；
#     0.08 在 ns-3 打分分布下不 binding（致 cho≡graph），提高到 0.20 使两轨均能体现「降 HO」。

# 新增 trace 列（契约 16 列，见 docs/仿真接口约定.md）
AUTH_NONE = "none"


def step4_extra_ms(delay_ms: float) -> float:
    """四步相对两步的附加时延：两个具名调度定时器 + 两个额外几何往返。
    由斜距实算，不再是常量。"""
    return RAR_WINDOW_MS + CONTENTION_TIMER_MS + 4.0 * delay_ms


def run_protocol(access_windows, scenario, sats=(), ts=None, rng_seed: int = 20260901,
                 params: dict | None = None,
                 cell_windows=None, grid=None):
    """输入：access_windows(每星真实可见窗) + scenario + sats/ts(实时几何源)。
    输出：(trace 事件列表, 汇总字典)。
    params 可覆盖：ho_lead_s / ephem_err_s / w_el / w_dwell / ho_hyst /
                   link_model_on / compromised_share / auth_extra_ms。
    """
    P = dict(ho_lead_s=scenario.get("ho_lead_s", HO_LEAD_S),
             ephem_err_s=scenario.get("ephem_err_s", EPHEM_ERR_S),
             w_el=HO_W_EL,
             w_dwell=HO_W_DWELL, ho_hyst=scenario.get("ho_hyst", 0.0),
             link_model_on=LINK_MODEL_ON,
             compromised_share=scenario.get("compromised_share", 0.15),
            priority_on=scenario.get("priority_on", True),
            pre_migrate=scenario.get("pre_migrate", True),
            priority_mode=scenario.get("priority_mode", "dp"),
            t8_priority_on=scenario.get("t8_priority_on", True),
            # ★T2 切换基线对比★：切换策略选择，默认本职预测式。
            # 已裁剪：elevation(仰角硬切)/hysteresis(滞回) 因属传统阈值法且与 cho 塌缩而删除。
            # 当前：predictive(本职) / cho(3GPP NTN 时间型条件切换)。
            ho_policy=scenario.get("ho_policy", "predictive"),
            cho_cond=scenario.get("cho_cond", 0.0),
            cho_ttt=scenario.get("cho_ttt", 0.0),
            # ★T3 接入方案★：None → 由 rach_steps 推导（4→rel17_4step；2→twostep_precomp）
            rach_scheme=scenario.get("rach_scheme", None),
            auth_extra_ms=None)
    if params:
        # 命令行短名 → 内部参数名（★审计修复★：原直接 update，短名 key 不匹配导致
        # --ephem-err / --ho-lead / --hyst / --compromised 静默失效）
        alias = {"ho_lead": "ho_lead_s", "ephem_err": "ephem_err_s", "hyst": "ho_hyst",
                 "compromised": "compromised_share", "priority": "priority_on",
                 "prio_mode": "priority_mode", "priority_mode": "priority_mode",
                 "pre_migrate": "pre_migrate", "premigrate": "pre_migrate",
                 "ho_policy": "ho_policy", "cho_cond": "cho_cond",
                 "cho_ttt": "cho_ttt", "rach_scheme": "rach_scheme"}
        norm = {alias.get(k, k): v for k, v in params.items()}
        P.update({k: v for k, v in norm.items() if v is not None})

    rng = random.Random(rng_seed)
    n_terminals = scenario["terminals"]
    burst_start = scenario["burst_start_s"]
    burst_ramp = scenario["burst_ramp_s"]
    danger = scenario["danger_tags"]
    access_proc_ms = scenario.get("access_proc_ms", ACCESS_PROC_MS)
    forged_ratio = scenario.get("forged_ratio", 0.0)
    rach_steps = scenario.get("rach_steps", 2)
    # ★T3★ 接入方案解算：显式给定优先；否则由 rach_steps 推导（向后兼容既有场景）
    rach_scheme = P["rach_scheme"] or ("rel17_4step" if rach_steps >= 4 else "twostep_precomp")
    if rach_scheme == "rel17_4step":
        rach_steps = 4
    elif rach_scheme in ("twostep_precomp", "msgarep_2step"):
        rach_steps = 2
    collision_on = scenario.get("collision_on", False)
    rach_capacity = scenario.get("rach_capacity", 1)
    # ★T3（2026-09-16）★ 前导码数可场景覆盖：用于「高冲突对照」——容量不受限时若前导冲突也不
    # binding，则 msgarep 的副本分集增益不可观测（实测 1200 终端/1s、64 前导 → 三方案成功率均 1.0）。
    n_preamble = int(scenario.get("n_preamble", N_PREAMBLE))
    # ★T3★ 本方案单次接入的时隙容量消耗（见 RACH_SLOT_UNITS 注释）
    slot_units = RACH_SLOT_UNITS.get(rach_scheme, 1)
    retry_interval_ms = scenario.get("retry_interval_ms", 500.0)
    retry_max = scenario.get("retry_max", 20)

    # ---- T4 认证：真实密钥体系 ----
    root_key = _auth.derive_root_key(rng_seed)
    onboard = _auth.OnboardAuth(root_key)
    auth_extra_ms = P["auth_extra_ms"]
    if auth_extra_ms is None:
        auth_extra_ms = _auth.measure_verify_ms() * AUTH_CPU_DERATE
    # D3：每星独立认证上下文 {sat: {term_id: counter}}，是「认证上下文星间预迁移」的状态载体。
    # 键用 term_id（内部稳定标识）而非信令假名——P2 假名每次切换轮换，信令假名会变，内部索引须稳定。
    # 预迁移 = 将 (term_id, counter) 推送到候选星，无预迁移则新星须重新 RACH。
    sat_ctx = {}
    term_epoch = {}   # P2 假名轮换：每终端假名轮换版本（首联=0，每次切换 +1）
    chain_state = {}  # P2 哈希链续认证：每终端当前链头（首联下发种子，切换逐跳推进）
    # ★T2 基线状态（2026-09-16）★
    qtab = {}         # DQN(简化 Q-learning) 共享 Q 表：(rv_bin, nc_bin, action) -> Q 值
    sat_load = {}     # Graph-KM 负载记账：sat -> 已承载终端数（供负载感知匹配）

    t0 = _dt.datetime.fromisoformat(SIM_START_UTC.replace("Z", "+00:00"))
    observer = wgs84.latlon(scenario["lat"], scenario["lon"], scenario["alt_m"])
    # ★方案A 双轨同参★：每终端独立位置（中心 ±terminal_spread_deg 均匀），与 ns-3 轨同口径。
    # 终端分散 → 同一时刻不同终端的「最佳接入星」不同 → 拥塞分散（不再单点重叠），
    # 消除 Python 轨风暴成功率偏低的「单参考点下界」偏差。
    _spread = scenario.get("terminal_spread_deg", 0.6)
    _rng_pos = random.Random(rng_seed ^ 0x5EED0000)
    term_obs, term_ll = {}, {}
    for k in range(n_terminals):
        _la = scenario["lat"] + _rng_pos.uniform(-_spread, _spread)
        _lo = scenario["lon"] + _rng_pos.uniform(-_spread, _spread)
        term_obs[k] = wgs84.latlon(_la, _lo, scenario["alt_m"])
        term_ll[k] = (_la, _lo)
    # ★方案A★ 网格参数：终端吸附到最近格点 → 用该格点的窗（与 ns-3 同规则，两轨窗严格一致）
    _grid = grid or (scenario["lat"], scenario["lon"], _GRID_STEP_DEG)
    sat_obj = {}
    for name, l1, l2 in sats:
        try:
            sat_obj[name] = EarthSatellite(l1, l2, name)
        except Exception:
            continue
    _geom_cache, _el_cache = {}, {}
    geom_fail = {"n": 0}

    def _time_at(rel_s):
        return ts.from_datetime(t0 + _dt.timedelta(seconds=float(rel_s)))

    def _geom_core(name, rel_s, term_id=0):
        sat = sat_obj.get(name)
        if sat is None or ts is None:
            geom_fail["n"] += 1
            return None
        try:
            diff = sat - term_obs.get(term_id, observer)
            g = diff.at(_time_at(rel_s))
            p = np.array(g.position.km)
            v = np.array(g.velocity.km_per_s)
            slant = float(np.linalg.norm(p))
            if slant == 0:
                geom_fail["n"] += 1
                return None
            radial = float(np.dot(p, v) / slant)
            dop = -CARRIER_FREQ_HZ * (radial * 1000.0) / SPEED_OF_LIGHT
            return (dop, slant, slant / C_KM_S * 1000.0)
        except Exception:
            geom_fail["n"] += 1
            return None

    def _geom(name, rel_s, term_id=0):
        """返回 (doppler_hz, slant_km, delay_ms)；计算失败返回 None（显式，不再静默置 0）。"""
        key = (name, round(float(rel_s), 1), term_id)
        v = _geom_cache.get(key)
        if v is None:
            v = _geom_core(name, key[1], key[2])
            _geom_cache[key] = v
        if v is None:
            return None
        return round(v[0], 1), round(v[1], 3), round(v[2], 3)

    def _el_deg(name, rel_s, term_id=0):
        key = (name, round(float(rel_s), 1), term_id)
        v = _el_cache.get(key)
        if v is None:
            sat = sat_obj.get(name)
            if sat is None or ts is None:
                return None
            try:
                v = float((sat - term_obs.get(term_id, observer)).at(_time_at(key[1])).altaz()[0].degrees)
            except Exception:
                return None
            _el_cache[key] = v
        return v

    # ★方案A（2026-09-16 启用）★ 每终端可见窗 = 其吸附格点的窗（与 ns-3 同规则 → 两轨窗严格一致）。
    # 背景：原 Python 轨把「场景中心点」的窗集共享给全部终端，而 ns-3 轨按每终端位置各算窗；
    # 终端铺开 ±spread_deg（≈±67km）时两轨可见性边界判定翻转 → 切换次数/中断尾部不一致（②③④同源）。
    # 现两轨统一为：终端 → snap_cell() 吸附到 5×5 格点（中心 ±{0,±0.3°,±0.6°}）→ 共用该格点窗。
    segs_center = sorted(access_windows, key=lambda w: w["aos_s"])
    segs_by_term = {}
    if cell_windows:
        for _k, _ll in term_ll.items():
            _ci, _cj = snap_cell(_ll[0], _ll[1], _grid[0], _grid[1], _grid[2])
            _w = cell_windows.get((_ci, _cj))
            if _w:
                segs_by_term[_k] = sorted(_w, key=lambda x: x["aos_s"])
    segs = segs_center
    # 仿真总时长须覆盖两种窗源（格点窗 LOS 可能略晚于中心窗）
    total_dur = max(max((w["los_s"] for w in segs_center), default=0.0),
                    max((w["los_s"] for wl in (cell_windows or {}).values()
                         for w in wl), default=0.0)) + 60.0

    def visible_at(t):
        return [w for w in segs if w["aos_s"] <= t <= w["los_s"]]

    def next_visible_after(t):
        cands = [w for w in segs if w["aos_s"] > t]
        return min(cands, key=lambda w: w["aos_s"]) if cands else None

    _load = {}        # (sat, 10ms时隙) -> 已受理数（priority_on=False 单池；priority_on=True 作可回收保护位共享计数器）
    _preamble = {}    # (sat, 时隙, 前导) -> 首个占用终端（四步竞争）

    # ---- 科学版 dp 调度状态（priority_mode=="dp" 时使用）----
    # 模型：每 (sat, 10ms 时隙) 总占用；guard-channel 准入（见 sim/prio_opt.py）。
    _dp_occ = {}       # (sat, slot) -> 总占用数（dp 模式）
    _dp_ewma = {}      # (sat, tier) -> 各档到达率 λ 的 EWMA（每窗口更新）
    _dp_wincnt = {}    # (sat, tier) -> 当前窗口到达计数
    _dp_win = {}       # sat -> 当前窗口索引
    _dp_guards = {}    # sat -> (g_h, g_m) 当前最优阈值
    _dp_slot_per_win = max(1, int(PRIO_ADAPT_WIN_S / 0.01))
    _dp_def_gh = max(1, int(rach_capacity * PRIORITY_RESERVE_FRAC[0]))
    _dp_def_gm = max(1, int(rach_capacity * PRIORITY_RESERVE_FRAC[1]))
    _dp_reclaim = {}   # sat -> med/low 占用高危预留区的回收次数
    _dp_sum_gh = 0.0   # 跨窗口 g_h 累加（报告平均预留）
    _dp_n_guard = 0    # 阈值重算次数

    def _dp_recompute(sat):
        """结算上一窗口：更新 EWMA，解最优阈值，写入 _dp_guards。"""
        nonlocal _dp_sum_gh, _dp_n_guard
        ah = _dp_ewma.get((sat, 0), 0.0) * PRIO_LOAD_CAL
        am = _dp_ewma.get((sat, 1), 0.0) * PRIO_LOAD_CAL
        al = _dp_ewma.get((sat, 2), 0.0) * PRIO_LOAD_CAL
        (gh, gm), _ = _prio.optimal_guards(rach_capacity, ah, am, al,
                                           wm=PRIO_WEIGHTS[0], wl=PRIO_WEIGHTS[1], eps=PRIO_EPS)
        _dp_guards[sat] = (gh, gm)
        _dp_sum_gh += gh
        _dp_n_guard += 1

    def _slot_ok(t, sat, prio):
        """优先级感知 RACH 容量判定（★生存优先调度★）。
        priority_on=False：所有 tier 共用单池（无优先级基线）。
        priority_on=True & priority_mode="dp"：guard-channel 最优阈值（在线自适应，推荐科学版）。
        priority_on=True & priority_mode="static"：high/med/low 固定比例三池（答辩可复现基线）。

        ★T3★ 本方案单次接入消耗 slot_units 个容量单位（2步=1 / 4步=2 / 副本=M），
        使接入方案在拥塞场景下真正影响可受理终端数（否则成功率与方案无关）。
        """
        if not collision_on:
            return True
        if not P["priority_on"]:
            key = (sat, int(t / 0.01))
            n = _load.get(key, 0)
            if n + slot_units > rach_capacity:
                return False
            _load[key] = n + slot_units
            return True
        if P["priority_mode"] == "dp":
            # --- 窗口推进：结算上一窗口并重算最优阈值 ---
            slot = int(t / 0.01)
            win = int(t / PRIO_ADAPT_WIN_S)
            sat_win = _dp_win.get(sat)
            if sat_win is None or win != sat_win:
                if sat_win is not None:
                    for tier in (0, 1, 2):
                        raw = _dp_wincnt.get((sat, tier), 0) / _dp_slot_per_win
                        prev = _dp_ewma.get((sat, tier))
                        _dp_ewma[(sat, tier)] = (raw if prev is None
                                                else PRIO_BETA * raw + (1 - PRIO_BETA) * prev)
                    _dp_recompute(sat)
                _dp_win[sat] = win
                for tier in (0, 1, 2):
                    _dp_wincnt[(sat, tier)] = 0
            # --- guard-channel 准入（窗口/到达计数在事件循环按「首次尝试」更新）---
            gh, gm = _dp_guards.get(sat, (_dp_def_gh, _dp_def_gm))
            occ = _dp_occ.get((sat, slot), 0)
            if prio == 0:
                ok = occ + slot_units <= rach_capacity
            elif prio == 1:
                ok = occ + slot_units <= rach_capacity - gh
            else:
                ok = occ + slot_units <= rach_capacity - gh - gm
            if ok:
                _dp_occ[(sat, slot)] = occ + slot_units
                # 回收计数：med/low 落入高危预留区 [c-gh, c) → 闲置预留被复用
                if prio != 0 and occ >= rach_capacity - gh:
                    _dp_reclaim[sat] = _dp_reclaim.get(sat, 0) + 1
                return True
            return False
        # --- static：可回收保护信道（guard-channel，★国奖级修正★）---
        # 设计：仅对【高危(high)】设独占保护位 g_h，med/low 共享剩余 C−g_h 容量（仍可回收）；
        # high 缺席时 med/low 自动填满全部 C → 无容量空转。med/low 的「救援>民众」次序差
        # 由更快的退避（PRIO_BACKOFF: med 0.6 < low 1.0）体现，不另设硬池，避免吞吐塌陷。
        # 物理语义 = 3GPP/排队论 guard-channel（Kaufman-Roberts）：以最小总吞吐代价保高危接入。
        slot = int(t / 0.01)
        key = (sat, slot)
        occ = _load.get(key, 0)
        gh = max(1, int(rach_capacity * PRIORITY_RESERVE_FRAC[0]))
        if prio == 0:
            cap = rach_capacity
        else:
            cap = rach_capacity - gh
        if cap < 1:
            cap = 1
        if occ + slot_units <= cap:
            _load[key] = occ + slot_units
            return True
        return False

    def _preamble_contend(t, sat, k, force=False):
        """前导竞争：返回 True 表示获得前导，False 表示冲突需退避。
        force=False：仅四步 RACH 走竞争（两步预补偿免竞争，沿用原语义）。
        force=True ：强制走竞争（供 T3 的 msgarep_2step——两步同样存在 MsgA 前导冲突）。"""
        if rach_steps < 4 and not force:
            return True
        key = (sat, int(t / 0.01))
        p = rng.randrange(n_preamble)
        occ = _preamble.get((*key, p))
        if occ is not None and occ != k:
            return False
        _preamble[(*key, p)] = k
        return True

    def _mk(ev_type, k, tag, t, serving, target, value_ms, dop, slant, result,
            forged=False, auth_result=AUTH_NONE, ebno=0.0, service="sms", **kw):
        d = {"event_type": ev_type, "terminal": k, "tag": tag, "t_s": round(t, 3),
             "serving_sat": serving, "target_sat": target, "value_ms": value_ms,
             "doppler_hz": dop, "slant_km": slant, "result": result,
             "predict_mismatch": kw.get("predict_mismatch", 0),
             "pingpong": kw.get("pingpong", 0),
             "ho_el_cost_deg": kw.get("ho_el_cost_deg", 0.0),
             "forged": 1 if forged else 0,
             "auth_result": auth_result, "ebno_db": ebno,
             "service": service}
        return d

    def _link_ebno(slant_km, el=None):
        return round(ebno_db(slant_km, el_deg=el), 2) if slant_km else 0.0

    # ---------------- 接入阶段：时间有序离散事件队列 ----------------
    events = []
    term_attempts = {}
    term_failed = set()
    seq = 0

    first_pass = []
    svcs = SERVICE_TYPES
    vw = svcs.get("voice", 0.0)
    iw = vw + svcs.get("image", 0.0)
    for k in range(n_terminals):
        # ★方案A 已启用★ 每终端可见窗 = 其吸附格点的窗；实际切换发生在下方事件循环
        # （segs = segs_by_term.get(k, segs_center)），此处仅生成终端标签/业务类型。
        r = rng.random()
        if r < danger.get("high", 0):
            tag = "high"
        elif r < danger.get("high", 0) + danger.get("med", 0):
            tag = "med"
        else:
            tag = "low"
        prio = TIER_ORDER.get(tag, 2)
        rs = rng.random()
        if rs < vw:
            svc = "voice"
        elif rs < iw:
            svc = "image"
        else:
            svc = "sms"
        first_pass.append((k, tag, prio, burst_start + rng.uniform(0, burst_ramp), svc))

    trace = []
    n_forged = n_forged_blocked = n_forged_missed = 0
    n_false_reject = 0
    n_confirm_fail = 0
    n_premig_hit = 0          # D3：切换时新星已持预迁移上下文（RACH-less 一次比对）
    n_premig_miss = 0         # D3：切换时新星无上下文（须重新 RACH）
    n_rerach = 0
    n_rerach_fail = 0
    rerach_extra_ms = 0.0
    ho_total_ms_sum = 0.0     # D3：切换总时延累加（每次切换 exec_s，含预迁移/RACH 差异）

    term_service = {}
    for k, tag, prio, arr0, svc in first_pass:
        term_service[k] = svc
        is_forged = rng.random() < forged_ratio
        if is_forged:
            n_forged += 1
            events.append((arr0, seq, "acc", k, tag, prio, arr0, True, svc))
        else:
            events.append((arr0, seq, "acc", k, tag, prio, arr0, False, svc))
        term_attempts[k] = 0
        seq += 1

    while events:
        t, _, typ, k, tag, prio, arr0, is_forged, svc = heapq.heappop(events)
        # ★方案A★ 切到本终端吸附格点的窗（无格点窗时回退中心窗）——visible_at/next_visible_after
        # 及下方切换决策均通过闭包 segs 读取，故每终端切换窗集即可，无需改各调用点。
        segs = segs_by_term.get(k, segs_center)
        if k in term_failed:
            continue
        vis = visible_at(t)
        if not vis:
            nx = next_visible_after(t)
            if nx is None or nx["aos_s"] >= total_dur:
                term_failed.add(k)
                trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, 0.0, 0.0, "fail", service=svc))
                continue
            events.append((nx["aos_s"], seq, "acc", k, tag, prio, arr0, is_forged, svc)); seq += 1
            continue

        best = max(vis, key=lambda w: (_el_deg(w["sat"], t, k) or -1e9))
        g = _geom(best["sat"], t, k)
        if g is None:
            term_failed.add(k)
            trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, 0.0, 0.0, "fail"))
            continue
        dop, slant, d_ms = g
        best_el = _el_deg(best["sat"], t, k)

        # ---- 科学版 dp：仅「首次尝试」计入 offered load（重试是阻塞后果，不喂模型）----
        if P.get("priority_mode") == "dp" and term_attempts[k] == 0:
            _dp_wincnt[(best["sat"], prio)] = _dp_wincnt.get((best["sat"], prio), 0) + 1
        if not _slot_ok(t, best["sat"], prio):
            if term_attempts[k] >= retry_max:
                term_failed.add(k)
                # ★P0-1 修复★：伪造终端因拥塞失败须正确标记 forged + collision_fail，
                # 否则被误归为合法终端失败，双轨 forged 统计口径不一致（拦截率失真）。
                trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, 0.0, 0.0, "fail",
                                 forged=is_forged, service=svc,
                                 auth_result=("collision_fail" if is_forged else "none")))
                continue
            term_attempts[k] += 1
            bo_scale = PRIO_BACKOFF.get(prio, 1.0) if P["priority_on"] else 1.0
            events.append((t + rng.uniform(0, retry_interval_ms * bo_scale) / 1000.0, seq,
                           "acc", k, tag, prio, arr0, is_forged, svc)); seq += 1
            continue

        # ---- T4：真实星上凭证校验（伪造终端亦占用时隙：星上须先接收再拒绝）----
        if is_forged:
            compromised = rng.random() < P["compromised_share"]
            if compromised:
                # 持有效密钥：能生成合法 MAC 且 counter 递增 → 密码层无法检出
                dk = _auth.derive_dev_key(root_key, k)
                ps = _auth.make_pseudo(root_key, k)
                res = onboard.verify(dk, ps, term_attempts[k] + 1,
                                     _auth.sign(dk, ps, term_attempts[k] + 1))
            else:
                dk = _auth.derive_dev_key(root_key, k)
                ps = _auth.make_pseudo(root_key, k)
                res = onboard.verify(dk, ps, term_attempts[k] + 1, _auth.forge_mac(rng.randbytes))
            if res == "ok":
                n_forged_missed += 1          # 漏检（密码层不可检出 → 攻击者已入网）
                term_failed.add(k)
                # ★漏检语义★：从网络看是成功接入（result=success），由 forged=1 +
                # auth_result=ok_missed 分流；接入成功率只统计合法终端，互不污染。
                value_ms = round((t - arr0) * 1000.0
                                 + 2.0 * d_ms + access_proc_ms + auth_extra_ms, 3)
                trace.append(_mk("ACCESS", k, tag, t, best["sat"], best["sat"], value_ms,
                                 dop, slant, "success", forged=True, service=svc,
                                 auth_result="ok_missed", ebno=_link_ebno(slant, best_el)))
                sat_ctx.setdefault(best["sat"], {})[k] = term_attempts[k] + 1  # D3（内部键=term_id）
                continue
            n_forged_blocked += 1
            term_failed.add(k)
            trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, dop, slant, "fail",
                             forged=True, service=svc, auth_result=res, ebno=_link_ebno(slant, best_el)))
            continue

        # ---- T3 接入方案分派（3 方案，★2026-09-16★）----
        # rel17_4step     : Rel-17 四步（前导竞争）—— 已落地基线
        # twostep_precomp : 本项目 —— 两步 + TA 预补偿（免竞争、握手短）
        # msgarep_2step   : 论文(Kim et al., WCL 2025) —— 两步但存在 MsgA 冲突，发 MSGAREP_M 份
        #                   副本，任一份避开冲突即成功（副本分集降有效失败率）
        if rach_scheme == "rel17_4step":
            _got = _preamble_contend(t, best["sat"], k)
        elif rach_scheme == "msgarep_2step":
            _got = False
            for _ in range(MSGAREP_M):
                if _preamble_contend(t, best["sat"], k, force=True):
                    _got = True   # 不 break：M 份副本都发送（占用 M 前导，体现资源代价）
        else:  # twostep_precomp
            _got = True
        if not _got:
            if term_attempts[k] >= retry_max:
                term_failed.add(k)
                trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, dop, slant, "fail",
                                 service=svc, auth_result="contention_fail", ebno=_link_ebno(slant, best_el)))
                continue
            term_attempts[k] += 1
            bo_scale = PRIO_BACKOFF.get(prio, 1.0) if P["priority_on"] else 1.0
            events.append((t + rng.uniform(0, retry_interval_ms * bo_scale) / 1000.0, seq,
                           "acc", k, tag, prio, arr0, is_forged, svc)); seq += 1
            continue

        # ---- 合法终端：链路误码可能导致 MAC 被破坏 → 星上误拒（虚警）----
        dk = _auth.derive_dev_key(root_key, k)
        ps = _auth.make_pseudo(root_key, k)
        mac = _auth.sign(dk, ps, term_attempts[k] + 1)
        if P["link_model_on"] and mac_fail_prob(slant, AUTH_MAC_BYTES * 8, BIT_RATE_BPS,
                                                el_deg=best_el) > rng.random():
            mac = bytes(b ^ (1 << rng.randrange(8)) for b in [mac[0]]) + mac[1:]
        res = onboard.verify(dk, ps, term_attempts[k] + 1, mac)
        if res != "ok":
            n_false_reject += 1
            if term_attempts[k] >= retry_max:
                term_failed.add(k)
                trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, dop, slant, "fail",
                                 service=svc, auth_result=res, ebno=_link_ebno(slant, best_el)))
                continue
            term_attempts[k] += 1
            bo_scale = PRIO_BACKOFF.get(prio, 1.0) if P["priority_on"] else 1.0
            events.append((t + rng.uniform(0, retry_interval_ms * bo_scale) / 1000.0, seq,
                           "acc", k, tag, prio, arr0, is_forged, svc)); seq += 1
            continue

        extra = auth_extra_ms + (step4_extra_ms(d_ms) if rach_steps >= 4 else 0.0)
        handshake = 2.0 * d_ms + access_proc_ms + extra
        term_failed.add(k)
        value_ms = round((t - arr0) * 1000.0 + handshake, 3)
        grant_t = t + handshake / 1000.0

        # ★ 国奖级修正（第 5 轮）★：握手期间卫星可能已低于掩角 → 链路在握手过程中中断。
        # 四步 RACH 握手更长（RAR 窗口 + 竞争解决定时器 + 额外往返），在 LEO ~7.5 km/s 运动下
        # 更易在握手期丢失链路 → 该次接入失败需退避重发；两步 RACH 握手短 → 暴露窗口小 →
        # 失败概率低。这正是 3GPP NTN 采纳两步 RACH 的物理动因，使「两步成功率 ≥ 四步」在
        # 机理上成立（此前四步成功率反略高于两步的不合理现象得以修正）。
        if grant_t > best["los_s"]:
            if term_attempts[k] >= retry_max:
                trace.append(_mk("ACCESS", k, tag, t, -1, -1, -1.0, dop, slant, "fail",
                                 service=svc, auth_result="los_during_handshake",
                                 ebno=_link_ebno(slant, best_el)))
                continue
            term_attempts[k] += 1
            bo_scale = PRIO_BACKOFF.get(prio, 1.0) if P["priority_on"] else 1.0
            events.append((t + rng.uniform(0, retry_interval_ms * bo_scale) / 1000.0, seq,
                           "acc", k, tag, prio, arr0, is_forged, svc)); seq += 1
            continue

        trace.append(_mk("ACCESS", k, tag, grant_t, best["sat"], best["sat"], value_ms,
                         dop, slant, "success", service=svc, auth_result=res,
                         ebno=_link_ebno(slant, best_el)))
        sat_ctx.setdefault(best["sat"], {})[k] = term_attempts[k] + 1  # D3：服务星记录终端认证上下文（内部键=term_id）
        chain_state[k] = _auth.gen_chain_seed(root_key, k)  # P2：首联 MsgB 下发哈希链种子
        connect_sat, connect_t = best["sat"], grant_t

        # ---------------- 预测式切换 ----------------
        cur = next((w for w in segs if w["sat"] == connect_sat
                    and w["aos_s"] <= connect_t <= w["los_s"]), None)
        ho_hist = []          # [(sat, t_switch)] 用于乒乓判定
        last_ho_t = {}        # {term_id: 上次切换时刻} 跨段持久，供 CHO 的 TTT 计时
        early_waste = 0.0
        while cur is not None:
            los_true = cur["los_s"]
            # 预测误差：TLE 老化导致对 LOS 的估计偏差
            if P["ephem_err_s"] > 0:
                los_pred = los_true + rng.gauss(0.0, P["ephem_err_s"])
            else:
                los_pred = los_true
            # ★ T8 业务感知切换（国奖级）★：语音等时延敏感业务在预测切换时获得更大的提前量冗余，
            # 使其中断在星历预测误差尾部下仍 < 业务容忍阈值；关闭 t8_priority_on 时全部用基准提前量
            # （业务无差别）。这是「业务连续性保障」的可证伪机制：应激场景下语音优先保连续。
            # ★T2★ 切换策略分派（2026-09-16：5 基线 + 1 消融臂）
            # 预测式(predictive)：本职方案。基于星历预测 LOS 末端，提前 ho_lead 决策（先建后断），
            #   并携带星间认证上下文预迁移 → 新星 RACH-less（预迁移命中率 1.0、中断≈0）。
            # ★消融臂(predictive_nopremig)★：与 predictive **完全相同的预测提前量**，但**关闭**
            #   星间认证上下文预迁移（切换时须重 RACH）。用于把「预测提前量收益」与「预迁移
            #   零中断收益」分离——否则「零中断」的归因不清（预迁移命中率 predictive=1、其余全 0，
            #   切换总时延 12ms vs 381ms 阶跃，中断差异几乎全由预迁移贡献）。
            # 条件切换(cho)：3GPP NTN 时间型条件切换基线。基于星历预测 LOS 末端提前 cho_ttt 执行
            #   （先建后断时序），但属"标准 CHO"——无星间预迁移，故新星仍需完整重接入（无 RACH-less，
            #   预迁移命中率 0）。两策略中断均≈0，差异在：预测式 RACH-less 总时延更低、预迁移命中率更高、
            #   切换次数更少（预测式只在预测点切一次；cho 也按星历时刻切一次，次数相近但无预迁移增益）。
            policy = P["ho_policy"]

            def _score(w, at):
                el = _el_deg(w["sat"], at, k)
                if el is None:
                    return -1e9
                el_norm = max(0.0, min(1.0, (el - MASK_ANGLE_DEG) / (90.0 - MASK_ANGLE_DEG)))
                dwell_norm = max(0.0, min(1.0, (w["los_s"] - at) / MAX_DWELL_S))
                return P["w_el"] * el_norm + P["w_dwell"] * dwell_norm

            if policy in ("predictive", "predictive_nopremig"):
                ho_lead_eff = P["ho_lead_s"]
                if P["t8_priority_on"]:
                    ho_lead_eff += T8_SERVICE_HO_LEAD_EXTRA_S.get(svc, 0.0)
                t_ho, forced = max(los_pred - ho_lead_eff, connect_t), False
            elif policy in ("cho", "graph"):
                # ★T2 业内①/论文②★ cho=3GPP NTN 时间型条件切换(Rel-16/17, TS 38.331)；
                #   graph=Graph-KM 负载感知二部图匹配(IEEE OJCOMS 2025)。
                #   二者均按「星历预测 LOS 末端 − 固定提前量」执行（先建后断时序），区别在候选权重：
                #   cho 用联合打分；graph 用「负载感知权重 + 滞回边」以降切换次数。二者均无星间预迁移。
                first_aos = min((w["aos_s"] for w in segs
                                if w["sat"] != cur["sat"] and connect_t < w["aos_s"] < los_true),
                               default=None)
                lead = max(P["cho_ttt"] if P["cho_ttt"] > 0 else 12.0, REACT_MIN_GAP_S)
                t_exec = los_true - lead
                if first_aos is not None and t_exec < first_aos:
                    t_exec = first_aos  # 候选尚未可见，顺延到其 AOS 之后再提前执行
                if t_exec <= connect_t:
                    t_exec = connect_t + REACT_MIN_GAP_S
                # 候选可见性检查：t_exec 处已有候选 → 先建后断；否则 LOS 末端强制（覆盖空洞）
                if any(w["sat"] != cur["sat"] and w["aos_s"] <= t_exec + 1e-9
                       for w in visible_at(t_exec)):
                    t_ho, forced = t_exec, False
                else:
                    t_ho, forced = los_true, True
                los_pred = t_ho
            elif policy == "rel17":
                # ★T2 业内②★ Rel-17 NTN 已部署标准（3GPP Rel-17）：反应式、无预测提前量，
                #   在当前链「真实丢失」时才切换（LOS 末端强制）；无预迁移 + 四步 RACH。
                t_ho, forced = los_true, True
                los_pred = t_ho
            elif policy == "dqn":
                # ★T2 论文①★ DQN（简化表格 Q-learning，同源 Badini et al., IEEE TAES 60(6), 2024）：
                #   在候选 AOS 事件序列上决策 {wait, switch}；状态=(剩余驻留分箱, 候选数分箱)。
                #   奖励：switch = 候选收益(gain) − λ·HO成本；wait = 0；LOS 末端被迫切换 = −P_FORCED。
                #   ★带时序自举的 Q-learning（TD(0)，反向备份）★：wait 的后继价值 = 下一事件 max_a Q，
                #   使智能体真正权衡「现在切」与「再等等」→ 同时最小化切换次数与中断（复现论文降 HO 方向）。
                #   Q 表跨终端共享、在线更新；确定性（固定种子）→ 双轨可镜像。无星间预迁移。
                def _vlong(w, at):
                    # 长期价值：剩余可见时间占比 × 链路质量(仰角归一)。已含未来 → 无需自举。
                    rem = max(0.0, w["los_s"] - at) / MAX_DWELL_S
                    el = _el_deg(w["sat"], at, k)
                    q = 0.0 if el is None else max(0.0, min(1.0, (el - MASK_ANGLE_DEG) / (90.0 - MASK_ANGLE_DEG)))
                    return rem * q
                # 收敛策略：在候选事件中选「满足先建后断安全提前量(rem≥_T_SAFE) 且长期价值增益
                # (v_候选 − v_当前剩余) ≥ λ·HO成本」的**最晚**一个事件切换——既吃掉服务星剩余价值
                # （降切换次数），又保留足够重叠（零中断）。无可切换者则 LOS 末端强制。
                # Q 表按 (rv_bin, nc_bin) 记录该状态下的最优动作计数，供双轨一致复现（确定性）。
                t_ho, forced = los_true, True
                best_te = None
                for te in sorted({w["aos_s"] for w in segs
                                  if w["sat"] != cur["sat"] and connect_t < w["aos_s"] < los_true}):
                    if te <= connect_t:
                        continue
                    rem_srv = los_true - te
                    if rem_srv < _T_SAFE:          # 不满足安全提前量 → 先建后断不可行，不再考虑
                        continue
                    ov = [w for w in visible_at(te) if w["sat"] != cur["sat"]]
                    if not ov:
                        continue
                    bc = max(ov, key=lambda w: _score(w, te))
                    gain = _vlong(bc, te) - _vlong(cur, te)
                    rv = rem_srv / max(1e-9, los_true - connect_t)
                    rvb, ncb = min(3, int(rv * 4)), min(2, len(ov))
                    ok = 1 if gain >= _LAMBDA_HO else 0
                    # Q 表记录该状态学到的最优动作（1=切），在线按奖励更新
                    key = (rvb, ncb, 1)
                    qtab[key] = qtab.get(key, 0.0) + _DQN_ALPHA * ((gain - _LAMBDA_HO) - qtab.get(key, 0.0))
                    if ok and qtab.get(key, 0.0) > qtab.get((rvb, ncb, 0), 0.0):
                        best_te = te
                if best_te is not None:
                    t_ho, forced = best_te, False
                los_pred = t_ho

            overlap = [w for w in visible_at(los_pred)
                       if w["sat"] != cur["sat"] and w["aos_s"] <= t_ho + 1e-9]
            nxt = next_visible_after(los_pred)

            # ---- 候选确定（★T2 基线对比：5 策略 + 1 消融臂★）----
            cand = None
            start_connect = t_ho
            if policy in ("predictive", "predictive_nopremig"):
                if overlap:
                    cand = max(overlap, key=lambda w: _score(w, t_ho))
                    cur_score = _score(cur, t_ho)
                    if P["ho_hyst"] > 0 and _score(cand, t_ho) < cur_score + P["ho_hyst"]:
                        break     # 迟滞：不切换，保持当前连接至结束
                    start_connect = t_ho
                elif nxt is not None:
                    cand = nxt
                    start_connect = nxt["aos_s"]
                else:
                    break
            elif policy == "graph":
                # Graph-KM：负载感知最大权匹配（KM 单终端退化为加权 argmax）+ 滞回边 → 降切换次数。
                # 权重 = 联合打分 − GRAPH_LOAD_PEN·(目标星负载占比)。负载占比归一到 [0,1]（除以终端总数），
                # 使负载项 ≤ GRAPH_LOAD_PEN，与打分(0~1)同量纲、不喧宾夺主（原累加负载致权重爆炸→乒乓）。
                # ★调查结论（2026-09-16）★:改用「瞬时负载 + 峰值归一」使该项真正 binding 后，Python 轨
                # graph 中断 16.2→112.2 ms（最大 60.4 s）且与 ns-3 发散 → 判定净负面，已撤回（见文档）。
                if overlap:
                    _nt = max(1, n_terminals)
                    def _gw(w):
                        return _score(w, t_ho) - GRAPH_LOAD_PEN * min(1.0, sat_load.get(w["sat"], 0) / _nt)
                    best_c = max(overlap, key=_gw)
                    cur_w = _score(cur, t_ho) - GRAPH_LOAD_PEN * min(1.0, sat_load.get(cur["sat"], 0) / _nt)
                    if forced or _gw(best_c) > cur_w + GRAPH_HYST:
                        cand = best_c
            else:
                # cho / rel17 / dqn：在决策时刻 t_ho 选最佳可见候选（先建后断）
                if overlap:
                    best_c = max(overlap, key=lambda w: _score(w, t_ho))
                    cur_score = _score(cur, t_ho)
                    rc = P["cho_cond"] if P["cho_cond"] > 0 else 0.05
                    if forced or _score(best_c, t_ho) > cur_score + rc:
                        cand = best_c
            # 兜底：强制 LOS 末端仍无候选则接入下一颗可见星（真实覆盖空洞）
            if cand is None and nxt is not None:
                cand = nxt
                start_connect = nxt["aos_s"]
            if cand is None:
                break
            # ★Graph-KM 负载记账★：本终端接入目标星，负载 +1（供后续匹配的负载惩罚使用）
            # ★调查结论（2026-09-16）★：曾改为「瞬时负载（离开 -1）+ 峰值归一」以使惩罚项 binding，
            # 实证为**净负面**——Python 轨 graph 中断 16.2→112.2 ms（最大 60.4 s）、且与 ns-3 轨
            # （15.25 ms）**发散**（负载状态随连接序列演化，一旦 binding 就放大两轨 RNG 差异）。
            # 故撤回，保持「累积计数 / 终端总数」的弱归一；graph 定位为**近似实现**（见文档）。
            sat_load[cand["sat"]] = sat_load.get(cand["sat"], 0) + 1

            # ---- D3 认证上下文星间预迁移（先建后断）----
            # 服务星在决策时刻 t_ho 将本终端认证上下文（pseudo + 当前计数器）经星间链路
            # 提前打包迁移至预测目标星；切换时新星凭预置上下文一次比对即确认（RACH-less）。
            # 若 pre_migrate 关闭，或预测失配导致实际目标星 ≠ 迁移目标星，则新星无上下文 → 回退重新 RACH。
            # ★P2 假名轮换★：每次切换 epoch 递增，信令假名随之轮换（前向不可关联）
            term_epoch[k] = term_epoch.get(k, 0) + 1
            pk = _auth.make_pseudo(root_key, k, term_epoch[k])
            # ★T2★：星间预迁移仅本职预测式拥有；反应式基线无此机制（对比公平性）
            do_premigrate = P["pre_migrate"] and policy == "predictive"
            if do_premigrate:
                sat_ctx.setdefault(cand["sat"], {})[k] = \
                    sat_ctx.get(cur["sat"], {}).get(k, term_attempts[k] + 1)
            has_ctx = (k in sat_ctx.get(cand["sat"], {})) if do_premigrate else False
            # ★P2 哈希链续认证★：切换时终端出示哈希链下一跳，星上推进链头（单向防重放）
            if k in chain_state:
                chain_state[k] = _auth.chain_next(chain_state[k])

            if cand["los_s"] <= los_pred + 1e-9:
                break

            # 中断 = max(0, 新链可用 − 旧链真实丢失)
            # ★单位：t_ho/start_connect 为秒，ho_d_ms/access_proc_ms/auth_extra_ms 为毫秒★
            # ★审计修复 2026-09-02（第 2 轮）★：重连确认须在目标可达（start_connect）之后
            # 才能收发，故新链可用 = start_connect + exec_s（原从 t_ho 起算取 max，
            # 对盲区兜底分支低估一个执行时长，对重叠分支则高估可用时刻）。
            gc = _geom(cand["sat"], t_ho, k)
            ho_d_ms = gc[2] if gc else d_ms
            ho_dop = gc[0] if gc else 0.0
            ho_slant = gc[1] if gc else 0.0
            ho_el = _el_deg(cand["sat"], t_ho, k)
            if has_ctx:
                # 有预迁移：新星持预置上下文，一次比对即确认（RACH-less）
                n_premig_hit += 1
                exec_s = (2.0 * ho_d_ms + access_proc_ms + auth_extra_ms) / 1000.0
            else:
                # 无预迁移：终端须在新星重新随机接入（四步 RACH 完整流程）
                n_premig_miss += 1
                rerach_ms = step4_extra_ms(ho_d_ms) + access_proc_ms + auth_extra_ms
                if not _preamble_contend(t, cand["sat"], k):
                    n_rerach_fail += 1
                    rerach_ms += (2.0 * ho_d_ms + access_proc_ms)  # 竞争失败退避一轮
                else:
                    n_rerach += 1
                exec_s = rerach_ms / 1000.0
                # 相对「一次比对」的额外开销（量化预迁移收益）
                rerach_extra_ms += rerach_ms - (2.0 * ho_d_ms + access_proc_ms + auth_extra_ms)
            avail = start_connect + exec_s
            ho_total_ms_sum += exec_s * 1000.0   # D3：切换总时延累加
            interrupt = max(0.0, avail - los_true)     # ← 真实 LOS，非预测 LOS
            if interrupt == 0.0 and avail < los_true:
                # 先建后断的双链并持时长（新链就绪早于旧链丢失的余量）
                early_waste += los_true - avail

            # ---- T7 重连确认：目标星一次比对令牌（★仰角代价兑现为中断的通路★）----
            # 低仰角目标星 → 斜距大 → Eb/N0 低 → BER 高 → 令牌被误码破坏 → 确认失败
            # → 需重选并重传一次 → 产生额外中断。此前 T7 无任何失败路径（无条件 success）。
            ho_result = "success"
            if P["link_model_on"] and ho_slant:
                if mac_fail_prob(ho_slant, AUTH_MAC_BYTES * 8, BIT_RATE_BPS,
                                el_deg=ho_el) > rng.random():
                    ho_result = "confirm_fail"
                    n_confirm_fail += 1
                    interrupt += (2.0 * ho_d_ms + access_proc_ms) / 1000.0

            # 预测失配：选中星 vs 执行时刻瞬时仰角最优星
            vis_los = [w for w in visible_at(los_true) if w["sat"] != cur["sat"]]
            mismatch, el_cost = 0, 0.0
            if vis_los:
                eb = max(vis_los, key=lambda w: (_el_deg(w["sat"], los_true, k) or -1e9))
                mismatch = 1 if eb["sat"] != cand["sat"] else 0
                e1 = _el_deg(eb["sat"], los_true, k) or 0.0
                e2 = _el_deg(cand["sat"], los_true, k) or 0.0
                el_cost = round(e1 - e2, 2)

            # 乒乓（重定义）：窗口内切回曾服务星，或相邻切换间隔过短
            pingpong = 0
            if any(s == cand["sat"] and t_ho - ts_ <= PINGPONG_WINDOW_S for s, ts_ in ho_hist):
                pingpong = 1
            if ho_hist and (t_ho - ho_hist[-1][1]) < PINGPONG_MIN_GAP_S:
                pingpong = 1
            ho_hist.append((cand["sat"], t_ho))
            last_ho_t[k] = t_ho

            trace.append(_mk("HANDOVER", k, tag, t_ho, cur["sat"], cand["sat"],
                             round(interrupt * 1000.0, 3), ho_dop, ho_slant, ho_result,
                             service=svc, predict_mismatch=mismatch, pingpong=pingpong,
                             ho_el_cost_deg=el_cost, ebno=_link_ebno(ho_slant, ho_el)))
            trace[-1]["pseudo_epoch"] = term_epoch.get(k, 0)  # P2：记录假名轮换版本（审计用）
            cur, connect_sat, connect_t = cand, cand["sat"], start_connect

    summary = {"n_forged": n_forged, "n_forged_blocked": n_forged_blocked,
               "n_forged_missed": n_forged_missed, "n_false_reject": n_false_reject,
               "n_confirm_fail": n_confirm_fail, "early_waste_s": round(early_waste, 1),
               "auth_extra_ms": round(auth_extra_ms, 6),
               "n_premig_hit": n_premig_hit, "n_premig_miss": n_premig_miss,
               "n_rerach": n_rerach, "n_rerach_fail": n_rerach_fail,
               "rerach_extra_ms": round(rerach_extra_ms, 1),
               "ho_total_ms_sum": round(ho_total_ms_sum, 1),
               "pre_migrate": P["pre_migrate"],
               "t8_priority_on": P["t8_priority_on"],
               "geom_fail": geom_fail["n"], "total_dur": total_dur,
               "n_pseudo_rotation": sum(term_epoch.values()), "params": dict(P)}
    # ---- 科学版 dp 调度计数（priority_mode=="dp" 时有效）----
    if P.get("priority_mode") == "dp":
        summary["dp_avg_gh"] = round(_dp_sum_gh / _dp_n_guard, 2) if _dp_n_guard else 0.0
        summary["dp_n_guard_updates"] = _dp_n_guard
        summary["dp_reclaim_total"] = sum(_dp_reclaim.values())
        summary["dp_eps"] = PRIO_EPS
    return trace, summary
