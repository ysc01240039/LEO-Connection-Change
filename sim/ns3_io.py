"""ns-3 接口 I/O（L3 真实协议仿真的供数 / 回采层）。

职责边界（AGENTS.md 一·B）：
- Python 侧：把真实 TLE 算成 ECEF 星历轨迹 + 灾害场景终端分布，写成 ns-3 可读文件；
  回采 ns-3 真实落盘的 trace，统计指标。
- ns-3 侧（scratch/leo_access.cc）：读这些文件，用离散事件引擎跑接入/切换，写 trace。

所有坐标单位：长度 km，时间 s。ECEF 为地心地固系（含地球自转），与 ns-3 一致。
"""
import csv
import json
import math
import random
from datetime import datetime, timedelta
import numpy as np

from .config import DATA_DIR, SIM_START_UTC, TIME_STEP_S
from .scenario import get_scenario
from .data_sources import fetch_tle
from .orbit import build_timescale
from skyfield.api import EarthSatellite

NS3_IN = DATA_DIR / "ns3_in"
NS3_OUT = DATA_DIR / "ns3_out"
NS3_IN.mkdir(parents=True, exist_ok=True)
NS3_OUT.mkdir(parents=True, exist_ok=True)

R_EARTH_KM = 6371.0
C_KM_S = 299792.458  # 光速 km/s


def ecef_from_latlon(lat_deg, lon_deg, alt_m):
    """WGS84 近似：经纬度+海拔 -> ECEF km。"""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    # 简化椭球（与 skyfield itrf 同量级，误差<0.3%）
    a = 6378.137
    f = 1.0 / 298.257223563
    e2 = f * (2 - f)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    h = alt_m / 1000.0
    x = (n + h) * math.cos(lat) * math.cos(lon)
    y = (n + h) * math.cos(lat) * math.sin(lon)
    z = (n * (1 - e2) + h) * math.sin(lat)
    return x, y, z


def gen_ephemeris(sats, ts, step_s, duration_s, out_csv):
    """为每颗星在 [0, duration_s] 内每 step_s 生成一条 ECEF(km) 轨迹。
    sats: fetch_tle 返回的 [(name, line1, line2), ...]
    返回卫星数。
    """
    t0 = datetime.fromisoformat(SIM_START_UTC.replace("Z", "+00:00"))
    n_steps = int(duration_s / step_s) + 1
    # 构造真正的 Time 数组（与 orbit._gen_times 一致）
    dts = [t0 + timedelta(seconds=step_s * i) for i in range(n_steps)]
    years = np.array([d.year for d in dts])
    months = np.array([d.month for d in dts])
    days = np.array([d.day for d in dts])
    hours = np.array([d.hour for d in dts])
    minutes = np.array([d.minute for d in dts])
    seconds = np.array([d.second + d.microsecond / 1e6 for d in dts])
    times = ts.utc(years, months, days, hours, minutes, seconds)
    rows = []
    for name, l1, l2 in sats:
        try:
            sat = EarthSatellite(l1, l2, name)
        except Exception:
            continue
        # itrf_xyz 已含地球自转（ECEF）
        pos = sat.at(times).itrf_xyz().km  # shape (3, n)
        for i in range(n_steps):
            rows.append((name, round(step_s * i, 3),
                         round(float(pos[0][i]), 3),
                         round(float(pos[1][i]), 3),
                         round(float(pos[2][i]), 3)))
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["sat_name", "t_s", "x_km", "y_km", "z_km"])
        w.writerows(rows)
    return len(sats)


def gen_terminals(sc, out_csv, seed=42):
    """在灾害中心周围生成带危险度标签的终端分布。
    返回终端列表 [(id, lat, lon, alt_m, tag)]。
    """
    rnd = random.Random(seed)
    center_lat, center_lon, center_alt = sc["lat"], sc["lon"], sc["alt_m"]
    spread = sc.get("spread_deg", 0.6)
    n = sc["terminals"]
    # 危险度分层（与 Note.txt/Text.txt 一致：指挥/灾情/报平安）
    # ★一致性修复（2026-09-02 第 2 轮）★：权重改读场景配置 danger_tags，
    # 原为硬编码 [0.2, 0.35, 0.45] 与 sim/scenario.py 双处维护，存在漂移风险。
    danger = sc.get("danger_tags", {"high": 0.20, "med": 0.35, "low": 0.45})
    tags = ["high", "med", "low"]
    weights = [danger["high"], danger["med"], danger["low"]]
    terms = []
    for i in range(n):
        dlat = rnd.uniform(-spread, spread)
        dlon = rnd.uniform(-spread, spread)
        lat = center_lat + dlat
        lon = center_lon + dlon
        alt = center_alt + rnd.uniform(-50, 200)
        tag = rnd.choices(tags, weights=weights, k=1)[0]
        terms.append((i, round(lat, 6), round(lon, 6), round(alt, 1), tag))
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["term_id", "lat", "lon", "alt_m", "tag"])
        w.writerows(terms)
    return terms


