"""ns-3 一体化驱动（真实集成）：生成输入 -> WSL 跑 ns-3 -> 解析 trace -> 指标 -> 中文图表 -> 专业报告。

用法（受管 venv python，工作区根目录）：
  python run_ns3.py                # 默认 汶川 + OneWeb
  python run_ns3.py henan oneweb --seed 42
  python run_ns3.py wenchuan oneweb --ephem-err 30 --ho-lead 5   # 敏感性分析

★ 审计修复（2026-09-02）★
1. **fail-fast**：原实现 WSL 调用失败仅打印 [WARN] 后**继续读取磁盘上的旧 trace**，
   把陈旧数据当新结果出报告（退出码 0，无任何标识）。现改为直接抛错终止。
2. **种子参数化**：原 ns-3 侧随机种子固定（mt19937(12345)），无法做多种子/置信区间
   实验。现支持 --seed 传入。
3. **机理参数透传**：星历误差 / 选星权重 / 迟滞 / 密钥泄露占比 / 链路参数等
   与 Python 轨同参透传，保证双轨可比。
4. **产物隔离**：结果落在 data/sim/runs/<场景>_<种子>_<时间戳>_ns3/，不再覆盖。
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sim.config import (DATA_DIR, MASK_ANGLE_DEG, SIM_DURATION_S, TIME_STEP_S,
                        HO_LEAD_S, TICK_S, CARRIER_FREQ_HZ, EPHEM_ERR_S,
                        HO_W_EL, HO_W_DWELL, EIRP_DBM, GT_DBI_K, BIT_RATE_BPS,
                        RAR_WINDOW_MS, CONTENTION_TIMER_MS, N_PREAMBLE,
                        ACCESS_PROC_MS, AUTH_CPU_DERATE, PRIORITY_RESERVE_FRAC)
from sim.scenario import get_scenario
from sim.data_sources import fetch_tle
from sim.orbit import build_timescale
from sim import ns3_io
from sim import auth as _auth
from sim.eval import compute_metrics
from sim.viz import plot_coverage_timeline, plot_handover


def win2wsl(p: str) -> str:
    p = Path(p).as_posix()
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def parse_args(argv):
    args = {"seed": 20260901, "no_viz": False, "ephem_err": None,
            "ho_lead": None, "w_el": None, "w_dwell": None, "hyst": None,
            "compromised": None, "prio_mode": None}
    pos, i = [], 0
    while i < len(argv):
        a, nxt = argv[i], (argv[i + 1] if i + 1 < len(argv) else None)
        if a == "--seed" and nxt:
            args["seed"] = int(nxt); i += 2
        elif a == "--ephem-err" and nxt:
            args["ephem_err"] = float(nxt); i += 2
        elif a == "--ho-lead" and nxt:
            args["ho_lead"] = float(nxt); i += 2
        elif a == "--w-el" and nxt:
            args["w_el"] = float(nxt); i += 2
        elif a == "--w-dwell" and nxt:
            args["w_dwell"] = float(nxt); i += 2
        elif a == "--hyst" and nxt:
            args["hyst"] = float(nxt); i += 2
        elif a == "--compromised" and nxt:
            args["compromised"] = float(nxt); i += 2
        elif a == "--prio-mode" and nxt:
            args["prio_mode"] = nxt; i += 2
        elif a == "--no-viz":
            args["no_viz"] = True; i += 1
        else:
            pos.append(a); i += 1
    return pos, args


def main(scenario_key: str = "wenchuan", group: str = "oneweb", no_viz: bool = False,
         seed: int = 20260901, overrides=None):
    ov = {k: v for k, v in (overrides or {}).items() if v is not None}
    print(f"[1/5] 生成真实输入（TLE -> ECEF 星历 + 终端分布）")
    sc = get_scenario(scenario_key)
    sats, prov = fetch_tle(group)
    ts = build_timescale()
    # 参数一律取自场景配置（双轨同源，避免硬编码漂移）
    # ★双轨一致性修复★：CLI 覆盖优先，其次取场景级 ho_lead_s/ephem_err_s，
    # 最后才是 config 默认。原实现直接取 config 默认，忽略场景级参数，
    # 导致 rel17_baseline(ho_lead=0, ephem=5) 被 ns-3 跑成提案(ho_lead=20, ephem=0)，双轨不一致。
    ho_lead = ov.get("ho_lead", sc.get("ho_lead_s", HO_LEAD_S))
    ephem_err = ov.get("ephem_err", sc.get("ephem_err_s", EPHEM_ERR_S))
    w_el = ov.get("w_el", HO_W_EL)
    w_dwell = ov.get("w_dwell", HO_W_DWELL)
    hyst = ov.get("hyst", sc.get("ho_hyst", 0.0))
    compromised = ov.get("compromised", sc.get("compromised_share", 0.15))
    pm = ov.get("prio_mode", sc.get("priority_mode", "dp"))  # ★科学版 dp★ 调度模式透传（默认 dp，与 Python 轨对齐）
    auth_extra_ms = _auth.measure_verify_ms() * AUTH_CPU_DERATE
    params = dict(mask_deg=MASK_ANGLE_DEG, sim_duration_s=SIM_DURATION_S,
                  time_step_s=TIME_STEP_S,
                  burst_start_s=sc.get("burst_start_s", 5),
                  burst_window_s=sc.get("burst_ramp_s", 60),
                  access_proc_ms=sc.get("access_proc_ms", 3.0),
                  ho_lead_s=ho_lead, tick_s=TICK_S, carrier_hz=CARRIER_FREQ_HZ,
                  # ★一致性修复（2026-09-02 第 2 轮）★：实测认证时延入 params，
                  # 使 scenario.json 溯源快照记录真实透传值（原写死 0.0）
                  auth_extra_ms=round(auth_extra_ms, 6))
    ns3_io.gen_ephemeris(sats, ts, params["time_step_s"], params["sim_duration_s"],
                         ns3_io.NS3_IN / "ephemeris.csv")
    ns3_io.gen_terminals(sc, ns3_io.NS3_IN / "terminals.csv", seed=seed)
    ns3_io.write_ns3_scenario(sc, prov, params, ns3_io.NS3_IN / "scenario.json")
    print(f"      卫星 {len(sats)} 颗 · 终端 {sc['terminals']} 个 · 星历已写 · seed={seed}")

    print(f"[2/5] 调用 WSL 运行 ns-3 离散事件仿真 ...")
    indir = win2wsl(str(ns3_io.NS3_IN))
    outdir = win2wsl(str(ns3_io.NS3_OUT))
    inner = (
        "if ! cmp -s /mnt/e/pytorchFile/NationalCreation1/.ns3_ref/leo_access.cc "
        "/home/mark/ns-3-dev/scratch/leo_access.cc 2>/dev/null; then "
        "cp /mnt/e/pytorchFile/NationalCreation1/.ns3_ref/leo_access.cc "
        "/home/mark/ns-3-dev/scratch/leo_access.cc; fi; "
        "cd /home/mark/ns-3-dev && ./ns3 run \"leo_access "
        f"--indir={indir} --outdir={outdir} "
        f"--maskDeg={params['mask_deg']} --simDur={params['sim_duration_s']} "
        f"--stepS={params['time_step_s']} --burstStart={params['burst_start_s']} "
        f"--burstWin={params['burst_window_s']} --hoLead={ho_lead} "
        f"--tickS={params['tick_s']} --carrierHz={int(params['carrier_hz'])} "
        f"--accessProcMs={params['access_proc_ms']} --nTerms={sc['terminals']} "
        f"--forgedRatio={sc.get('forged_ratio', 0)} --authExtraMs={auth_extra_ms:.6f} "
        f"--rachSteps={sc.get('rach_steps', 2)} "
        f"--collisionOn={1 if sc.get('collision_on', False) else 0} "
        f"--priorityOn={1 if sc.get('priority_on', True) else 0} "
        f"--prioMode={pm} "
        f"--prioResHigh={PRIORITY_RESERVE_FRAC[0]} "
        f"--prioResMed={PRIORITY_RESERVE_FRAC[1]} "
        f"--prioResLow={PRIORITY_RESERVE_FRAC[2]} "
        f"--rachCapacity={sc.get('rach_capacity', 64)} "
        f"--retryIntervalMs={sc.get('retry_interval_ms', 500)} "
        f"--retryMax={sc.get('retry_max', 20)} "
        f"--ephemErrS={ephem_err} --wEl={w_el} --wDwell={w_dwell} --hoHyst={hyst} "
        f"--compromisedShare={compromised} --rngSeed={seed} "
        f"--eirpDbm={EIRP_DBM} --gtDbiK={GT_DBI_K} --bitRateBps={BIT_RATE_BPS} "
        f"--rarWindowMs={RAR_WINDOW_MS} --contTimerMs={CONTENTION_TIMER_MS} "
        f"--nPreamble={N_PREAMBLE} --linkModelOn=1\""
    )
    run_cmd = f'wsl -d Ubuntu-24.04 -- bash -c "{inner}"'
    # ★审计修复：fail-fast★ —— WSL/ns-3 失败必须终止，禁止静默使用旧 trace 冒充新结果
    r = subprocess.run(["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", inner],
                       capture_output=True, text=True, timeout=600,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(
            f"ns-3 运行失败（returncode={r.returncode}）。\n"
            f"  stderr 尾部: {r.stderr[-500:] if r.stderr else '(空)'}\n"
            f"  手动复现命令: {run_cmd}\n"
            f"  ★已禁止读取旧 trace 冒充新结果（审计修复 2026-09-02）★")
    wall_s = None
    out = r.stdout + r.stderr
    for line in out.splitlines():
        if "ns-3 调度墙钟时间=" in line:
            wall_s = line.split("=")[-1].strip().rstrip("s")
    for line in out.splitlines()[-8:]:
        print("      " + line)

    print(f"[3/5] 解析 ns-3 真实 trace ...")
    trace = ns3_io.parse_ns3_trace(ns3_io.NS3_OUT / "access_trace.csv")
    summary = {"auth_extra_ms": round(auth_extra_ms, 6),
               "total_dur": params["sim_duration_s"]}
    # ★P1/P2 双轨对齐★：解析 ns-3 stdout 的 [P2] 统计行，回填 summary（对齐 Python protocol.py）
    _p2_map = {"预迁移命中": ("n_premig_hit", int), "预迁移回退": ("n_premig_miss", int),
               "假名轮换": ("n_pseudo_rotation", int),
               "切换总时延和ms": ("ho_total_ms_sum", float),
               "重连额外时延和ms": ("rerach_extra_ms", float)}
    for line in (r.stdout + r.stderr).splitlines():
        if "[P2]" not in line:
            continue
        for tok in line.split():
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            if k in _p2_map:
                try:
                    summary[_p2_map[k][0]] = _p2_map[k][1](v)
                except ValueError:
                    pass
    metrics = compute_metrics(trace, summary)
    print("      指标：" + __import__("json").dumps(metrics, ensure_ascii=False))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rundir = DATA_DIR / "runs" / f"{scenario_key}_s{seed}_ns3_{stamp}"
    rundir.mkdir(parents=True, exist_ok=True)
    (rundir / "metrics.json").write_text(
        __import__("json").dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (rundir / "access_trace.csv").write_bytes((ns3_io.NS3_OUT / "access_trace.csv").read_bytes())
    print(f"      产物 -> {rundir}")

    if no_viz:
        return metrics, None, rundir

    print(f"[4/5] 生成中文图表 ...")
    cov = ns3_io.compute_coverage(ns3_io.NS3_IN / "ephemeris.csv", sc["lat"], sc["lon"],
                                  sc["alt_m"], params["mask_deg"], params["time_step_s"],
                                  int(params["sim_duration_s"] / params["time_step_s"]) + 1)
    pc = plot_coverage_timeline(cov, params["time_step_s"], sc["name"], outdir=rundir)
    ph = plot_handover(trace, sc["name"], outdir=rundir)
    print(f"[5/5] 图表: {pc.name}, {ph.name}")
    return metrics, (pc, ph), rundir


if __name__ == "__main__":
    pos, args = parse_args([a for a in sys.argv[1:]])
    sk = pos[0] if len(pos) > 0 else "wenchuan"
    gp = pos[1] if len(pos) > 1 else "oneweb"
    ov = {k: args[k] for k in ("ephem_err", "ho_lead", "w_el", "w_dwell", "hyst", "compromised", "prio_mode")}
    main(sk, gp, no_viz=args["no_viz"], seed=args["seed"], overrides=ov)
