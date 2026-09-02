"""信道模型（REPLACEABLE：可换为 ns-3 物理层/更精细链路预算）。

★ 审计修复（2026-09-02）★
1. 原模块**无任何调用者**（死代码），L2 信道层在仿真中不存在；现已接入 `protocol.py`，
   仰角经斜距 → 路径损耗 → Eb/N0 → BER → MAC 误码，最终影响接入成败。
2. 原 `link_budget_ebno_db` 缺少比特率项：
       Eb/N0 = C/N0 − 10·log10(Rb)
   缺 Rb 时"Eb/N0"实际是 C/N0，量纲错误（原值 66.8 dB 明显不合理）。已补全。
3. 新增 BER 模型与「MAC 误码导致合法终端被误拒」的概率，这是**虚警率**的物理来源，
   使"仰角代价"从记账数字变为有后果的量。
"""
import math

from .config import (CARRIER_FREQ_HZ, SPEED_OF_LIGHT, BOLTZMANN, EIRP_DBM,
                     GT_DBI_K, NOISE_TEMP_K, BIT_RATE_BPS, BER_MODEL)


def doppler_hz(radial_velocity_km_s: float) -> float:
    """径向速度(km/s, 远离为正) -> 多普勒频偏(Hz)。"""
    return -CARRIER_FREQ_HZ * (radial_velocity_km_s * 1000.0) / SPEED_OF_LIGHT


def propagation_delay_s(slant_km: float) -> float:
    """单向传播时延(s)。"""
    return slant_km * 1000.0 / SPEED_OF_LIGHT


def free_space_loss_db(slant_km: float, freq_hz: float = CARRIER_FREQ_HZ) -> float:
    """自由空间路径损耗(dB)：FSPL = 20·log10(4πd/λ)。"""
    return 20.0 * math.log10(4.0 * math.pi * slant_km * 1000.0 * freq_hz / SPEED_OF_LIGHT)


def cn0_db_hz(slant_km: float) -> float:
    """载波噪声比 C/N0 (dB-Hz) = EIRP − FSPL + G/T − 10log10(k·T)。"""
    rx_dbm = EIRP_DBM - free_space_loss_db(slant_km)
    rx_dbw = rx_dbm - 30.0
    noise_density_dbw_hz = 10.0 * math.log10(BOLTZMANN) + 10.0 * math.log10(NOISE_TEMP_K)
    return rx_dbw - noise_density_dbw_hz + GT_DBI_K


def ebno_db(slant_km: float, bit_rate_bps: float = BIT_RATE_BPS) -> float:
    """Eb/N0 (dB) = C/N0 − 10·log10(Rb)。★已补全比特率项★"""
    return cn0_db_hz(slant_km) - 10.0 * math.log10(bit_rate_bps)


def _q(x: float) -> float:
    """高斯 Q 函数（math.erfc 实现，无 scipy 依赖）。"""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def ber(ebno_db_value: float, model: str = BER_MODEL) -> float:
    """误码率。BPSK: Pb = Q(sqrt(2·γ))；QPSK 与 BPSK 同 Pb。"""
    gamma = 10.0 ** (ebno_db_value / 10.0)
    if model in ("BPSK", "QPSK"):
        return _q(math.sqrt(2.0 * gamma))
    raise ValueError(f"未知调制方式 {model}")


def mac_fail_prob(slant_km: float, mac_bits: int,
                  bit_rate_bps: float = BIT_RATE_BPS) -> float:
    """MAC 字段被信道误码破坏的概率（→ 合法终端被误拒 = 虚警率）。

    P_fail = 1 − (1 − BER)^(mac_bits)。
    这是「仰角代价」兑现为真实后果的通路：仰角低 → 斜距大 → Eb/N0 低
    → BER 高 → MAC 被破坏概率高 → 合法终端被星上误拒。
    """
    p_bit = ber(ebno_db(slant_km, bit_rate_bps))
    if p_bit <= 0.0:
        return 0.0
    if p_bit >= 1.0:
        return 1.0
    return 1.0 - (1.0 - p_bit) ** mac_bits


def link_margin_db(slant_km: float, required_ebno_db: float = 6.0,
                   bit_rate_bps: float = BIT_RATE_BPS) -> float:
    """链路余量(dB) = 实际 Eb/N0 − 解调门限。<=0 表示不可用。"""
    return ebno_db(slant_km, bit_rate_bps) - required_ebno_db
