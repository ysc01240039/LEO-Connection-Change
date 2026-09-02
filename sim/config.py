"""中央配置：所有可调参数集中此处，便于替换/修改（不散落各模块）。

★ 审计修复（2026-09-02）★
原配置中若干常量无来源标注、且直接决定核心指标，已按下述原则整改：
1. 每个常量标注「来源」——分为【物理常量】【标准/文献】【建模假设】【实测】四类；
2. 决定核心结论的常量（如 step4_extra_ms）改为**由机理计算得出**，或拆为具名可引用参数；
3. 全部常量的溯源表内嵌于 HTML 报告（sim/viz.py 生成「常量溯源表」小节），报告中原「无硬编码」标签已删除。
"""
from pathlib import Path

# ---- 路径 ----
WORKSPACE = Path(r"E:/pytorchFile/NationalCreation1")
DATA_DIR = WORKSPACE / "data" / "sim"
DOCS_DIR = WORKSPACE / "docs"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---- 仿真时间窗（UTC）----
SIM_START_UTC = "2026-09-01T00:00:00Z"   # 真实历元起算点（用真实 TLE 对应时刻）
SIM_DURATION_S = 3600.0                   # 仿真时长（秒，与 ns-3 输入一致）
TIME_STEP_S = 15                          # 时间步长（秒，与 ns-3 星历采样一致）

# ---- 协议参数（★与 ns-3 侧 leo_access.cc 默认值严格一致★）----
MASK_ANGLE_DEG = 25.0        # 最低仰角屏蔽角【建模假设：3GPP NTN 常用 25°/30°】
ACCESS_PROC_MS = 3.0         # 星上接入处理时延(ms)【建模假设：待星上算力数据替换】
HO_LEAD_S = 20.0             # 预测切换提前量(s)【可扫描：决定中断是否为 0】
TICK_S = 1.0                 # 切换检测/判决周期(s)（ns-3 轨用）

# ---- 物理常量 ----
SPEED_OF_LIGHT = 299792458.0              # m/s【物理常量】
BOLTZMANN = 1.380649e-23                  # J/K【物理常量】

# ---- L2 链路预算（★审计修复：原模型缺比特率，Eb/N0 无量纲意义★）----
CARRIER_FREQ_HZ = 2.0e9      # S 波段载波【建模假设：灾害终端常用 S 波段】
# 链路预算三参数经重新标定（原 EIRP=50dBm / G·T=+10dB/K 组合使 Eb/N0 达 15~20 dB，
# 链路过于理想、BER 恒为 0，模型失去判别力）。现标定为：
#   天底(斜距 1200 km) Eb/N0 ≈ 11.9 dB；25° 掩角边缘(斜距 1964 km) ≈ 7.7 dB
EIRP_DBM = 68.0              # 卫星单载波 EIRP(dBm)=38 dBW【建模假设：低轨用户波束多载波分配后】
GT_DBI_K = -20.0             # 终端 G/T(dB/K)【建模假设：带小型定向天线的应急终端】
NOISE_TEMP_K = 290.0         # 噪声温度(K)【标准值】
BIT_RATE_BPS = 100e3         # 业务速率(bps)【建模假设：短报文+低码率图像】
BER_MODEL = "BPSK"           # BER 模型【建模假设】
LINK_MODEL_ON = True         # 链路模型开关：False 退化为「仅几何可见性」旧基线

# ---- T4 认证（真实 HMAC-SHA256，见 sim/auth.py）----
AUTH_MAC_BYTES = 4           # MAC 截断长度(字节) → 盲猜漏检概率 2^-32
AUTH_PSEUDO_BYTES = 4        # 假名长度(字节)
AUTH_MEASURE_SAMPLES = 20000  # auth_extra_ms 实测样本数
AUTH_CPU_DERATE = 1000.0     # 星上抗辐照 CPU 相对仿真宿主的降频系数【建模假设，可扫描】

