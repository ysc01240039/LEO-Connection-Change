"""可视化（REPLACEABLE：后续由 Web/Three.js 替代 L5）。
当前产出 PNG 供快速查看；接口产出 JSON 后可无缝接入前端。
中文字体：自动注册 Windows 自带黑体(SimHei)，确保图表无乱码。
matplotlib 惰性加载：仅实际绘图时才导入并注册字体，指标-only 流程零可视化开销。
"""
import base64
import os
from pathlib import Path
from .config import (DATA_DIR, SIM_DURATION_S, TIME_STEP_S, MASK_ANGLE_DEG,
                     ACCESS_PROC_MS, HO_LEAD_S, CARRIER_FREQ_HZ, EIRP_DBM,
                     GT_DBI_K, NOISE_TEMP_K, BIT_RATE_BPS, BER_MODEL,
                     RAR_WINDOW_MS, CONTENTION_TIMER_MS, N_PREAMBLE,
                     EPHEM_ERR_S, HO_W_EL, HO_W_DWELL, AUTH_CPU_DERATE,
                     AUTH_MAC_BYTES, LINK_MODEL_ON)

# ---- 中文字体注册（解决乱码，惰性：首次绘图时执行一次并缓存）----
_PLT = {"module": None}


def _plt():
    if _PLT["module"] is None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        _PLT["module"] = plt
        candidates = [
            r"C:/Windows/Fonts/simhei.ttf",      # 黑体
            r"C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
            r"C:/Windows/Fonts/simsun.ttc",      # 宋体
        ]
        for fp in candidates:
            if os.path.exists(fp):
                try:
                    from matplotlib import font_manager
                    font_manager.fontManager.addfont(fp)
                    name = font_manager.FontProperties(fname=fp).get_name()
                    plt.rcParams["font.family"] = name
                    plt.rcParams["axes.unicode_minus"] = False  # 正常显示负号
                    return plt
                except Exception:
                    continue
        plt.rcParams["axes.unicode_minus"] = False
    return _PLT["module"]


def plot_coverage(windows, scenario_name, outdir=None):
    plt = _plt()
    out = Path(outdir) if outdir else DATA_DIR
    n = int(SIM_DURATION_S / TIME_STEP_S)
    cov = [0] * n
    for w in windows:
        a, l = w["aos_s"] // TIME_STEP_S, min(w["los_s"] // TIME_STEP_S, n - 1)
        for t in range(a, l + 1):
            cov[t] += 1
    xs = [t * TIME_STEP_S / 60.0 for t in range(n)]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, cov, color="#1f77b4")
    ax.set_xlabel("时间 (分钟)"); ax.set_ylabel("可见卫星数")
    ax.set_title(f"{scenario_name}：上空可见卫星数（仰角>{MASK_ANGLE_DEG}°）")
    ax.grid(True, alpha=0.3)
    p = out / "coverage.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    return p


def plot_handover(trace, scenario_name, outdir=None):
    plt = _plt()
    out = Path(outdir) if outdir else DATA_DIR
    ho = [e for e in trace if e.get("event_type") == "HANDOVER"]
    xs = [float(e["t_s"]) / 60.0 for e in ho]
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.scatter(xs, list(range(len(ho))), s=6, color="#d62728")
    ax.set_xlabel("时间 (分钟)"); ax.set_ylabel("切换序号")
    ax.set_title(f"{scenario_name}：切换事件分布（共 {len(ho)} 次）")
    ax.grid(True, alpha=0.3)
    p = out / "handover.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    return p


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def plot_coverage_timeline(cov, step_s, scenario_name, outdir=None):
    """cov: 每步可见卫星数列表；step_s: 采样步长(s)。
    ★修复 2026-09-02★：补齐 outdir 参数（run_ns3.py 调用时传 rundir，
    原签名缺失导致 TypeError），产物落独立目录、不覆盖共享 coverage_ns3.png。"""
    plt = _plt()
    out = Path(outdir) if outdir else DATA_DIR
    n = len(cov)
    xs = [i * step_s / 60.0 for i in range(n)]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, cov, color="#1f77b4", linewidth=1.4)
    ax.fill_between(xs, cov, color="#1f77b4", alpha=0.15)
    ax.set_xlabel("时间 (分钟)"); ax.set_ylabel("可见卫星数")
    ax.set_title(f"{scenario_name}：上空可见卫星数时序（仰角>{MASK_ANGLE_DEG}°，真实星历）")
    ax.grid(True, alpha=0.3)
    p = out / "coverage.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    return p


