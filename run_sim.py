"""驱动脚本：真实数据流入 -> 真实计算 -> 接口产出 -> 可视化。

用法（在 workspace 根目录，用受管 venv 的 python）：
  python run_sim.py                       # 默认 汶川 + OneWeb，种子 20260901
  python run_sim.py henan oneweb          # 切换场景/数据源
  python run_sim.py wenchuan oneweb --seed 42 --reps 5
                                          # 批量：5 个种子取均值+置信区间
"""
import sys
import json
import statistics as st
from sim.config import DATA_DIR
from sim.scenario import get_scenario
from sim.data_sources import fetch_tle
from sim.orbit import build_timescale, compute_access
from sim.protocol import run_protocol
from sim.eval import compute_metrics
from sim.interfaces import write_scenario_json, write_trace_csv, write_metrics_json
from sim.viz import plot_coverage, plot_handover, write_report


def parse_args(argv):
    args = {"seed": 20260901, "reps": 1}
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--seed" and i + 1 < len(argv):
            args["seed"] = int(argv[i + 1]); i += 2
        elif a == "--reps" and i + 1 < len(argv):
            args["reps"] = int(argv[i + 1]); i += 2
        else:
            pos.append(a); i += 1
    return pos, args


def main(scenario_key="wenchuan", group="oneweb", seed=20260901, reps=1):
    print(f"[1/6] TLE（缓存优先）({group}) ...")
    sats, prov = fetch_tle(group)
    print(f"      来源 : {prov['url']}   缓存: {prov.get('cache_hit')}")
    print(f"      卫星数: {prov['satellite_count']}")

    sc = get_scenario(scenario_key)
    print(f"[2/6] 场景: {sc['name']} ({sc['lat']}, {sc['lon']}) 终端数 {sc['terminals']}")

    ts = build_timescale()
    from sim.config import MASK_ANGLE_DEG
    print(f"[3/6] 计算真实可见性窗口（仰角>{MASK_ANGLE_DEG}°）...")
    windows, _ = compute_access(sats, sc["lat"], sc["lon"], sc["alt_m"], ts)
    print(f"      可见窗总数: {len(windows)}")

    reps = max(1, reps)
    all_metrics = []
    for r in range(reps):
        rseed = seed + r
        print(f"[4/{4 + reps - 1}] 运行协议参考模型（两步/四步接入 + T4 认证 + 碰撞拥塞 + 预测式切换）seed={rseed} ...")
        trace = run_protocol(windows, sc, sats=sats, ts=ts, rng_seed=rseed)
        print(f"      事件总数: {len(trace)}")
        m = compute_metrics(trace)
        all_metrics.append(m)
        if reps == 1:
            print("      " + json.dumps(m, ensure_ascii=False))

    # 汇总：单次直接落盘；多次取均值 + 三西格玛区间（真实可靠性）
    if reps == 1:
        metrics = all_metrics[0]
    else:
        keys = [k for k in all_metrics[0]]
        metrics = {}
        for k in keys:
            vals = [m[k] for m in all_metrics]
            mean = st.mean(vals)
            sd = st.stdev(vals) if len(vals) > 1 else 0.0
            metrics[k] = mean
            metrics[k + "_stdev"] = sd
        print("      多种子汇总(mean ± 2σ):")
        for k in keys:
            print(f"      {k}: {metrics[k]:.4f} ± {metrics[k + '_stdev']:.4f}")

    print(f"[{4 + reps}/6] 写接口 & 图表 ...")
    write_scenario_json(DATA_DIR / "scenario_config.json", sc, prov, windows)
    write_trace_csv(DATA_DIR / "access_trace.csv", trace)
    write_metrics_json(DATA_DIR / "metrics.json", metrics)
    pc = plot_coverage(windows, sc["name"])
    ph = plot_handover(trace, sc["name"])
    rep = write_report(metrics, prov, sc["name"], pc, ph, DATA_DIR / "report.html")
    print(f"产出目录: {DATA_DIR}")
    print(f"图表: {pc.name}, {ph.name}")
    print(f"报告: {rep.name}")
    return metrics, rep


if __name__ == "__main__":
    pos, args = parse_args(sys.argv[1:])
    sk = pos[0] if len(pos) > 0 else "wenchuan"
    gp = pos[1] if len(pos) > 1 else "oneweb"
    main(sk, gp, seed=args["seed"], reps=args["reps"])