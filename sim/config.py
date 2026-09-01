"""中央配置：所有可调参数集中此处，便于替换/修改（不散落各模块）。"""
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
MASK_ANGLE_DEG = 25.0        # 最低仰角屏蔽角
ACCESS_PROC_MS = 3.0         # 星上接入处理时延（ms）
HO_LEAD_S = 20.0             # 预测切换提前量（s）
TICK_S = 1.0                 # 切换检测/判决周期（s）
CARRIER_FREQ_HZ = 2.0e9      # S 波段载波（灾害终端常用）
EIRP_DBM = 50.0              # 星上/终端等效全向辐射功率（模型假设）
GT_DBI_K = 10.0              # 接收 G/T（dB/K，模型假设）
NOISE_TEMP_K = 290.0         # 噪声温度（K）
REQUIRED_EBNO_DB = 6.0       # 解调所需 Eb/N0（dB，模型假设）
SPEED_OF_LIGHT = 299792458.0
