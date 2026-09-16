"""灾害场景参数包（REPLACEABLE：直接改这里即可切换场景/终端分布/危险度）。

来源：AGENTS.md §0.2 场景五要素 + 计划书模块一。

★ 审计修复（2026-09-02）★
- 删除 `step4_extra_ms`：四步附加时延现由 `sim/protocol.step4_extra_ms()` 依斜距实算
  （RAR 窗口 + 竞争解决定时器 + 两个额外几何往返），不再是常量平移。
- 删除 `auth_extra_ms`：现由 `sim/auth.measure_verify_ms()` × 降频系数实测得出。
- 新增 `compromised_share`：伪造终端中「持有效密钥」的比例，决定**漏检率**。
  这是把拦截率从 1.0 同义反复变为可计算指标的关键参数。
- 新增 `ho_hyst`：切换迟滞，配合联合打分使乒乓率可证伪。
- 所有参数与 ns-3 轨（leo_access.cc 命令行）及 data/sim/ns3_in/scenario.json 同参。
- `terminal_spread_deg`（默认 0.6，见 protocol.py）：终端在中心 ±0.6° 均匀分布（★方案A 双轨同参★），
  与 ns-3 轨每终端独立位置口径一致；protocol.py 据此生成每终端 observer，
  消除「单参考点」导致的拥塞过度集中、风暴成功率偏低的下界偏差。
"""

