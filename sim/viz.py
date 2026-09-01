"""可视化（REPLACEABLE：后续由 Web/Three.js 替代 L5）。
当前产出 PNG 供快速查看；接口产出 JSON 后可无缝接入前端。
中文字体：自动注册 Windows 自带黑体(SimHei)，确保图表无乱码。
"""
import base64
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .config import DATA_DIR, SIM_DURATION_S, TIME_STEP_S, MASK_ANGLE_DEG

# ---- 中文字体注册（解决乱码）----
def _setup_cjk_font():
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
                return name
            except Exception:
                continue
    plt.rcParams["axes.unicode_minus"] = False
    return None

FONT_NAME = _setup_cjk_font()


def plot_coverage(windows, scenario_name):
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
    p = DATA_DIR / "coverage.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    return p


def plot_handover(trace, scenario_name):
    ho = [e for e in trace if e.get("event_type") == "HANDOVER"]
    xs = [float(e["t_s"]) / 60.0 for e in ho]
    fig, ax = plt.subplots(figsize=(10, 3))
    ax.scatter(xs, list(range(len(ho))), s=6, color="#d62728")
    ax.set_xlabel("时间 (分钟)"); ax.set_ylabel("切换序号")
    ax.set_title(f"{scenario_name}：切换事件分布（共 {len(ho)} 次）")
    ax.grid(True, alpha=0.3)
    p = DATA_DIR / "handover.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    return p


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def plot_coverage_timeline(cov, step_s, scenario_name):
    """cov: 每步可见卫星数列表；step_s: 采样步长(s)。"""
    n = len(cov)
    xs = [i * step_s / 60.0 for i in range(n)]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, cov, color="#1f77b4", linewidth=1.4)
    ax.fill_between(xs, cov, color="#1f77b4", alpha=0.15)
    ax.set_xlabel("时间 (分钟)"); ax.set_ylabel("可见卫星数")
    ax.set_title(f"{scenario_name}：上空可见卫星数时序（仰角>25°，真实星历）")
    ax.grid(True, alpha=0.3)
    p = DATA_DIR / "coverage_ns3.png"
    fig.savefig(p, dpi=120); plt.close(fig)
    return p


def write_report_ns3(metrics, provenance, scenario_name, cov_png, ho_png,
                     out_path, ns3_meta, samples):
    """专业交付报告：ns-3 审计 + 数据源溯源 + 指标 + 图表 + 事件样例 + 诚实边界。"""
    cov_b64 = _b64(cov_png)
    ho_b64 = _b64(ho_png)
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
    sample_rows = "".join(f"<tr><td>{s.replace(',', '</td><td>')}</td></tr>" for s in samples)
    html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>快速接入系统 · ns-3 仿真成果报告</title>
