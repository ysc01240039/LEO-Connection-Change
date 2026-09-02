"""性能诊断 ①：分阶段计时 + 工作单元计数。

目的：定位「时间花在哪一段」，以及「真实计算量有多大」（skyfield 调用次数、
可见窗线性扫描次数、缓存命中率）。只读不写，不污染 data/sim 交付物。

用法：python perf/diag_stages.py [终端数]
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PY = "C:/Users/ASUS/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
N_TERMINALS = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

# ---------- 计数器：skyfield 单次 .at() 调用次数（真实轨道计算的核心工作量） ----------
import skyfield.vectorlib as _sv
import skyfield.timelib as _stl

CNT = {"at": 0, "from_datetime": 0, "altaz": 0}

_orig_at = _sv.VectorFunction.at
_orig_fdt = _stl.Timescale.from_datetime


def _cnt_at(self, t):
    CNT["at"] += 1
    return _orig_at(self, t)


def _cnt_fdt(self, *a, **kw):
    CNT["from_datetime"] += 1
    return _orig_fdt(self, *a, **kw)


_sv.VectorFunction.at = _cnt_at
_stl.Timescale.from_datetime = _cnt_fdt

_orig_altaz = None
try:
    from skyfield.positionlib import GeometricInstant as _GI
    if hasattr(_GI, "altaz"):
        _orig_altaz = _GI.altaz

        def _cnt_altaz(self, *a, **kw):
            CNT["altaz"] += 1
            return _orig_altaz(self, *a, **kw)

        _GI.altaz = _cnt_altaz
except Exception:
    pass


def stage(name, fn):
    t0 = time.perf_counter()
    out = fn()
    dt = time.perf_counter() - t0
    print(f"  {name:<28} {dt:8.3f} s")
    return out, dt


print("=" * 72)
print(f"性能诊断 ①  阶段计时 + 工作单元计数   终端数={N_TERMINALS}")
print("=" * 72)

from sim.data_sources import fetch_tle
from sim.orbit import build_timescale, compute_access
from sim.scenario import get_scenario
from sim.protocol import run_protocol
from sim.eval import compute_metrics

t_all = time.perf_counter()

(sats, prov), t_tle = stage("① fetch_tle(缓存)", lambda: fetch_tle("oneweb"))
ts, t_ts = stage("② build_timescale", build_timescale)
sc, _ = stage("③ get_scenario", lambda: get_scenario("wenchuan"))
(windows, _), t_acc = stage("④ compute_access(可见性)",
                            lambda: compute_access(sats, sc["lat"], sc["lon"], sc["alt_m"], ts))

print(f"     卫星数={len(sats)}  可见窗数={len(windows)}")

sc2 = dict(sc)
sc2["terminals"] = N_TERMINALS

c0 = dict(CNT)
trace, t_proto = stage("⑤ run_protocol(协议+切换)",
                       lambda: run_protocol(windows, sc2, sats=sats, ts=ts, rng_seed=20260901))
c1 = dict(CNT)
metrics, t_eval = stage("⑥ compute_metrics", lambda: compute_metrics(trace))

t_total = time.perf_counter() - t_all

n_acc = sum(1 for e in trace if e["event_type"] == "ACCESS")
n_ho = sum(1 for e in trace if e["event_type"] == "HANDOVER")

print("-" * 72)
print(f"总墙钟                : {t_total:8.3f} s")
print(f"run_protocol 占比     : {t_proto / t_total * 100:8.1f} %")
print(f"compute_access 占比   : {t_acc / t_total * 100:8.1f} %")
print("-" * 72)
print(f"trace 事件数          : {len(trace):8d}  (接入 {n_acc} / 切换 {n_ho})")
print(f"每终端切换次数        : {n_ho / max(n_acc, 1):8.2f}")
print("-" * 72)
print("run_protocol 内部真实计算量：")
print(f"  skyfield VectorFunction.at() 调用 : {c1['at'] - c0['at']:10d}")
print(f"  ts.from_datetime() 调用           : {c1['from_datetime'] - c0['from_datetime']:10d}")
if _orig_altaz is not None:
    print(f"  GeometricInstant.altaz() 调用     : {c1['altaz'] - c0['altaz']:10d}")
print(f"  每次 .at() 摊薄耗时               : "
      f"{t_proto / max(c1['at'] - c0['at'], 1) * 1e6:10.1f} us")
print("=" * 72)
