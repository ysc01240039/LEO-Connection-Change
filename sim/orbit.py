"""轨道与可见性计算（REPLACEABLE：可换为 STK/其他星历）。
使用真实 TLE + skyfield，星历与几何全部来自真实计算（非写死常量）；设备/协议层参数为 config.py 中的显式建模假设。"""
from datetime import datetime, timedelta
import numpy as np
from skyfield.api import load, EarthSatellite, wgs84
from .config import (SIM_START_UTC, SIM_DURATION_S, TIME_STEP_S, MASK_ANGLE_DEG,
                     CARRIER_FREQ_HZ, SPEED_OF_LIGHT)


def build_timescale():
    # builtin=True 使用内置历表，避免在线下载精密星历，离线可用；演示精度足够
    return load.timescale(builtin=True)


def _gen_times(ts):
    t0 = datetime.fromisoformat(SIM_START_UTC.replace("Z", "+00:00"))
    n = int(SIM_DURATION_S / TIME_STEP_S)
    # 构造真正的 Time 数组（skyfield .at() 需数组而非 list 才能向量化）
    dts = [t0 + timedelta(seconds=s * TIME_STEP_S) for s in range(n)]
    years = np.array([d.year for d in dts])
    months = np.array([d.month for d in dts])
    days = np.array([d.day for d in dts])
    hours = np.array([d.hour for d in dts])
    minutes = np.array([d.minute for d in dts])
    seconds = np.array([d.second + d.microsecond / 1e6 for d in dts])
    times = ts.utc(years, months, days, hours, minutes, seconds)
    return times, t0, n


def _geometry(diff, t):
    """真实几何：径向速度->多普勒、斜距->传播时延。"""
    g = diff.at(t)
    p = np.array(g.position.km)
    v = np.array(g.velocity.km_per_s)
    slant = float(np.linalg.norm(p))
    if slant == 0:
        return 0.0, 0.0, 0.0
    radial = float(np.dot(p, v) / slant)            # km/s，远离为正
    dop = -CARRIER_FREQ_HZ * (radial * 1000.0) / SPEED_OF_LIGHT
    delay = slant * 1000.0 / SPEED_OF_LIGHT          # 单向传播时延(s)
    return round(dop, 1), round(slant, 1), round(delay * 1000.0, 2)


# ★方案A（2026-09-16）★ 网格窗：以场景中心 ± (n//2)·step 的方区取格点中心，各算可见窗。
# 两轨把终端**吸附**到最近格点（round((lat-clat)/step)）后共用该格点的窗 →
# 窗集只依赖格点中心（两轨共享同一 center/step/n），故 Python 与 ns-3 的可见性**严格一致**。
GRID_N = 5          # 每边格点数（5×5=25）
GRID_STEP_DEG = 0.3  # 格点间距(°)（±0.6° ≈ 覆盖 spread_deg=0.6 的方区）


def snap_cell(lat, lon, clat, clon, step_deg=GRID_STEP_DEG, n=GRID_N):
    """终端 → 最近格点 (i, j)，i/j ∈ [-n//2, n//2]。"""
    h = n // 2
    i = int(round((lat - clat) / step_deg)); i = max(-h, min(h, i))
    j = int(round((lon - clon) / step_deg)); j = max(-h, min(h, j))
    return i, j


def compute_grid_windows(sats, clat, clon, alt_m, ts,
                         step_deg=GRID_STEP_DEG, n=GRID_N):
    """返回 {(i,j): windows}，键为格点索引；窗内容同 compute_access。"""
    h = n // 2
    out = {}
    for i in range(-h, h + 1):
        for j in range(-h, h + 1):
            w, _ = compute_access(sats, clat + i * step_deg, clon + j * step_deg, alt_m, ts)
            out[(i, j)] = w
    return out


def compute_access(sats, lat, lon, alt_m, ts):
    """返回每颗星的可见时间窗（真实计算）：
    [{'sat','aos_s','los_s','max_el','dur_s','doppler_max_hz','slant_km','delay_ms'}, ...]
    时间为相对仿真起点的秒。"""
    times, t0, n = _gen_times(ts)
    observer = wgs84.latlon(lat, lon, alt_m)
    out = []
    for name, l1, l2 in sats:
        try:
            sat = EarthSatellite(l1, l2, name)
        except Exception:
            continue
        diff = sat - observer
        try:
            alts = np.asarray(diff.at(times).altaz()[0].degrees, dtype=float)
        except Exception:
            continue
        i = 0
        while i < n:
            if alts[i] >= MASK_ANGLE_DEG:
                j = i
                mx = alts[i]; mi = i
                while j < n and alts[j] >= MASK_ANGLE_DEG:
                    if alts[j] > mx:
                        mx = alts[j]; mi = j
                    j += 1
                dop, slant, delay = _geometry(diff, times[mi])
                out.append({
                    "sat": name,
                    "aos_s": int(i * TIME_STEP_S),
                    "los_s": int((j - 1) * TIME_STEP_S),
                    "max_el": round(float(mx), 2),
                    "dur_s": int((j - 1 - i) * TIME_STEP_S),
                    "doppler_max_hz": dop,
                    "slant_km": slant,
                    "delay_ms": delay,
                })
                i = j
            else:
                i += 1
    return out, [t0 + timedelta(seconds=s * TIME_STEP_S) for s in range(n)]