def _provenance_rows():
    """常量溯源表（★审计修复★：原报告标注「无硬编码」，与事实不符）。

    来源分类：
      【物理】物理常量，不可调
      【标准】行业标准取值
      【实测】由本机实测得到
      【假设】建模假设，直接影响结论，必须做敏感性分析
    """
    items = [
        ("光速 / 玻尔兹曼常数", "299792458 m/s、1.380649e-23 J/K", "【物理】", "无影响"),
        ("载波频率", f"{CARRIER_FREQ_HZ/1e9:.1f} GHz（S 波段）", "【假设】", "影响多普勒量级与 FSPL"),
        ("掩角 MASK_ANGLE_DEG", f"{MASK_ANGLE_DEG}°", "【假设】", "决定可见窗数量与覆盖结论"),
        ("星上接入处理 ACCESS_PROC_MS", f"{ACCESS_PROC_MS} ms", "【假设】", "占接入时延约 23%"),
        ("切换提前量 HO_LEAD_S", f"{HO_LEAD_S} s", "【假设·可扫描】", "**决定中断是否为 0**"),
        ("星历预测误差 EPHEM_ERR_S", f"{EPHEM_ERR_S} s", "【假设·可扫描】", "**决定中断是否可非零**"),
        ("卫星 EIRP", f"{EIRP_DBM} dBm（38 dBW）", "【假设】", "决定 Eb/N0 与误码率"),
        ("终端 G/T", f"{GT_DBI_K} dB/K", "【假设】", "决定 Eb/N0 与误码率"),
        ("噪声温度", f"{NOISE_TEMP_K} K", "【标准】", "—"),
        ("业务速率 BIT_RATE", f"{BIT_RATE_BPS/1e3:.0f} kbps", "【假设】", "决定 Eb/N0（原模型缺失此项）"),
        ("BER 模型", BER_MODEL, "【假设】", "决定虚警率量级"),
        ("MAC 长度", f"{AUTH_MAC_BYTES} 字节（32 bit）", "【假设】", "盲猜漏检概率 2^-32"),
        ("星上 CPU 降频系数", f"{AUTH_CPU_DERATE}×", "【假设·可扫描】", "决定认证引入时延"),
        ("RAR 响应窗口", f"{RAR_WINDOW_MS} ms", "【文献待补 TS 38.321】", "四步附加时延组成"),
        ("竞争解决定时器", f"{CONTENTION_TIMER_MS} ms", "【文献待补 TS 38.321】", "四步附加时延组成"),
        ("前导码数 N_PREAMBLE", f"{N_PREAMBLE}", "【标准】", "决定四步竞争冲突概率"),
        ("选星权重 w_el / w_dwell", f"{HO_W_EL} / {HO_W_DWELL}", "【假设·可扫描】", "决定仰角代价"),
        ("链路模型开关", "开" if LINK_MODEL_ON else "关", "【开关】", "关=退化为纯几何基线"),
    ]
    return "".join(
        f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>" for a, b, c, d in items)


def write_report(metrics, provenance, scenario_name, cov_png, ho_png, out_path,
                 manifest=None, comparison=None):
    """生成自包含 HTML 报告（内嵌图表base64 + 指标 + 数据源溯源 + 常量溯源）。
    comparison: dict（可选）含 'priority'（生存优先对照）与 'rel17'（Rel-17 提升%）块。
    """
    cov_b64 = _b64(cov_png)
    ho_b64 = _b64(ho_png)
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
    prov_rows = _provenance_rows()
    meta_rows = ""
    if manifest:
        meta_rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in [("运行标识", manifest.get("run_tag", "")),
                         ("场景", f"{manifest.get('scenario_key','')} / 种子 {manifest.get('seed','')}"),
                         ("重复次数", manifest.get("reps", "")),
                         ("代码版本", manifest.get("git_commit", "")),
                         ("生成时间", manifest.get("created_utc", "")),
                         ("参数覆盖", manifest.get("param_overrides") or "无"),
                         ("链路模型", "开" if manifest.get("link_model_on") else "关")])
    # ---- ② 生存优先分级调度对照表 ----
    prio_rows = ""
    if comparison and comparison.get("priority"):
        on = comparison["priority"].get("优先级开启", {})
        off = comparison["priority"].get("优先级关闭", {})
        tiers = [k for k in on if "危终端接入成功率" in k]
        prio_rows = "<tr><td>分层</td><td>优先级开启·成功率</td><td>优先级关闭·成功率</td>" \
                    "<td>开启·时延(ms)</td><td>关闭·时延(ms)</td><td>开启·拒绝数</td>" \
                    "<td>关闭·拒绝数</td></tr>"
        for k in tiers:
            t = k.replace("危终端接入成功率", "")
            prio_rows += (f"<tr><td>{t}</td><td>{on.get(k)}</td><td>{off.get(k)}</td>"
                          f"<td>{on.get(t+'危终端时延均值_ms')}</td><td>{off.get(t+'危终端时延均值_ms')}</td>"
                          f"<td>{on.get(t+'危终端拒绝数')}</td><td>{off.get(t+'危终端拒绝数')}</td></tr>")
        gain = on.get("生存优先_时延降低%(high-vs-low)")
        prio_rows += (f"<tr><td colspan='7' class='warn'>生存优先增益（high vs low）："
                      f"成功率差 {on.get('生存优先_成功率差(high-low)')}；"
                      f"高优时延降低 <b>{gain}%</b>（开启优先级时高优终端接入更快/更少被阻塞）</td></tr>")
    # ---- ③ Rel-17 基线提升% 表 ----
    rel17_rows = ""
    if comparison and comparison.get("rel17"):
        imp = comparison["rel17"].get("提升%", {})
        rel17_rows = "<tr><td>指标</td><td>相对 Rel-17 基线提升%</td></tr>"
        label = {"接入时延均值_ms": "接入时延（降低为优）",
                 "接入成功率": "接入成功率（提升为优）",
                 "high危终端接入成功率": "高危终端接入成功率（提升为优）",
                 "切换中断均值_ms": "切换中断时间（降低为优）",
                 "乒乓切换率": "乒乓切换率（降低为优）",
                 "伪造终端拦截率": "伪造终端拦截率"}
        for k, v in imp.items():
            rel17_rows += f"<tr><td>{label.get(k, k)}</td><td><b>{v}</b></td></tr>"
    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>快速接入系统 · 仿真成果报告</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;
   background:#f6f8fa;color:#1f2328;margin:0;padding:28px;}}
 .card{{background:#fff;border:1px solid #e2e6ea;border-radius:10px;padding:20px 24px;margin:0 0 18px;
   box-shadow:0 1px 3px rgba(0,0,0,.05);}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:18px 0 10px;color:#0969da}}
 .meta{{color:#57606a;font-size:13px;line-height:1.7}}
 table{{border-collapse:collapse;width:100%;font-size:14px}}
 td{{border:1px solid #e2e6ea;padding:8px 12px}}
 td:first-child{{background:#f6f8fa;font-weight:600;width:42%}}
 img{{max-width:100%;border:1px solid #e2e6ea;border-radius:8px;margin-top:8px}}
 .tag{{display:inline-block;background:#ddf4ff;color:#0969da;border-radius:6px;
   padding:2px 8px;font-size:12px;margin-right:6px}}
 .warn{{background:#fff8c5;color:#7d4e00;border-radius:6px;padding:8px 12px;font-size:13px}}
</style></head>
<body>
 <div class="card">
  <h1>面向应急救灾的低轨卫星-地面融合组网快速接入系统</h1>
  <div class="meta">仿真成果报告 · 场景：<b>{scenario_name}</b></div>
  <div style="margin-top:10px">
    <span class="tag">真实 TLE 数据源</span>
    <span class="tag">真实轨道计算</span>
    <span class="tag">参数假设已标注</span>
    <span class="tag">模块可替换(ns-3/Web)</span>
  </div>
 </div>
 <div class="card">
  <h2>数据源溯源（可审计）</h2>
  <div class="meta">
   来源：{provenance.get('source','')}<br>
   URL：<a href="{provenance.get('url','')}" target="_blank">{provenance.get('url','')}</a><br>
   抓取UTC：{provenance.get('fetched_utc','')} · 卫星数：{provenance.get('satellite_count','')}
  </div>
 </div>
 <div class="card">
  <h2>统一指标（metrics.json，与 ns-3 trace 契约一致）</h2>
  <table>{rows}</table>
 </div>
 <div class="card">
  <h2>上空可见卫星数（仰角&gt;25°）</h2>
  <img src="data:image/png;base64,{cov_b64}">
 </div>
 <div class="card">
  <h2>切换事件分布</h2>
  <img src="data:image/png;base64,{ho_b64}">
 </div>
 <div class="card">
  <h2>运行溯源（manifest）</h2>
  <table>{meta_rows}</table>
 </div>
 <div class="card">
  <h2>常量与假设溯源表</h2>
  <table>
   <tr><td>常量 / 参数</td><td>取值</td><td>来源类别</td><td>对结论的影响</td></tr>
   {prov_rows}
  </table>
  <div class="warn" style="margin-top:10px">
   <b>关于「无硬编码」：</b>早期版本报告曾标注「无硬编码」，该表述不准确，已更正。
   本仿真的<b>轨道、几何、可见性、多普勒、碰撞退避动力学均由真实 TLE 与物理关系计算</b>；
   但<b>设备与协议层参数（星上处理时延、EIRP、G/T、速率、定时器等）为建模假设</b>，
   已在上表逐项列出。标注为【假设】且影响核心结论的项（切换提前量、星历预测误差、
   星上 CPU 降频系数、选星权重）已支持命令行扫描，敏感性分析见
   <code>perf/scan.py</code> 产出。</div>
 </div>
 <div class="card">
  <h2>说明</h2>
  <div class="warn">当前 L3 协议为参考实现（sim/protocol.py），接口与 ns-3 完全一致；
   ns-3 接入时整体替换该文件、复现同名 trace CSV 即可，L4 评估/L5 可视化零改动。
   信道链路预算（channel.py）为自由空间损耗 + 固定 EIRP/G-T + 比特率的参考模型，
   已于 2026-09-02 接入协议主流程（仰角 → 斜距 → Eb/N0 → BER → MAC 误码 → 虚警率），
   待 ns-3 物理层进一步细化。</div>
 </div>

 <div class="card">
  <h2>② 生存优先分级调度对照（按危险度 tier）</h2>
  <table>{prio_rows}</table>
  <div class="warn" style="margin-top:8px">高优(high=指挥/救援)终端在 RACH 拥塞时拥有专用前导池且退避更短，
   故被碰撞阻塞概率更低、接入时延更小。对照为同场景关闭优先级（priority_on=False）的结果。</div>
 </div>

 <div class="card">
  <h2>③ 相对 Rel-17 基线提升%（本方案 vs Rel-17 标准范式）</h2>
  <table>{rel17_rows}</table>
  <div class="warn" style="margin-top:8px">基线 = rel17_baseline（四步 RACH + 反应式切换 + 无优先级 + 无星间预迁移 + 5s 星历漂移）。
   受控方案 = 从基线继承<b>完全相同负载</b>（容量/突发/终端数/伪造比例/危险度分层），仅翻转接入/切换范式
   （两步 RACH + 预测式提前切换 + 生存优先分级 + 星间认证上下文预迁移），并在相同 5s 星历误差下重跑，
   确保对照只反映方案差异（★修复 2026-09-02：旧版把任意场景当提案，导致容量错配伪差）。
   提升% 实算：<b>降低类</b>=(基线−方案)/基线，<b>提升类</b>=(方案−基线)/基线（成功率越高越好）。
   「持平」=与接入范式无关（如认证拦截率）；「—」=基线为 0 无法算相对值。
   <b>注意：</b>整体接入成功率在生存优先调度下为负值（刻意把容量让给高危终端），
   真正的赢点是<b>高危终端成功率、接入时延、切换中断、乒乓率</b>四项。</div>
 </div>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