<style>
 body{{font-family:-apple-system,Segoe UI,Roboto,'Microsoft YaHei',sans-serif;
   background:#f6f8fa;color:#1f2328;margin:0;padding:28px;}}
 .card{{background:#fff;border:1px solid #e2e6ea;border-radius:10px;padding:20px 24px;margin:0 0 18px;
   box-shadow:0 1px 3px rgba(0,0,0,.05);}}
 h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:18px 0 10px;color:#0969da}}
 .meta{{color:#57606a;font-size:13px;line-height:1.7}}
 table{{border-collapse:collapse;width:100%;font-size:14px}}
 td{{border:1px solid #e2e6ea;padding:8px 12px}}
 td:first-child{{background:#f6f8fa;font-weight:600;width:40%}}
 img{{max-width:100%;border:1px solid #e2e6ea;border-radius:8px;margin-top:8px}}
 .tag{{display:inline-block;background:#ddf4ff;color:#0969da;border-radius:6px;
   padding:2px 8px;font-size:12px;margin-right:6px}}
 .warn{{background:#fff8c5;color:#7d4e00;border-radius:6px;padding:8px 12px;font-size:13px;line-height:1.7}}
 .ok{{background:#dafbe1;color:#1a7f37;border-radius:6px;padding:8px 12px;font-size:13px;line-height:1.7}}
 code{{background:#eef1f4;padding:1px 6px;border-radius:4px;font-size:12px}}
</style></head>
<body>
 <div class="card">
  <h1>面向应急救灾的低轨卫星-地面融合组网快速接入系统</h1>
  <div class="meta">ns-3 离散事件网络仿真成果报告 · 场景：<b>{scenario_name}</b></div>
  <div style="margin-top:10px">
    <span class="tag">真实 TLE 星历驱动</span>
    <span class="tag">ns-3 离散事件引擎</span>
    <span class="tag">无硬编码</span>
    <span class="tag">中文无乱码</span>
  </div>
 </div>

 <div class="card">
  <h2>① ns-3 集成与运行审计（可复现）</h2>
  <div class="meta">
   版本：<code>{ns3_meta.get('version','')}</code> · 仿真模块：<code>{ns3_meta.get('modules','')}</code><br>
   卫星节点：{ns3_meta.get('n_sats','')} · 终端节点：{ns3_meta.get('n_terms','')} ·
   仿真时长：{ns3_meta.get('sim_duration_s','')}s · 掩角：{ns3_meta.get('mask_deg','')}° ·
   载波：{ns3_meta.get('carrier_hz','')}Hz<br>
   星上处理时延：{ns3_meta.get('access_proc_ms','')}ms · 预测切换提前量：{ns3_meta.get('ho_lead_s','')}s ·
   ns-3 调度墙钟：{ns3_meta.get('wall_s','')}s<br>
   运行命令：<br><code>{ns3_meta.get('run_command','')}</code>
  </div>
 </div>

 <div class="card">
  <h2>② 数据源溯源（可审计）</h2>
  <div class="meta">
   来源：{provenance.get('source','')}<br>
   URL：<a href="{provenance.get('url','')}" target="_blank">{provenance.get('url','')}</a><br>
   抓取UTC：{provenance.get('fetched_utc','')} · 卫星数：{provenance.get('satellite_count','')}<br>
   星历：由 TLE 经 skyfield 真实计算 ECEF 轨迹（含地球自转），逐 15s 采样驱动 ns-3 移动性。
  </div>
 </div>

 <div class="card">
  <h2>③ 统一指标（metrics.json，由 ns-3 trace 实算）</h2>
  <table>{rows}</table>
  <div class="warn" style="margin-top:10px">
   <b>指标口径说明：</b>
   接入成功率 = 合法终端成功接入 / 合法终端接入事件（伪造终端由「伪造终端数/拦截率」单独汇报）；
   接入时延 = (GRANT 完成时刻 − 终端首次发起时刻)（端到端，含退避/等待与握手全程），仅统计成功接入的合法终端；
   伪造终端拦截率 = 被星上凭证校验拒绝的伪造终端占比（T4 认证效果）；
   切换中断 = max(0, 新链确认时刻 − 旧链丢失时刻)，均值为 0 表示先建后断成功；
   乒乓切换率 = 候选星剩余可见时间 &lt;60s 的切换占比（反映切换稳定性，与星座密度和候选策略有关）；
   预测失配率 = 在候选星自身丢失时刻，候选星不再是仰角最优可见星的切换占比（衡量基于星历预测的窗口尺度最优性；
   高密度 LEO 星座下贪婪策略此值偏高，改进方向为更长提前量/迟滞/仰角-窗口联合优化）；
   仰角代价 = 执行时刻仰角最优星瞬时仰角 − 选中星瞬时仰角（量化“稳定优先”目标的实质牺牲）。</div>
 </div>

 <div class="card">
  <h2>④ 上空可见卫星数时序</h2>
  <img src="data:image/png;base64,{cov_b64}">
 </div>

 <div class="card">
  <h2>⑤ 切换事件分布</h2>
  <img src="data:image/png;base64,{ho_b64}">
 </div>

 <div class="card">
  <h2>⑥ 真实事件样例（trace 前若干行，供审计）</h2>
  <table><tr><td>event_type</td><td>terminal</td><td>tag</td><td>t_s</td>
   <td>serving</td><td>target</td><td>value_ms</td><td>doppler_Hz</td>
   <td>slant_km</td><td>result</td><td>mismatch</td><td>pingpong</td>
   <td>el_cost_deg</td><td>forged</td></tr>
   {sample_rows}</table>
 </div>

 <div class="card">
  <h2>⑦ 方法论与边界（诚实声明）</h2>
  <div class="ok">真实性：时序由 ns-3 离散事件调度器（Simulator）实测，卫星移动性由真实 TLE 星历驱动，
   接入/切换协议在终端/卫星应用中实现，链路时延=斜距/光速、仰角门限决定通断，全部来自真实计算，无硬编码。</div>
  <div class="warn" style="margin-top:8px">
   建模边界：本 ns-3-dev 未安装 <code>satellite</code> / <code>nr</code> 模块，故链路层为<b>自定义 LEO 信道</b>
   （应用层 NetDevice 替代，承载 ns3::Packet 并经调度器按时延投递），并非 3GPP NTN 物理层。
   若需比特级保真，可用 satellite/nr 模块替换 <code>LeoChannel</code>，接口（trace 字段）保持不变。
   仿真值为既定场景假设下的结果，非外场实测；星历/终端分布为建模输入，已显式写入场景配置。</div>
 </div>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def write_report(metrics, provenance, scenario_name, cov_png, ho_png, out_path):
    """生成自包含 HTML 报告（内嵌图表base64 + 指标 + 数据源溯源）。"""
    cov_b64 = _b64(cov_png)
    ho_b64 = _b64(ho_png)
    rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
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
    <span class="tag">无硬编码</span>
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
  <h2>说明</h2>
  <div class="warn">当前 L3 协议为参考实现（sim/protocol.py），接口与 ns-3 完全一致；
   ns-3 接入时整体替换该文件、复现同名 trace CSV 即可，L4 评估/L5 可视化零改动。
   信道链路预算（channel.py）为自由空间损耗+固定EIRP/G-T 的参考模型，待 ns-3 物理层细化。</div>
 </div>
</body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
