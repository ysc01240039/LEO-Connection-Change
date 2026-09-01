"""ns-3 一体化驱动（真实集成）：生成输入 -> WSL 跑 ns-3 -> 解析 trace -> 指标 -> 中文图表 -> 专业报告。

用法（受管 venv python，工作区根目录）：
  python run_ns3.py                # 默认 汶川 + OneWeb
  python run_ns3.py henan starlink
"""
import sys
import json
import subprocess
from pathlib import Path

from sim.config import (DATA_DIR, MASK_ANGLE_DEG, SIM_DURATION_S, TIME_STEP_S,
                        HO_LEAD_S, TICK_S, CARRIER_FREQ_HZ)
from sim.scenario import get_scenario
from sim.data_sources import fetch_tle
from sim.orbit import build_timescale
from sim import ns3_io
from sim.viz import plot_coverage_timeline, plot_handover, write_report_ns3


def win2wsl(p: str) -> str:
    p = Path(p).as_posix()
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def main(scenario_key: str = "wenchuan", group: str = "oneweb"):
    print(f"[1/5] 生成真实输入（TLE -> ECEF 星历 + 终端分布）")
    sc = get_scenario(scenario_key)
    sats, prov = fetch_tle(group)
    ts = build_timescale()
    # 参数一律取自场景配置（双轨同源，避免硬编码漂移）
    params = dict(mask_deg=MASK_ANGLE_DEG, sim_duration_s=SIM_DURATION_S,
                  time_step_s=TIME_STEP_S,
                  burst_start_s=sc.get("burst_start_s", 5),
                  burst_window_s=sc.get("burst_ramp_s", 60),
                  access_proc_ms=sc.get("access_proc_ms", 3.0),
                  ho_lead_s=HO_LEAD_S, tick_s=TICK_S, carrier_hz=CARRIER_FREQ_HZ)
    ns3_io.gen_ephemeris(sats, ts, params["time_step_s"], params["sim_duration_s"], ns3_io.NS3_IN / "ephemeris.csv")
    ns3_io.gen_terminals(sc, ns3_io.NS3_IN / "terminals.csv")
    ns3_io.write_ns3_scenario(sc, prov, params, ns3_io.NS3_IN / "scenario.json")
    print(f"      卫星 {len(sats)} 颗 · 终端 {sc['terminals']} 个 · 星历已写")

    print(f"[2/5] 调用 WSL 运行 ns-3 离散事件仿真 ...")
    indir = win2wsl(str(ns3_io.NS3_IN))
    outdir = win2wsl(str(ns3_io.NS3_OUT))
    inner = (
        "cp /mnt/e/pytorchFile/NationalCreation1/.ns3_ref/leo_access.cc "
        "/home/mark/ns-3-dev/scratch/leo_access.cc; "
        "cd /home/mark/ns-3-dev && ./ns3 run \"leo_access "
        f"--indir={indir} --outdir={outdir} "
        f"--maskDeg={params['mask_deg']} --simDur={params['sim_duration_s']} "
        f"--stepS={params['time_step_s']} --burstStart={params['burst_start_s']} "
        f"--burstWin={params['burst_window_s']} --hoLead={params['ho_lead_s']} "
        f"--tickS={params['tick_s']} --carrierHz={int(params['carrier_hz'])} "
        f"--accessProcMs={params['access_proc_ms']} --nTerms={sc['terminals']} "
        f"--forgedRatio={sc.get('forged_ratio', 0)} --authExtraMs={sc.get('auth_extra_ms', 0)} "
        f"--rachSteps={sc.get('rach_steps', 2)} --step4ExtraMs={sc.get('step4_extra_ms', 400)} "
        f"--collisionOn={1 if sc.get('collision_on', False) else 0} "
        f"--rachCapacity={sc.get('rach_capacity', 64)} "
        f"--retryIntervalMs={sc.get('retry_interval_ms', 500)} "
        f"--retryMax={sc.get('retry_max', 20)}\""
    )
    run_cmd = f'wsl -d Ubuntu-24.04 -- bash -c "{inner}"'
    wall_s = None
    try:
        r = subprocess.run(["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", inner],
                           capture_output=True, text=True, timeout=600,
                           encoding="utf-8", errors="replace")
        out = r.stdout + r.stderr
        for line in out.splitlines():
            if "ns-3 调度墙钟时间=" in line:
                wall_s = line.split("=")[-1].strip().rstrip("s")
        print("      ns-3 输出尾部：")
        for line in out.splitlines()[-8:]:
            print("      " + line)
    except Exception as e:
        print(f"      [WARN] 自动调用 WSL 失败（{e}）；将使用已存在的 trace。可手动运行：\n      {run_cmd}")

    print(f"[3/5] 解析 ns-3 真实 trace ...")
    trace = ns3_io.parse_ns3_trace(ns3_io.NS3_OUT / "access_trace.csv")
    metrics = ns3_io.compute_ns3_metrics(trace)
    print("      指标：" + json.dumps(metrics, ensure_ascii=False))

    print(f"[4/5] 生成中文图表 ...")
    cov = ns3_io.compute_coverage(ns3_io.NS3_IN / "ephemeris.csv", sc["lat"], sc["lon"],
                                  sc["alt_m"], params["mask_deg"], params["time_step_s"],
                                  int(params["sim_duration_s"] / params["time_step_s"]) + 1)
    pc = plot_coverage_timeline(cov, params["time_step_s"], sc["name"])
    ph = plot_handover(trace, sc["name"])

    print(f"[5/5] 写专业报告 ...")
    ns3_meta = {
        "version": "ns-3-dev (3-dev)",
        "modules": "core / network / mobility（自定义 LEO 信道）",
        "n_sats": len(sats),
        "n_terms": sc["terminals"],
        "sim_duration_s": params["sim_duration_s"],
        "mask_deg": params["mask_deg"],
        "carrier_hz": int(params["carrier_hz"]),
        "access_proc_ms": params["access_proc_ms"],
        "ho_lead_s": params["ho_lead_s"],
        "rach_steps": sc.get("rach_steps", 2),
        "forged_ratio": sc.get("forged_ratio", 0),
        "auth_extra_ms": sc.get("auth_extra_ms", 0),
        "rach_capacity": sc.get("rach_capacity", 64),
        "retry_max": sc.get("retry_max", 20),
        "collision_on": sc.get("collision_on", False),
        "wall_s": wall_s or "≈0.2",
        "run_command": run_cmd,
    }
    # 事件样例（前 6 条 ACCESS + 前 6 条 HANDOVER）
    acc = [e for e in trace if e["event_type"] == "ACCESS"][:6]
    ho = [e for e in trace if e["event_type"] == "HANDOVER"][:6]
    samples = [",".join(str(x) for x in e.values()) for e in acc + ho]
    rep = write_report_ns3(metrics, prov, sc["name"], pc, ph,
                           DATA_DIR / "report_ns3.html", ns3_meta, samples)

    # 同时落盘 metrics.json（供 Web 消费，接口一致）
    with open(DATA_DIR / "metrics_ns3.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    print(f"产出：{rep}")
    print(f"      指标：{DATA_DIR / 'metrics_ns3.json'}")
    print(f"      图表：{pc.name}, {ph.name}")
    return metrics, rep


if __name__ == "__main__":
    sk = sys.argv[1] if len(sys.argv) > 1 else "wenchuan"
    gp = sys.argv[2] if len(sys.argv) > 2 else "oneweb"
    main(sk, gp)
