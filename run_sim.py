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
import statistics as st
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from sim.config import DATA_DIR
from sim.scenario import get_scenario
from sim.data_sources import fetch_tle
from sim.orbit import build_timescale, compute_access
from sim.protocol import run_protocol
from sim.eval import (compute_metrics, merge_reps, rel17_improvement,
                     confidence_intervals, ablation_rows)
from sim.interfaces import write_scenario_json, write_trace_csv, write_metrics_json
from sim.viz import plot_coverage, plot_handover, write_report


def parse_args(argv):
    args = {"seed": 20260901, "reps": 1, "no_viz": False,
            "ho_lead": None, "ephem_err": None, "w_el": None, "w_dwell": None,
            "hyst": None, "compromised": None, "no_link": False,
            "no_priority": False, "priority_cmp": False, "rel17": False,
            "no_pre_migrate": False, "prio_mode": "dp",
            "ablation": False, "t8": False, "premigrate_cmp": False}
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
        elif a == "--ablation":
            args["ablation"] = True; i += 1
        elif a == "--t8":
            args["t8"] = True; i += 1
        elif a == "--premigrate-cmp":
            args["premigrate_cmp"] = True; i += 1
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


def _merge_improvements(imp_list):
    """合并多种子 rel17_improvement() 结果：数值键取均值±标准差，非数值键取首个。
    ★P0-4★：让「相对 Rel-17 提升%」这一 headline 具备多种子置信度，而非单种子点估计。"""
    if not imp_list:
        return {}
    out = {}
    for k in list(imp_list[0]):
        vals = [d[k] for d in imp_list if isinstance(d.get(k), (int, float))]
        if vals:
            out[k] = round(st.mean(vals), 2)
            if len(vals) > 1:
                out[k + "_stdev"] = round(st.stdev(vals), 2)
        else:
            out[k] = imp_list[0][k]
    return out


