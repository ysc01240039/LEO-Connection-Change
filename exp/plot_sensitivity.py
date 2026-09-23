"""从 results/敏感性分析_20260903.json 重绘 results/敏感性分析_20260903.png（三组扫描，3 面板）。

图三要素（对照任务书硬要求②）：
  · 数据来源：seed 20260901 / commit 7114319 的敏感性扫描 run（每点 run_id 见同名 json）；
  · 对比基线：默认工作点（ho_lead_s=20、ephem_err_s=5）与理论线 1−compromised_share；
  · 一句话结论：预测提前量 ~10-20s 近零中断拐点；中断随星历误差约 5~10s 后陡增；拦截率略高于 1−share。

沿用英文轴标签，避免 CJK 字体缺失。

用法：python logs/plot_sensitivity.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
J = ROOT / "results" / "敏感性分析_20260903.json"
PNG = ROOT / "results" / "敏感性分析_20260903.png"

d = json.loads(J.read_text(encoding="utf-8"))


def sweep(sec, key="切换中断均值_ms"):
    es = sorted(d[sec], key=lambda e: e["value"])
    return [e["value"] for e in es], [e[key] for e in es]


ho_x, ho_y = sweep("ho_lead_s")
ee_x, ee_y = sweep("ephem_err_s")
cs_x, cs_y = sweep("compromised_share", "伪造终端拦截率")
ideal = [1.0 - v for v in cs_x]

fig, ax = plt.subplots(1, 3, figsize=(13.8, 4.1))

ax[0].plot(ho_x, ho_y, "-o", color="#c0392b", lw=2, ms=5)
ax[0].set_title("Interruption vs prediction lead\n(approx zero-interruption knee ~10-20s)", fontsize=10)
ax[0].set_xlabel("ho_lead_s (prediction lead, s)")
ax[0].set_ylabel("mean handover interruption (ms)")
ax[0].grid(alpha=.3)

ax[1].plot(ee_x, ee_y, "-o", color="#2c6fb3", lw=2, ms=5)
ax[1].set_title("Interruption vs ephemeris error\n(sharp rise beyond ~5-10s)", fontsize=10)
ax[1].set_xlabel("ephem_err_s (TLE aging sigma, s)")
ax[1].set_ylabel("mean handover interruption (ms)")
ax[1].grid(alpha=.3)

ax[2].plot(cs_x, cs_y, "-o", color="#27ae60", lw=2, ms=5, label="measured block rate")
ax[2].plot(cs_x, ideal, "--", color="#888", lw=1.6, label="ideal 1 - compromised_share")
ax[2].set_title("Block rate vs compromised share\n(password-layer detection ceiling)", fontsize=10)
ax[2].set_xlabel("compromised_share")
ax[2].set_ylabel("forged-terminal block rate")
ax[2].set_ylim(0, 1.05)
ax[2].grid(alpha=.3)
ax[2].legend(fontsize=8)

fig.suptitle("Sensitivity analysis — data source: seed 20260901 / commit 7114319 (run_id per point in the JSON)",
             fontsize=11)
fig.text(0.5, 0.005,
         "Baseline: default operating point (ho_lead=20s, ephem_err=5s) + ideal line 1-share   |   "
         "Conclusion: interruption knee at ~10-20s lead; sharp rise beyond ~5-10s ephemeris error; "
         "block rate marginally above 1-share",
         ha="center", fontsize=7.5)

fig.tight_layout(rect=[0, 0.03, 1, 0.95])
fig.savefig(PNG, dpi=120)
plt.close(fig)
print("已重绘", PNG, "| points:", len(ho_x), len(ee_x), len(cs_x))
