"""科学版 dp 调度 vs static 三池：量化对照（★第4轮验证★）。

验证点：
1. 低高危负载 → dp 收缩 g_h（回收闲置），中/低危成功率 ↑，高危成功率不降；
2. 高危主导负载 → dp 维持高危保护（高危成功率 ≥ static），中低危按 guard 受限；
3. 均衡负载 → 两者接近，dp 不劣化。

用法：python perf/prio_dp_cmp.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sim.scenario import get_scenario
from sim.orbit import build_timescale, compute_access
from sim.data_sources import fetch_tle
from sim.protocol import run_protocol
from sim.eval import compute_metrics
from sim.config import MASK_ANGLE_DEG


def run(sc, prio_mode):
    sats, _ = fetch_tle("oneweb")
    ts = build_timescale()
    windows, _ = compute_access(sats, sc["lat"], sc["lon"], sc["alt_m"], ts)
    params = {"priority_mode": prio_mode}
    trace, summary = run_protocol(windows, sc, sats=sats, ts=ts,
                                  rng_seed=20260901, params=params)
    m = compute_metrics(trace, summary)
    return m, summary


def main():
    bases = {
        "窄带风暴 c=4": "wenchuan_storm2",
        "宽带 c=64": "wenchuan",
    }
    mixes = {
        "低高危(0.05/0.35/0.60)": (0.05, 0.35, 0.60),
        "均衡(0.20/0.35/0.45)": (0.20, 0.35, 0.45),
        "高危主导(0.45/0.30/0.25)": (0.45, 0.30, 0.25),
    }
    print(f"{'场景':14s} {'危险度配比':20s} {'模式':6s} {'高危':>7s} {'中危':>7s} {'低危':>7s} "
          f"{'加权阻塞':>9s} {'平均g_h':>7s}")
    print("-" * 86)
    for bname, bkey in bases.items():
        base = get_scenario(bkey)
        for name, (h, me, lo) in mixes.items():
            sc = dict(base)
            sc["name"] = f"{bname}/{name}"
            sc["danger_tags"] = {"high": h, "med": me, "low": lo}
            for mode in ("static", "dp"):
                m, s = run(sc, mode)
                gh = s.get("dp_avg_gh", "-") if mode == "dp" else "—"
                print(f"{bname:14s} {name:20s} {mode:6s} "
                      f"{m['high危终端接入成功率']:7.4f} {m['med危终端接入成功率']:7.4f} "
                      f"{m['low危终端接入成功率']:7.4f} {m.get('加权阻塞率(中低危)',0):9.4f} {str(gh):>7s}")


if __name__ == "__main__":
    main()
