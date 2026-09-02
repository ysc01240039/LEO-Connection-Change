"""性能诊断 ②：cProfile 拆解 run_protocol 内部热点（tottime + cumtime）。

用法：python perf/diag_profile.py [终端数]
输出：按 tottime / cumtime 排序的调用树，落盘 perf/profile_<n>.prof 供 perf_read.py 复看。
"""
import cProfile
import pstats
import sys
import io
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

from sim.data_sources import fetch_tle
from sim.orbit import build_timescale, compute_access
from sim.scenario import get_scenario
from sim.protocol import run_protocol

sats, prov = fetch_tle("oneweb")
ts = build_timescale()
sc = get_scenario("wenchuan")
windows, _ = compute_access(sats, sc["lat"], sc["lon"], sc["alt_m"], ts)
sc = dict(sc)
sc["terminals"] = N

print(f"卫星={len(sats)} 可见窗={len(windows)} 终端={N}")

pr = cProfile.Profile()
pr.enable()
trace = run_protocol(windows, sc, sats=sats, ts=ts, rng_seed=20260901)
pr.disable()

out = ROOT / "perf" / f"profile_{N}.prof"
pr.dump_stats(out)
print(f"profile 落盘: {out}")
print(f"事件数: {len(trace)}")

s = io.StringIO()
ps = pstats.Stats(pr, stream=s).strip_dirs()

for sort_key, title in (("tottime", "内部耗时 tottime（谁在自己身上耗 CPU）"),
                        ("cumtime", "累计耗时 cumtime（谁的调用链最贵）")):
    print("\n" + "=" * 78)
    print(f"=== {title} — top 22 ===")
    print("=" * 78)
    s2 = io.StringIO()
    pstats.Stats(pr, stream=s2).strip_dirs().sort_stats(sort_key).print_stats(22)
    print(s2.getvalue())