# ---- RACH：四步基线的具名调度参数（★审计修复★）----
# 原 step4_extra_ms=400.0 为单一无源常量，且使两步/四步成功率完全相同。
# 现拆为：几何往返（由斜距实算）+ 两个具名 3GPP 调度定时器 + 前导竞争失败（机理）。
RAR_WINDOW_MS = 160.0        # RAR 响应窗口(ms)【文献待补：3GPP TS 38.321 ra-ResponseWindow】
CONTENTION_TIMER_MS = 200.0  # 竞争解决定时器(ms)【文献待补：3GPP TS 38.321 mac-ContentionResolutionTimer】
N_PREAMBLE = 64              # 每时隙可用前导码数【标准：LTE/NR 64】
# 兼容旧字段：step4_extra_ms 仅作为「无几何信息时的退化估计」保留，默认不再使用
STEP4_FALLBACK_MS = 360.0

# ---- 星历预测误差（★审计修复：使「预测式切换」可被证伪★）----
# 原模型使用完美未来窗口，预测永不失败 → 中断恒为 0。
# 现引入 TLE 老化导致的 LOS 预测偏差（零均值高斯，截断非负）。
EPHEM_ERR_S = 0.0            # 预测 LOS 的标准差(s)【可扫描：0=完美预测】

# ---- 切换：联合打分 + 迟滞 + 乒乓重定义（★审计修复★）----
HO_W_EL = 0.5                # 联合打分中仰角权重【可扫描】
HO_W_DWELL = 0.5             # 联合打分中剩余驻留权重【可扫描】
HO_HYST_DEG = 0.0            # 迟滞(度)：新目标得分需超出该门限才切换【可扫描】
PINGPONG_WINDOW_S = 60.0     # 乒乓判定窗口：窗口内切回曾服务过的星
PINGPONG_MIN_GAP_S = 30.0    # 相邻两次切换间隔低于此值计为频繁切换

# ---- 生存优先分级调度（★完成计划书「温度」创新点★）----
# 原实现仅给终端贴 high/med/low 标签（tag），但 RACH 拥塞下各 tier 平等竞争，
# 「指挥/救援终端优先接入」并未真正生效。现引入优先级感知 RACH：
#   · 高优(high)预留专用前导池，med/low 共用共享池 → 高优被碰撞阻塞概率更低；
#   · 高优退避更短 → 重发更快、接入时延更低。
# priority_on=False 时为「无优先级基线」（所有 tier 共用单池），用于量化增益。
PRIORITY_RESERVE_FRAC = {0: 0.5, 1: 0.3, 2: 0.2}   # 各优先级专用预留比例(high/med/low)【建模假设：可扫描】
PRIO_BACKOFF = {0: 0.3, 1: 0.6, 2: 1.0}  # 各优先级退避缩放(high 最快)【建模假设】
TIER_ORDER = {"high": 0, "med": 1, "low": 2}  # tag → 优先级(0=最高)

# ---- 科学版生存优先调度：SMDP/guard-channel 最优阈值（★2026-09-02 第4轮★）----
# 替代静态 PRIORITY_RESERVE_FRAC：每窗口用 Kaufman-Roberts 生灭过程解出最优预留
# (g_h,g_m)，使加权阻塞最小且高危阻塞 ≤ PRIO_EPS。详见 sim/prio_opt.py。
# priority_mode="static"（默认）：固定比例预留，保留为答辩可复现基线；
# priority_mode="dp"：在线自适应最优阈值（低高危负载时 g_h→0 回收闲置）。
PRIO_ADAPT_WIN_S = 1.0     # 阈值自适应窗口(s)【建模假设：星上重估周期】
PRIO_EPS = 0.12             # 高危阻塞率 QoS 上界(CAC 目标)【建模假设：≈static 高危阻塞，避免超额保护；可扫描】
PRIO_LOAD_CAL = 0.80         # 损失模型负载标定：模型不计重试跨时隙恢复→偏保守，乘此系数对齐仿真实测阻塞【建模假设：可扫描】
PRIO_BETA = 0.3             # EWMA 新窗口权重（0.3=较快跟踪负载变化）【建模假设】
PRIO_WEIGHTS = (1.0, 1.0)   # 目标函数中 (中危,低危) 权重（高危由 PRIO_EPS 约束保护）


