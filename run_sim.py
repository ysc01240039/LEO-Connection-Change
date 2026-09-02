"""驱动脚本：真实数据流入 -> 真实计算 -> 接口产出 -> 可视化。

用法（在 workspace 根目录，用受管 venv 的 python）：
  python run_sim.py                       # 默认 汶川 + OneWeb，种子 20260901
  python run_sim.py henan oneweb          # 切换场景/数据源
  python run_sim.py wenchuan oneweb --seed 42 --reps 5
  python run_sim.py wenchuan oneweb --ho-lead 2 --ephem-err 15   # 参数覆盖（敏感性分析）

★ 审计修复（2026-09-02）★
原实现把所有场景的结果都写到同一组文件名（metrics.json / access_trace.csv / report.html），
导致连跑多场景后**产物互相覆盖**：实测出现 metrics.json 为 storm4 数据而 report.html 仍为
wenchuan 图表的「张冠李戴」。现改为每次运行落在独立目录
`data/sim/runs/<场景>_<种子>_<时间戳>/`，并写 manifest.json（参数 + git commit + 场景快照）。
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sim.config import DATA_DIR
from sim.scenario import get_scenario
from sim.data_sources import fetch_tle
from sim.orbit import build_timescale, compute_access
from sim.protocol import run_protocol
from sim.eval import compute_metrics, merge_reps, rel17_improvement
from sim.interfaces import write_scenario_json, write_trace_csv, write_metrics_json
from sim.viz import plot_coverage, plot_handover, write_report


def parse_args(argv):
    args = {"seed": 20260901, "reps": 1, "no_viz": False,
            "ho_lead": None, "ephem_err": None, "w_el": None, "w_dwell": None,
            "hyst": None, "compromised": None, "no_link": False,
            "no_priority": False, "priority_cmp": False, "rel17": False,
            "no_pre_migrate": False, "prio_mode": "static"}
    pos = []
    i = 0
    while i < len(argv):
        a = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if a == "--seed" and nxt:
            args["seed"] = int(nxt); i += 2
        elif a == "--reps" and nxt:
            args["reps"] = int(nxt); i += 2
        elif a == "--ho-lead" and nxt:
            args["ho_lead"] = float(nxt); i += 2
        elif a == "--ephem-err" and nxt:
            args["ephem_err"] = float(nxt); i += 2
        elif a == "--w-el" and nxt:
            args["w_el"] = float(nxt); i += 2
        elif a == "--w-dwell" and nxt:
            args["w_dwell"] = float(nxt); i += 2
        elif a == "--hyst" and nxt:
            args["hyst"] = float(nxt); i += 2
        elif a == "--compromised" and nxt:
            args["compromised"] = float(nxt); i += 2
        elif a == "--no-link":
            args["no_link"] = True; i += 1
        elif a == "--no-viz":
            args["no_viz"] = True; i += 1
        elif a == "--no-priority":
            args["no_priority"] = True; i += 1
        elif a == "--priority-cmp":
            args["priority_cmp"] = True; i += 1
        elif a == "--no-pre-migrate":
            args["no_pre_migrate"] = True; i += 1
        elif a == "--prioMode" and nxt:
            args["prio_mode"] = nxt; i += 2
        elif a == "--rel17":
            args["rel17"] = True; i += 1
        else:
            pos.append(a); i += 1
    return pos, args


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=DATA_DIR.parent.parent, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def _sim_core(sc, group, seed, params, no_link):
    """单次仿真核心（单种子）：真实 TLE → 可见窗 → 协议模型 → 指标。
    供主流程与对照实验（优先级增益 / Rel-17 基线）复用，避免重复代码。"""
    sats, prov = fetch_tle(group)
    ts = build_timescale()
    windows, _ = compute_access(sats, sc["lat"], sc["lon"], sc["alt_m"], ts)
    trace, summary = run_protocol(windows, sc, sats=sats, ts=ts,
                                  rng_seed=seed, params=params)
    metrics = compute_metrics(trace, summary)
    return metrics, trace, summary, windows, prov


def main(scenario_key="wenchuan", group="oneweb", seed=20260901, reps=1,
         no_viz=False, overrides=None, no_link=False,
         priority_cmp=False, rel17=False):
    ov = overrides or {}
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

    params = {k: v for k, v in ov.items() if v is not None}
    if no_link:
        params["link_model_on"] = False
    if params:
        print(f"      参数覆盖: {params}")

    reps = max(1, reps)
    all_metrics, last_trace, last_summary = [], None, None
    for r in range(reps):
        rseed = seed + r
        print(f"[4/6] 运行协议模型 seed={rseed} ...")
        trace, summary = run_protocol(windows, sc, sats=sats, ts=ts,
                                      rng_seed=rseed, params=params)
        print(f"      事件总数: {len(trace)}")
        all_metrics.append(compute_metrics(trace, summary))
        last_trace, last_summary = trace, summary
        if reps == 1:
            print("      " + json.dumps(all_metrics[-1], ensure_ascii=False))

    metrics = merge_reps(all_metrics)
    if reps > 1:
        print("      多种子汇总 (mean ± stdev):")
        for k in [x for x in all_metrics[0]]:
            print(f"      {k}: {metrics.get(k)} ± {metrics.get(k + '_stdev')}")

    # ---- 产物落在独立目录，杜绝互相覆盖（★审计修复★）----
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = f"{scenario_key}_s{seed}_r{reps}_{stamp}"
    rundir = DATA_DIR / "runs" / tag
    rundir.mkdir(parents=True, exist_ok=True)

    # ---- ② 生存优先增益对照：同场景关掉优先级重跑，量化 high vs low 差距 ----
    comparison = {}
    if priority_cmp:
        print("[4.5/6] 生存优先对照：同场景 priority_on=False 重跑 ...")
        sc_cmp = dict(get_scenario(scenario_key))
        sc_cmp["priority_on"] = False
        m_off, _, _, _, _ = _sim_core(sc_cmp, group, seed, params, no_link)
        tier_keys = [k for k in metrics if "危终端" in k or "生存优先" in k]
        comparison["priority"] = {
            "优先级开启": {k: metrics.get(k) for k in tier_keys},
            "优先级关闭": {k: m_off.get(k) for k in tier_keys},
        }
        (rundir / "priority_comparison.json").write_text(
            json.dumps(comparison["priority"], ensure_ascii=False, indent=2), encoding="utf-8")
        print("      优先级关闭时 low危终端拒绝数:", m_off.get("low危终端拒绝数"),
              " high危终端拒绝数:", m_off.get("high危终端拒绝数"))

    # ---- ③ Rel-17 基线提升%：受控方案对照（★修复 2026-09-02 第 3 轮★）----
    # 旧实现把「用户传入的场景」直接当提案去比 rel17_baseline，跑 wenchuan（容量64/平缓突发）
    # 对比 rel17_baseline（容量4/呼叫风暴）时出现容量错配伪差（成功率 −70.1%）。
    # 现改为受控实验：提案 = 从 rel17_baseline 继承全部【负载】参数（容量/突发/终端/
    # 伪造比例/危险度分层），仅翻转【接入/切换范式】四项（rach_steps / ho_lead_s /
    # priority_on / pre_migrate），确保对照只反映方案差异。wenchuan_storm2 即等于该受控
    # 提案，结果一致；对任意场景调用 --rel17 都得到同负载受控对照，杜绝错配类伪差。
    if rel17:
        print("[4.6/6] Rel-17 基线对照：受控方案(同负载·仅改范式) ...")
        sc_base = get_scenario("rel17_baseline")
        sc_prop = dict(sc_base)
        sc_prop["name"] = "本方案（两步RACH+预测切换+生存优先+星间预迁移，同负载）"
        sc_prop["rach_steps"] = 2
        sc_prop["ho_lead_s"] = 20.0
        sc_prop["priority_on"] = True
        sc_prop["pre_migrate"] = True
        m_base, _, _, _, _ = _sim_core(sc_base, group, seed, {}, no_link)
        # 公平隔离：同 5s 星历误差
        params_p = dict(params)
        params_p["ephem_err_s"] = 5.0
        m_prop, _, _, _, _ = _sim_core(sc_prop, group, seed, params_p, no_link)
        imp = rel17_improvement(m_base, m_prop)
        comparison["rel17"] = {"基线指标": m_base, "本方案(同误差)指标": m_prop, "提升%": imp}
        (rundir / "rel17_improvement.json").write_text(
            json.dumps(comparison["rel17"], ensure_ascii=False, indent=2), encoding="utf-8")
        print("      相对 Rel-17 提升%:", imp)

    manifest = {
        "run_tag": tag, "scenario_key": scenario_key, "scenario_name": sc["name"],
        "group": group, "seed": seed, "reps": reps,
        "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(),
        "scenario_config": sc,
        "param_overrides": params,
        "link_model_on": (not no_link),
        "provenance": prov,
        "protocol_summary": last_summary,
    }
    write_scenario_json(rundir / "scenario_config.json", sc, prov, windows)
    write_trace_csv(rundir / "access_trace.csv", last_trace)
    write_metrics_json(rundir / "metrics.json", metrics)
    (rundir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"[5/6] 写接口 -> {rundir}")
    if no_viz:
        print("      --no-viz：跳过图表与 HTML 报告")
        return metrics, None, rundir

    print(f"[6/6] 生成图表与报告 ...")
    pc = plot_coverage(windows, sc["name"], outdir=rundir)
    ph = plot_handover(last_trace, sc["name"], outdir=rundir)
    rep = write_report(metrics, prov, sc["name"], pc, ph, rundir / "report.html",
                        manifest, comparison)
    # latest 指针，便于脚本/Web 取最新结果
    (DATA_DIR / "runs" / "latest.json").write_text(
        json.dumps({"run_tag": tag, "path": rundir.as_posix()}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"      报告: {rep.name}")
    return metrics, rep, rundir


if __name__ == "__main__":
    pos, args = parse_args(sys.argv[1:])
    sk = pos[0] if len(pos) > 0 else "wenchuan"
    gp = pos[1] if len(pos) > 1 else "oneweb"
    ov = {k: args[k] for k in ("ho_lead", "ephem_err", "w_el", "w_dwell", "hyst", "compromised")}
    if args["prio_mode"] != "static":
        ov["priority_mode"] = args["prio_mode"]
    if args["no_pre_migrate"]:
        ov["pre_migrate"] = False
    main(sk, gp, seed=args["seed"], reps=args["reps"], no_viz=args["no_viz"],
         overrides=ov, no_link=args["no_link"],
         priority_cmp=args["priority_cmp"], rel17=args["rel17"])
