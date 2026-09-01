"""信道模型（REPLACEABLE：可换为 ns-3 物理层/更精细链路预算）。
全部由真实几何（距离、径向速度）计算；模型假设显式标注，便于审计与替换。"""
import math
from .config import (CARRIER_FREQ_HZ, SPEED_OF_LIGHT, EIRP_DBM, GT_DBI_K,
                     NOISE_TEMP_K, REQUIRED_EBNO_DB)


def doppler_hz(radial_velocity_km_s: float) -> float:
    """径向速度(km/s, 远离为正) -> 多普勒频偏(Hz)。"""
    return -CARRIER_FREQ_HZ * (radial_velocity_km_s * 1000.0) / SPEED_OF_LIGHT


def propagation_delay_s(slant_km: float) -> float:
    """单向传播时延(s)。"""
    return slant_km * 1000.0 / SPEED_OF_LIGHT


def link_budget_ebno_db(slant_km: float) -> float:
    """简化 Friis 链路预算，返回 Eb/N0(dB)（★模型假设，待 ns-3 PHY 替代★）。
    假设：自由空间路径损耗、固定 EIRP/G/T、热噪声、所需 Eb/N0 常数。"""
    path_loss = 20.0 * math.log10(4.0 * math.pi * slant_km * 1000.0 * CARRIER_FREQ_HZ / SPEED_OF_LIGHT)
    rx_power_dbm = EIRP_DBM - path_loss
    rx_power_dBW = rx_power_dbm - 30.0
    k_dbw_hz_k = 10.0 * math.log10(1.380649e-23)      # dBW/Hz/K
    noise_density_dBW_hz = k_dbw_hz_k + 10.0 * math.log10(NOISE_TEMP_K)
    eb_no = rx_power_dBW - noise_density_dBW_hz + GT_DBI_K - REQUIRED_EBNO_DB
    return round(eb_no, 2)