def write_ns3_scenario(sc, prov, params, out_json):
    cfg = {
        "scenario_name": sc["name"],
        "center_lat": sc["lat"],
        "center_lon": sc["lon"],
        "center_alt_m": sc["alt_m"],
        "mask_deg": params["mask_deg"],
        "sim_duration_s": params["sim_duration_s"],
        "time_step_s": params["time_step_s"],
        "n_terminals": sc["terminals"],
        "burst_start_s": params["burst_start_s"],
        "burst_window_s": params["burst_window_s"],
        "access_proc_ms": params["access_proc_ms"],
        "ho_lead_s": params["ho_lead_s"],
        "tick_s": params["tick_s"],
        "carrier_hz": params["carrier_hz"],
        # ---- T4 / RACH / 碰撞（与 scenario.py 及 leo_access.cc 同参；ns-3 实际
        #      消费的参数由 run_ns3.py 命令行显式透传，此处为溯源快照）----
        "forged_ratio": sc.get("forged_ratio", 0.0),
        # ★一致性修复（2026-09-02 第 2 轮）★：auth_extra_ms 为本机 HMAC 实测折算值
        # （run_ns3.py 计算并透传）；step4_extra_ms 已删除（四步附加时延改为
        # RAR 窗口+竞争解决定时器+几何往返实算，见 sim/protocol.py 四步建模）。
        "auth_extra_ms": params.get("auth_extra_ms", 0.0),
        "rach_steps": sc.get("rach_steps", 2),
        "collision_on": sc.get("collision_on", False),
        "rach_capacity": sc.get("rach_capacity", 64),
        "retry_interval_ms": sc.get("retry_interval_ms", 500.0),
        "retry_max": sc.get("retry_max", 20),
        "sat_eph_csv": "ephemeris.csv",
        "terminals_csv": "terminals.csv",
        "out_csv": "access_trace.csv",
        "provenance": prov,
    }
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


# ----------------- 回采：解析 ns-3 真实 trace -----------------
# ★一致性修复（2026-09-02 第 2 轮）★：补齐至 16 列（auth_result、ebno_db 为
# 第一轮审计新增字段，leo_access.cc 与 sim/interfaces.py TRACE_COLS 均为 16 列）。
TRACE_FIELDS = ["event_type", "terminal", "tag", "t_s", "serving_sat",
                "target_sat", "value_ms", "doppler_hz", "slant_km",
                "result", "predict_mismatch", "pingpong", "ho_el_cost_deg", "forged",
                "auth_result", "ebno_db"]


def parse_ns3_trace(out_csv):
    """解析 ns-3 trace。★审计修复★：增加表头校验，缺列立即报错而非静默取空。"""
    with open(out_csv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None:
            raise ValueError(f"trace 文件为空或缺少表头: {out_csv}")
        missing = [c for c in TRACE_FIELDS if c not in r.fieldnames]
        if missing:
            raise ValueError(
                f"trace 缺少契约字段 {missing}\n  实际表头: {r.fieldnames}\n"
                f"  期望: {TRACE_FIELDS}\n  → 请重新编译/运行 ns-3 侧 leo_access.cc")
        rows = []
        for i, d in enumerate(r, 1):
            if not d.get("event_type"):
                raise ValueError(f"trace 第 {i} 行缺少 event_type: {out_csv}")
            rows.append(d)
    return rows


def compute_ns3_metrics(trace, summary=None):
    """★审计修复★：原为与 sim/eval.compute_metrics 近乎逐行的重复实现（约 55 行），
    两侧靠注释维系「同名同口径」，实际已出现不一致（C++ 对 elCost 做 <0→0 截断，
    Python 未做）。现统一委托给 eval.compute_metrics（类型宽容），消除漂移。"""
    from .eval import compute_metrics
    return compute_metrics(trace, summary)


if __name__ == "__main__":
    sc = get_scenario("wenchuan")
    sats, prov = fetch_tle("oneweb")
    ts = build_timescale()
    params = dict(mask_deg=25.0, sim_duration_s=3600, time_step_s=15,
                  burst_start_s=5, burst_window_s=60, access_proc_ms=3.0,
                  ho_lead_s=20, tick_s=1, carrier_hz=2.0e9)
    n = gen_ephemeris(sats, ts, params["time_step_s"], params["sim_duration_s"], NS3_IN / "ephemeris.csv")
    gen_terminals(sc, NS3_IN / "terminals.csv")
    write_ns3_scenario(sc, prov, params, NS3_IN / "scenario.json")
    print("ephemeris sats:", n, "->", NS3_IN / "ephemeris.csv")


# ----------------- 覆盖度（供可视化，真实几何） -----------------
def _elev_deg(term_xyz, sat_xyz):
    t = np.array(term_xyz, dtype=float); s = np.array(sat_xyz, dtype=float)
    tu = np.linalg.norm(t)
    up = t / tu
    zhat = np.array([0.0, 0.0, 1.0])
    east = np.cross(zhat, up); east = east / np.linalg.norm(east)
    north = np.cross(up, east)
    sv = s - t
    e = float(np.dot(sv, east)); nn = float(np.dot(sv, north)); u = float(np.dot(sv, up))
    return math.degrees(math.atan2(u, math.sqrt(e * e + nn * nn)))


def compute_coverage(ephemeris_csv, center_lat, center_lon, center_alt_m,
                     mask_deg, step_s, n_steps):
    """返回每步在灾害中心上空的可见卫星数（真实几何，与 ns-3 判据一致）。"""
    from collections import defaultdict
    data = defaultdict(list)
    with open(ephemeris_csv, newline="", encoding="utf-8") as f:
        for d in csv.DictReader(f):
            data[d["sat_name"]].append((float(d["t_s"]), float(d["x_km"]),
                                        float(d["y_km"]), float(d["z_km"])))
    term = ecef_from_latlon(center_lat, center_lon, center_alt_m)
    cov = [0] * n_steps
    for rows in data.values():
        for (t, x, y, z) in rows:
            idx = int(round(t / step_s))
            if 0 <= idx < n_steps and _elev_deg(term, (x, y, z)) >= mask_deg:
                cov[idx] += 1
    return cov