SCENARIOS = {
    "wenchuan": {
        "name": "汶川地震灾区",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,                 # 建模假设：灾区集中突发终端规模
        "burst_start_s": 5,
        "burst_ramp_s": 60,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,              # 伪造终端占比
        "compromised_share": 0.15,         # 其中持有效密钥（密码层不可检出）的比例
        "rach_steps": 2,                   # 2=两步预补偿；4=Rel-17 四步基线
        "collision_on": True,
        "rach_capacity": 64,
        "retry_interval_ms": 500.0,
        "retry_max": 5,
        "ho_hyst": 0.0,
        "note": "常规突发（60s 均匀涌入）：1200 终端低于 RACH 容量，拥塞模型验证性开启",
    },
    "wenchuan_storm2": {
        "name": "汶川地震灾区（呼叫风暴对照·两步接入）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 2,
        "collision_on": True,
        "rach_capacity": 4,                # 波束受损/窄带低容量：4 前导码/10ms 时隙
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_hyst": 0.0,
        "note": "呼叫风暴：1s 内 1200 终端涌入，10ms 时间片仅 4 前导码 → 碰撞/拥塞显现",
    },
    # ---- T3 容量不受限对照（★2026-09-16★）----
    # 动因：窄带场景（rach_capacity=4）下 msgarep 的 ×M=4 资源开销抵消其副本分集增益，
    # 三方案成功率被「资源效率」主导，论文方法的本意（副本分集降低回退四步概率）看不出来。
    # 本场景把 rach_capacity 提高 64×（256），使**容量不再 binding**、失败只来自前导冲突
    # → 可观察 msgarep 相对 rel17_4step 的分集增益（二者均存在 MsgA/Msg1 前导冲突）。
    # 其余参数与 wenchuan_storm2 完全一致，保证对照只反映「容量」这一个变量。
    "wenchuan_storm2_wide": {
        "name": "汶川地震灾区（呼叫风暴·容量不受限对照）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 2,
        "collision_on": True,
        "rach_capacity": 256,             # ★容量不受限★：隔离「资源效率」与「分集增益」两个变量
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_hyst": 0.0,
        "note": "与 wenchuan_storm2 同负载，仅 rach_capacity 4→256：容量不 binding，"
                "失败只来自前导冲突 → msgarep 副本分集增益可见（论文本意的公平对照）",
    },
    # ---- T3 高冲突对照（★2026-09-16★）----
    # 动因：`wenchuan_storm2_wide`（容量不受限）下三方案成功率**均为 1.0**——说明 1200 终端/1s 与
    # 64 前导码的组合下**前导冲突也不 binding**。本场景把前导码降到 8，使前导冲突成为主导失败源。
    # ★实测结论（与预期相反，据实记录）★：msgarep 成功率**低于** rel17_4step
    #   （0.8025/0.7651 vs 0.9561/0.9424，双轨一致）。原因：本项目对副本分集采用**忠实资源记账**
    #   （M=4 份副本各占**独立**前导资源，见 `sim/protocol.py` 的 `_preamble_contend(force=True)` 与
    #   `RACH_SLOT_UNITS`）——副本的**资源外部性**（多占 4 份前导，加剧全体冲突）抵消了其**分集增益**。
    # 即：Kim et al. (WCL 2025) 的机制在「副本各占独立资源」这一忠实解释下，于本模型/本负载区间不占优。
    # 注：n_preamble 为**场景可覆盖**参数（原为 config 常量），仅用于隔离冲突这一个变量。
    "wenchuan_storm2_contend": {
        "name": "汶川地震灾区（呼叫风暴·高冲突对照）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 2,
        "collision_on": True,
        "rach_capacity": 256,             # 容量不受限（隔离「资源效率」变量）
        "n_preamble": 8,                  # ★高冲突★：每时隙仅 8 前导码，使前导冲突成为主导失败源
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_hyst": 0.0,
        "note": "容量不受限 + 前导码 8：隔离「前导冲突」变量。实测 msgarep 成功率低于 rel17_4step"
                "（0.80/0.77 vs 0.96/0.94）——副本各占独立前导的资源外部性抵消其分集增益",
    },
    "wenchuan_storm2_lowhigh": {
        "name": "汶川灾区（低高危负载·回收对照）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.05, "med": 0.35, "low": 0.60},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 2,
        "collision_on": True,
        "rach_capacity": 4,
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_hyst": 0.0,
        "note": "低高危(5%)：验证科学版 dp 调度在高危终端稀少时回收闲置预留，中/低危成功率↑而高危不变",
    },
    "wenchuan_storm4": {
        "name": "汶川地震灾区（呼叫风暴对照·Rel-17 四步 RACH 基线）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 4,
        "collision_on": True,
        "rach_capacity": 4,
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_hyst": 0.0,
        "note": "同 wenchuan_storm2 但走四步 RACH：前导竞争 + RAR/竞争解决定时器",
    },
    "henan": {
        "name": "河南暴雨灾区",
        "lat": 34.75, "lon": 113.62, "alt_m": 110,
        "terminals": 1000,
        "burst_start_s": 5,
        "burst_ramp_s": 60,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 2,
        "collision_on": True,
        "rach_capacity": 64,
        "retry_interval_ms": 500.0,
        "retry_max": 5,
        "ho_hyst": 0.0,
        "note": "平原洪区；注：当前终端分布为方形均匀抽样，尚未实现『沿河道带状分布』（见 P1）",
    },
    # ---- T8 业务连续性压力场景（★国奖级 T8 验证★）----
    # 设计：提前量被约束为 4s（紧切换决策，如高动态下 LOS 预测较晚触发），星历误差 5s。
    # 此时「无业务感知」（t8_priority_on=False，所有业务用同一 4s 提前量）的语音中断会
    # 因预测误差尾部超过 50ms 容忍而显著掉线；而本方案「业务感知切换」给语音 +12s 冗余，
    # 语音中断被压到 <50ms → 语音连续性 ≈99.9%。该对照直接验证「业务连续性保障(T8)」机制。
    "t8_stress": {
        "name": "应急切换压力场景（T8 业务连续性验证）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1500,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.03,
        "compromised_share": 0.15,
        "rach_steps": 2,
        "collision_on": True,
        "rach_capacity": 4,
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_lead_s": 4.0,                 # ★约束提前量：使预测误差尾部可超过语音容忍→语音掉线★
        "ephem_err_s": 5.0,               # 真实星历漂移
        "ho_hyst": 0.0,
        "pre_migrate": True,
        "priority_on": True,
        "priority_mode": "dp",
        "t8_priority_on": True,           # ★业务感知切换：语音获更大提前量冗余★
        "note": "切换压力：提前量仅 4s + 星历误差 5s；关闭 t8_priority_on 时语音连续性显著下降，开启后≈99.9%",
    },
    # ---- Rel-17 基线（对照）：标准四步 RACH + 反应式切换 + 无优先级 ----
    #   · rach_steps=4（Rel-17 标准四步接入，无两步预补偿）
    #   · ho_lead_s=0（反应式切换：在预测 LOS 时刻才切换，不做提前建链预测）
    #   · priority_on=False（无生存优先分级调度）
    "rel17_baseline": {
        "name": "Rel-17 基线（四步RACH+反应式切换，对照）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "compromised_share": 0.15,
        "rach_steps": 4,                  # Rel-17 标准四步
        "collision_on": True,
        "rach_capacity": 4,               # 与 storm 同窄带低容量，隔离「方案」效应
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "ho_lead_s": 0.0,                  # 反应式（无预测提前量）
        "ephem_err_s": 5.0,               # 真实星历漂移：反应式切换无法补偿 → 中断非零
        "ho_hyst": 0.0,
        "priority_on": False,             # 无生存优先
        "pre_migrate": False,            # ★受控对照：基线 = 纯 Rel-17，不含本项目的星间认证上下文预迁移(D3)★
        "note": "Rel-17 NTN 标准范式基线：四步 RACH + 反应式切换 + 无优先级 + 无星间预迁移；"
                "与 wenchuan_storm2(两步+预测+优先级) 同负载对照，量化本方案提升%",
    },
}


def get_scenario(key: str = "wenchuan") -> dict:
    if key not in SCENARIOS:
        raise KeyError(f"未知场景 {key}，可选: {list(SCENARIOS)}")
    return SCENARIOS[key]
