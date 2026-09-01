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
from pathlib import Path
import numpy as np

from .config import DATA_DIR, SIM_START_UTC, TIME_STEP_S
from .scenario import get_scenario
from .data_sources import fetch_tle
from .orbit import build_timescale
from skyfield.api import EarthSatellite, wgs84

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
    tags = ["high", "med", "low"]
    weights = [0.2, 0.35, 0.45]
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
        # ---- T4 / RACH / 碰撞（与 scenario.py 及 leo_access.cc 同参）----
        "forged_ratio": sc.get("forged_ratio", 0.0),
        "auth_extra_ms": sc.get("auth_extra_ms", 0.0),
        "rach_steps": sc.get("rach_steps", 2),
        "step4_extra_ms": sc.get("step4_extra_ms", 400.0),
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
TRACE_FIELDS = ["event_type", "terminal", "tag", "t_s", "serving_sat",
                "target_sat", "value_ms", "doppler_hz", "slant_km",
                "result", "predict_mismatch", "pingpong", "ho_el_cost_deg", "forged"]


def parse_ns3_trace(out_csv):
    rows = []
    with open(out_csv, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for d in r:
            rows.append(d)
    return rows


def compute_ns3_metrics(trace):
    """trace: list[dict]，字段见 TRACE_FIELDS（与接口契约一致）。
    口径（契约 2.1，与 Python 轨 sim/eval.compute_metrics 完全一致）：
    - 接入成功率按合法终端统计；伪造终端由「伪造终端数/拦截率」单独汇报；
    - 接入时延 = (GRANT 完成时刻 − 首次发起时刻)×1000（端到端，含退避/等待与握手），
      仅统计成功接入的合法终端（value_ms > 0）。"""
    acc = [e for e in trace if e["event_type"] == "ACCESS"]
    ho = [e for e in trace if e["event_type"] == "HANDOVER"]
    def f(x):
        return float(x)
    def metrics_of(acc_list):
        if not acc_list:
            return {}
        dl = sorted(f(e["value_ms"]) for e in acc_list if f(e["value_ms"]) > 0)  # 失败事件不参与时延统计（口径同 eval.py）
        n = len(acc_list)
        succ = sum(1 for e in acc_list if e["result"] == "success")
        if not dl:
            return {"接入事件数": n, "接入成功率": round(succ / n, 4),
                    "接入时延均值_ms": 0.0, "接入时延P95_ms": 0.0}
        mean = sum(dl) / len(dl)
        p95 = dl[min(len(dl) - 1, int(0.95 * len(dl)))]
        return {
            "接入事件数": n,
            "接入成功率": round(succ / n, 4),
            "接入时延均值_ms": round(mean, 2),
            "接入时延P95_ms": round(p95, 2),
        }
    # 合法终端与伪造终端分流（T4 口径，与 Python 轨 eval.py 同名同口径）
    forged = [e for e in acc if e.get("forged", "0") == "1"]
    legit = [e for e in acc if e.get("forged", "0") != "1"]
    acc_m = metrics_of(legit)
    ho_metrics = {}
    if ho:
        inter = sorted(f(e["value_ms"]) for e in ho)
        n = len(inter)
        elc = [f(e.get("ho_el_cost_deg", 0) or 0) for e in ho]
        ho_metrics = {
            "切换事件数": n,
            "切换中断均值_ms": round(sum(inter) / n, 2),
            "切换中断最大_ms": round(max(inter), 2),
            "乒乓切换率": round(sum(1 for e in ho if e["pingpong"] == "1") / n, 4),
            "预测失配率": round(sum(1 for e in ho if e["predict_mismatch"] == "1") / n, 4),
            "仰角代价均值_deg": round(sum(elc) / n, 2),
            "仰角代价最大_deg": round(max(elc), 2),
        }
    # T4 认证指标（伪造终端拦截，与 Python 轨 sim/eval.compute_metrics 同名同口径）
    if forged:
        acc_m["伪造终端数"] = len(forged)
        acc_m["伪造终端拦截率"] = round(
            sum(1 for e in forged if e["result"] == "fail") / len(forged), 4)
    # 多普勒（取所有事件最大绝对值，反映 LEO 多普勒量级）
    dop = [abs(f(e["doppler_hz"])) for e in trace if e["doppler_hz"] not in ("", None)]
    dop_max = round(max(dop), 1) if dop else 0.0
    merged = {}
    merged.update(acc_m)
    merged.update(ho_metrics)
    merged["多普勒最大值_Hz"] = dop_max
    merged["总事件数"] = len(trace)
    return merged


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