def main(scenario_key="wenchuan", group="oneweb", seed=20260901, reps=1,
         no_viz=False, overrides=None, no_link=False,
         priority_cmp=False, rel17=False, ablation=False, t8=False,
         premigrate_cmp=False):
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
    comparison = {}

    # ---- ① 多种子 95% 置信区间（回答「提升是种子运气还是稳健结论」）----
    ci = {}
    if reps >= 2:
        print("[4.4/6] 计算多种子 95% 置信区间（正态近似 z=1.96）...")
        ci = confidence_intervals(all_metrics)
        (rundir / "confidence_intervals.json").write_text(
            json.dumps({k: list(v) for k, v in ci.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"      置信区间指标数: {len(ci)}")
        comparison["ci"] = ci
        comparison["reps"] = reps

    # ---- ② 生存优先增益对照：同场景关掉优先级重跑，量化 high vs low 差距 ----
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
        print(f"[4.6/6] Rel-17 基线对照（{reps} 种子）：核心方案(无优先) + 全方案(含生存优先) ...")
        sc_base = get_scenario("rel17_baseline")
        # 核心方案：仅翻转接入/切换范式（两步+预测+预迁移），【不含】生存优先调度
        sc_core = dict(sc_base)
        sc_core["name"] = "核心方案（两步RACH+预测切换+星间预迁移，无优先级）"
        sc_core["rach_steps"] = 2
        sc_core["ho_lead_s"] = 20.0
        sc_core["pre_migrate"] = True
        sc_core["priority_on"] = False
        sc_core["ephem_err_s"] = 5.0
        # 全方案：在核心之上叠加生存优先分级调度（本方案最终形态）
        sc_full = dict(sc_core)
        sc_full["name"] = "全方案（两步RACH+预测切换+生存优先+星间预迁移）"
        sc_full["priority_on"] = True
        # ★P0-4★：多种子循环，相对提升%具备置信度（非单种子点估计）
        mbase_list, mcore_list, mfull_list = [], [], []
        impc_list, impf_list = [], []
        for r in range(reps):
            mb, _, _, _, _ = _sim_core(sc_base, group, seed + r, {}, no_link)
            mc, _, _, _, _ = _sim_core(sc_core, group, seed + r, {}, no_link)
            mf, _, _, _, _ = _sim_core(sc_full, group, seed + r, {}, no_link)
            mbase_list.append(mb); mcore_list.append(mc); mfull_list.append(mf)
            impc_list.append(rel17_improvement(mb, mc))
            impf_list.append(rel17_improvement(mb, mf))
        m_base = merge_reps(mbase_list)
        m_core = merge_reps(mcore_list)
        m_full = merge_reps(mfull_list)
        imp_core = _merge_improvements(impc_list)   # 核心：应全为正（证明两步/预测无退化）
        imp_full = _merge_improvements(impf_list)   # 全方案：高危↑ + 低危吞吐权衡
        imp_prio = rel17_improvement(m_core, m_full)   # 优先级效应隔离（核心→全方案）
        comparison["rel17"] = {
            "基线指标": m_base, "核心方案(无优先)指标": m_core, "核心方案提升%": imp_core,
            "全方案(含生存优先)指标": m_full, "全方案提升%": imp_full,
            "优先级隔离提升%(核心→全方案)": imp_prio,
            "种子数": reps, "种子起点": seed,
            "关键指标95%CI": {
                "高危成功率": confidence_intervals(mfull_list, ["high危终端接入成功率"]),
                "接入时延均值": confidence_intervals(mfull_list, ["接入时延均值_ms"]),
            },
        }
        (rundir / "rel17_improvement.json").write_text(
            json.dumps(comparison["rel17"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("      核心方案 相对 Rel-17 提升%:", imp_core)
        print("      全方案 相对 Rel-17 提升%:", imp_full)

    # ---- ④ 三阶段消融实验：隔离每一创新点的边际贡献（★国奖级：增量贡献可证伪★）----
    # 从 rel17_baseline 继承全部【负载】参数（容量/突发/终端/伪造/危险度），仅逐级翻转接入/切换范式：
    #   基线 = 四步RACH + 反应式切换 + 无优先级 + 无星间预迁移
    #   阶段1 = +两步RACH（仅范式1）          → 隔离「两步接入」增益
    #   阶段2 = +预测切换 + 星间预迁移（范式2） → 隔离「预测式切换」增益
    #   阶段3 = +生存优先分级调度（范式3）      → 隔离「生存优先」增益
    # ablation_rows 输出每个 KPI 相对前一阶段的边际提升%，方向统一为正=更好。
    if ablation:
        print(f"[4.7/6] 三阶段消融（{reps} 种子）：逐级翻转接入/切换范式 ...")
        sc_b = get_scenario("rel17_baseline")
        s1 = dict(sc_b); s1["name"] = "阶段1·仅两步RACH"; s1["rach_steps"] = 2
        s2 = dict(s1);  s2["name"] = "阶段2·+预测切换+星间预迁移"
        s2["ho_lead_s"] = 20.0; s2["pre_migrate"] = True; s2["ephem_err_s"] = 5.0
        s3 = dict(s2);  s3["name"] = "阶段3·+生存优先分级调度"; s3["priority_on"] = True
        # ★P0-4★：多种子，逐阶段边际增益用 merge 后的均值 dict 计算，附 _stdev
        mb_list, m1_list, m2_list, m3_list = [], [], [], []
        for r in range(reps):
            mb, _, _, _, _ = _sim_core(sc_b, group, seed + r, {}, no_link)
            a1, _, _, _, _ = _sim_core(s1, group, seed + r, {}, no_link)
            a2, _, _, _, _ = _sim_core(s2, group, seed + r, {}, no_link)
            a3, _, _, _, _ = _sim_core(s3, group, seed + r, {}, no_link)
            mb_list.append(mb); m1_list.append(a1); m2_list.append(a2); m3_list.append(a3)
        m_b = merge_reps(mb_list); m1 = merge_reps(m1_list)
        m2 = merge_reps(m2_list); m3 = merge_reps(m3_list)
        abl = ablation_rows(m_b, [("阶段1·两步RACH", m1),
                                   ("阶段2·+预测切换+预迁移", m2),
                                   ("阶段3·+生存优先调度", m3)])
        comparison["ablation"] = {
            "基线指标": m_b, "阶段1指标": m1, "阶段2指标": m2, "阶段3指标": m3,
            "消融表": abl, "种子数": reps, "种子起点": seed,
        }
        (rundir / "ablation.json").write_text(
            json.dumps(comparison["ablation"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print("      消融：阶段1(两步)时延↓、阶段2(预测)中断↓、阶段3(生存优先)高危↑")

    # ---- ⑤ T8 业务连续性压力对照：业务感知切换 vs 业务无差别 ----
    # 在 t8_stress 场景（约束提前量 4s + 星历误差 5s）下，关闭业务感知(t8_priority_on=False)
    # 时语音中断因预测误差尾部超过 50ms 容忍而显著掉线；开启时语音获 +12s 冗余→语音连续性≈99.9%。
    if t8:
        print(f"[4.8/6] T8 业务连续性压力对照（{reps} 种子）：业务感知 vs 业务无差别 ...")
        sc_t = get_scenario("t8_stress")
        on_s = dict(sc_t);  on_s["t8_priority_on"] = True
        off_s = dict(sc_t); off_s["t8_priority_on"] = False
        # ★P0-4★：多种子循环，语音连续性 headline 附 _stdev
        mon_list, moff_list = [], []
        for r in range(reps):
            a, _, _, _, _ = _sim_core(on_s, group, seed + r, {}, no_link)
            b, _, _, _, _ = _sim_core(off_s, group, seed + r, {}, no_link)
            mon_list.append(a); moff_list.append(b)
        m_on = merge_reps(mon_list)
        m_off = merge_reps(moff_list)
        def _svc(m):
            return {s: m.get(f"{s}业务连续性满足率") for s in ("话音", "图像", "短信")}
        comparison["t8"] = {
            "开启业务感知指标": m_on, "关闭业务感知指标": m_off,
            "开启_各业务连续性": _svc(m_on), "关闭_各业务连续性": _svc(m_off),
            "开启_总体": m_on.get("T8_业务连续性总体满足率"),
            "关闭_总体": m_off.get("T8_业务连续性总体满足率"),
            "种子数": reps, "种子起点": seed,
        }
        (rundir / "t8_comparison.json").write_text(
            json.dumps(comparison["t8"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"      语音连续性  开启={m_on.get('话音业务连续性满足率')}±{m_on.get('话音业务连续性满足率_stdev')}  关闭={m_off.get('话音业务连续性满足率')}±{m_off.get('话音业务连续性满足率_stdev')}")

    # ---- ⑥ 星间认证上下文预迁移单变量对照（★P0-5★）----
    # 支撑计划书「预迁移使单次切换重连总时延 401ms→12ms（↓97%）」硬数字：
    # pre_migrate 开启 → 新星持预置上下文，一次比对即确认（RACH-less，重连额外时延≈0）；
    # pre_migrate 关闭 → 终端须在新星重新完整 RACH（RAR+竞争定时器+额外几何往返），重连额外时延≈400ms。
    if premigrate_cmp:
        print(f"[4.9/6] 星间预迁移单变量对照（{reps} 种子）：pre_migrate 开 vs 关 ...")
        sc_t = get_scenario(scenario_key)
        on_s = dict(sc_t);  on_s["pre_migrate"] = True
        off_s = dict(sc_t); off_s["pre_migrate"] = False
        on_list, off_list = [], []
        for r in range(reps):
            a, _, _, _, _ = _sim_core(on_s, group, seed + r, {}, no_link)
            b, _, _, _, _ = _sim_core(off_s, group, seed + r, {}, no_link)
            on_list.append(a); off_list.append(b)
        m_on = merge_reps(on_list)
        m_off = merge_reps(off_list)
        def _pick(m):
            return {k: m.get(k) for k in ("切换总时延均值_ms", "切换重连额外时延_ms",
                                           "预迁移命中率", "切换中断均值_ms", "切换事件数")}
        ho_off = m_off.get("切换总时延均值_ms")
        ho_on = m_on.get("切换总时延均值_ms")
        drop = round((ho_off - ho_on) / ho_off * 100, 1) if ho_off else None
        comparison["premigrate"] = {
            "开启预迁移": _pick(m_on), "关闭预迁移": _pick(m_off),
            "开启预迁移(完整)": m_on, "关闭预迁移(完整)": m_off,
            "种子数": reps, "种子起点": seed,
            "切换总时延下降%": drop,
        }
        (rundir / "premigrate_comparison.json").write_text(
            json.dumps(comparison["premigrate"], ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"      预迁移 开: 总时延={m_on.get('切换总时延均值_ms')}ms 重连额外={m_on.get('切换重连额外时延_ms')}ms 命中率={m_on.get('预迁移命中率')}")
        print(f"      预迁移 关: 总时延={m_off.get('切换总时延均值_ms')}ms 重连额外={m_off.get('切换重连额外时延_ms')}ms 命中率={m_off.get('预迁移命中率')}   → 切换总时延下降 {drop}%")

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
    if args["prio_mode"] != "dp":
        ov["priority_mode"] = args["prio_mode"]
    if args["no_pre_migrate"]:
        ov["pre_migrate"] = False
    main(sk, gp, seed=args["seed"], reps=args["reps"], no_viz=args["no_viz"],
         overrides=ov, no_link=args["no_link"],
         priority_cmp=args["priority_cmp"], rel17=args["rel17"],
         ablation=args["ablation"], t8=args["t8"],
         premigrate_cmp=args["premigrate_cmp"])
