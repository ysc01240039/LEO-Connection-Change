"""灾害场景参数包（REPLACEABLE：直接改这里即可切换场景/终端分布/危险度）。
来源：AGENTS.md §0.2 场景五要素 + 计划书模块一。
注意：
- 终端数、突发强度、风暴占比为建模假设（非实测），显式声明以便审计；
- 所有参数与 ns-3 轨（leo_access.cc 命令行）及 data/sim/ns3_in/scenario.json 同参，
  双轨共用同一配置，保证交叉验证可比。"""

SCENARIOS = {
    "wenchuan": {
        "name": "汶川地震灾区",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,                 # 建模假设：灾区集中突发终端规模
        "burst_start_s": 5,                # 突发起始（与 ns-3 输入 burstStart 一致）
        "burst_ramp_s": 60,               # 突发窗口（与 ns-3 输入 burstWin 一致）
        "access_proc_ms": 3.0,            # 星上接入处理时延（与 ns-3 accessProcMs 一致）
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},  # 与 ns-3 terminals.csv tag 一致
        # ---- T4 认证（伪造终端/认证代价，双轨同参）----
        "forged_ratio": 0.05,             # 伪造终端占比（dev_sig 校验失败 → 拦截率）
        "auth_extra_ms": 1.0,             # 星上轻量凭证校验额外时延（EC-Schnorr 量级）
        # ---- RACH 基线（Rel-17 四步 vs 两步，双轨同参）----
        "rach_steps": 2,                  # 2=两步预补偿接入；4=Rel-17 四步 RACH 基线
        "step4_extra_ms": 400.0,          # 四步模式附加时延（RAR 等待+竞争解决，Rel-17 典型量级）
        # ---- 突发碰撞/拥塞（接入成功率不再恒为 1）----
        "collision_on": True,             # 启用碰撞模型（星上按 10ms 时间片限流）
        "rach_capacity": 64,              # 每 10ms 时间片星上前导码受理上限（NTN 单波束典型 64）
        "retry_interval_ms": 500.0,       # 碰撞后随机退避重试间隔上限（均匀抽样 ms）
        "retry_max": 5,                   # 碰撞重试上限（超限判失败）
        "note": "常规突发（60s 均匀涌入）：1200 终端低于 RACH 容量，拥塞模型验证性开启",
    },
    "wenchuan_storm2": {
        "name": "汶川地震灾区（呼叫风暴对照·两步接入）",
        "lat": 31.0083, "lon": 103.5833, "alt_m": 1326,
        "terminals": 1200,
        "burst_start_s": 5,
        "burst_ramp_s": 1,               # 风暴：全部终端 1s 内同步涌入（地震后集体呼救建模）
        "access_proc_ms": 3.0,
        "danger_tags": {"high": 0.20, "med": 0.35, "low": 0.45},
        "forged_ratio": 0.05,
        "auth_extra_ms": 1.0,
        "rach_steps": 2,
        "step4_extra_ms": 400.0,
        "collision_on": True,
        "rach_capacity": 4,              # 波束受损/窄带低容量：4 前导码/10ms 时隙
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "note": "呼叫风暴：1s 内 1200 终端涌入，10ms 时间片仅 4 前导码，量级超过容量 → 碰撞/拥塞显现",
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
        "auth_extra_ms": 1.0,
        "rach_steps": 4,                 # Rel-17 四步 RACH 基线（同风暴，做两步→四步相对提升对比）
        "step4_extra_ms": 400.0,
        "collision_on": True,
        "rach_capacity": 4,
        "retry_interval_ms": 500.0,
        "retry_max": 3,
        "note": "同 wenchuan_storm2 但走四步 RACH：附加 RAR+竞争解决时延，用于基线对比",
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
        "auth_extra_ms": 1.0,
        "rach_steps": 2,
        "step4_extra_ms": 400.0,
        "collision_on": True,
        "rach_capacity": 64,
        "retry_interval_ms": 500.0,
        "retry_max": 5,
        "note": "平原洪区，终端沿河道/安置点带状分布",
    },
}


def get_scenario(key: str = "wenchuan") -> dict:
    if key not in SCENARIOS:
        raise KeyError(f"未知场景 {key}，可选: {list(SCENARIOS)}")
    return SCENARIOS[key]