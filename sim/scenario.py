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
    # ---- Rel-17 基线（对照）：标准四步 RACH + 反应式切换 + 无优先级 ----
    # 用于固化「相对 Rel-17 基线提升%」。与 wenchuan_storm2 同负载，仅改变接入/切换范式：
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
