"""T3 接入握手方案对比（3 方案 × 2 容量场景）—— 双轨（Python / ns-3）编排器。

对比（1 已落地 + 1 论文 + 本项目自身）：
  · twostep_precomp : 本项目 —— 两步 RACH + 星历开环 TA 预补偿（免前导竞争、握手短）
  · rel17_4step     : 已落地基线 —— 3GPP Rel-17 NTN 四步 RACH（前导竞争 + RAR/竞争解决定时器）
  · msgarep_2step   : 论文方法 —— T. Kim et al., "Two-Step Random Access With Message
                      Replication for LEO Satellite Networks," IEEE WCL 2025。
                      两步 RACH 但存在 MsgA 前导冲突，发送 M 份副本，任一份避开冲突即成功。

★为什么跑三个场景（2026-09-16，逐步隔离变量的实证过程）★
  ① `wenchuan_storm2`（窄带 rach_capacity=4）：msgarep 的 ×M=4 资源开销压过其副本分集增益，
     成功率被「资源效率」主导（0.158 vs 本项目 0.7234），论文方法的本意看不出来。
  ② `wenchuan_storm2_wide`（容量不受限 rach_capacity=256，其余同参）：隔离「资源效率」变量。
     **实测三方案成功率均 1.0** —— 说明该规模下**前导冲突也不 binding**（1200 终端/1s 对 64 前导），
     但已可证明 msgarep 的窄带劣势**纯粹来自 ×M 资源代价**（其时延由 116.6/270.8 恢复到 13.3/14.8 ms，
     ≈ 本项目 12.4 ms），而非其机制无效。
  ③ `wenchuan_storm2_contend`（容量不受限 + 前导码 8）：把**前导冲突**变为主导失败源。
     **实测结论与预期相反（据实记录）**：msgarep 成功率**低于** rel17_4step
     （0.8025/0.7651 vs 0.9561/0.9424，双轨一致）。原因：本项目对副本分集采用**忠实资源记账**
     （M=4 份副本各占**独立**前导资源）——副本的**资源外部性**（多占 4 份前导、加剧全体冲突）
     抵消了其**分集增益**。即 Kim et al. (WCL 2025) 的机制在「副本各占独立资源」这一忠实解释下，
     于本模型/本负载区间**不占优**。
  三个场景合起来才能公平评价论文方法：谁在起作用、作用多大、以及在什么条件下失效。

★时延口径（务必并读）★
  `接入时延均值_ms` 按契约 2.1 仅统计**成功终端**（失败事件 value_ms=-1 不计入）。
  因此三方案时延不可单独横向比较——成功率差异大时存在「幸存者偏差」
  （msgarep 成功率低 → 其均值只覆盖少数快速成功的终端）。跨方案比较请并读
  `接入成功率` 与 `RACH吞吐_终端每秒`（二者无该偏差）。

用法：
  python exp/t3_access/run_t3.py
产物：
  exp/t3_access/t3_table.txt
  exp/t3_access/t3_results.json
"""
import subprocess, json, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

WORK = Path("E:/pytorchFile/NationalCreation1")
PY = r"C:\Users\ASUS\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
GROUP = "oneweb"
SEED = 42
SCHEMES = ["twostep_precomp", "rel17_4step", "msgarep_2step"]
SCENARIOS = [
    ("wenchuan_storm2", "窄带：rach_capacity=4（资源效率主导）"),
    ("wenchuan_storm2_wide", "容量不受限：rach_capacity=256（隔离资源效率）"),
    ("wenchuan_storm2_contend", "高冲突：容量不受限 + 前导码 8（隔离前导冲突→检验分集增益）"),
]

KEYS = ["接入时延均值_ms", "接入时延P95_ms", "接入成功率", "RACH吞吐_终端每秒"]


def _run(cmd):
    r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True,
                       timeout=900, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(f"[ERR] {cmd}\n{r.stderr[-600:]}\n")
    return r


def _find_rundir(stdout, marker):
    for line in reversed(stdout.splitlines()):
        if marker in line and "->" in line:
            p = line.split("->", 1)[1].strip()
            if p and (":\\" in p or p.startswith("/")):
                return p
    return None


def _metrics(r, marker):
    rd = _find_rundir(r.stdout, marker)
    if not rd:
        return None, r.stdout[-400:]
    mp = Path(rd) / "metrics.json"
    if not mp.exists():
        return None, f"no metrics.json in {rd}"
    return json.loads(mp.read_text(encoding="utf-8")), rd


def run_py(scenario, scheme):
    return _metrics(_run([PY, "run_sim.py", scenario, GROUP, "--seed", str(SEED),
                          "--no-viz", "--rach-scheme", scheme]), "写接口 ->")


def run_ns3(scenario, scheme):
    return _metrics(_run([PY, "run_ns3.py", scenario, GROUP, "--seed", str(SEED),
                          "--no-viz", "--rach-scheme", scheme]), "产物 ->")


def main():
    res, err = {}, {}
    for scenario, desc in SCENARIOS:
        res[scenario] = {"python": {}, "ns3": {}}
        with ThreadPoolExecutor(max_workers=3) as ex:
            for s, (m, info) in zip(SCHEMES, ex.map(lambda s_: run_py(scenario, s_), SCHEMES)):
                key = f"{scenario}/{s}"
                (res[scenario]["python"] if m else err).__setitem__(
                    s, {k: m.get(k) for k in KEYS} if m else info)
        for s in SCHEMES:                       # ns-3 串行（共享 WSL 构建）
            m, info = run_ns3(scenario, s)
            (res[scenario]["ns3"] if m else err).__setitem__(
                s, {k: m.get(k) for k in KEYS} if m else info)

    lines = [f"T3 接入握手方案对比（双轨 Python/ns-3，{GROUP}，seed={SEED}）", "=" * 104]
    fmt = (lambda v: f"{v:.4g}" if isinstance(v, (int, float)) else str(v))
    for scenario, desc in SCENARIOS:
        lines.append("")
        lines.append(f"场景 {scenario} —— {desc}")
        lines.append("-" * 104)
        lines.append("指标".ljust(18) + "".join(f"{s}(Py/ns3)".rjust(28) for s in SCHEMES))
        for k in KEYS:
            row = k.ljust(18)
            for s in SCHEMES:
                p = res[scenario]["python"].get(s, {}).get(k, "—")
                n = res[scenario]["ns3"].get(s, {}).get(k, "—")
                row += f"{fmt(p)}/{fmt(n)}".rjust(28)
            lines.append(row)
    lines += ["", "注：接入时延按契约 2.1 仅统计**成功终端**（失败不计入），成功率差异大时存在",
              "    幸存者偏差，跨方案比较须并读 `接入成功率` 与 `RACH吞吐_终端每秒`。",
              f"[errors] {err}"]
    txt = "\n".join(lines)
    print(txt)
    (Path(__file__).parent / "t3_table.txt").write_text(txt, encoding="utf-8")
    (Path(__file__).parent / "t3_results.json").write_text(
        json.dumps({"scenarios": [s for s, _ in SCENARIOS], "results": res, "errors": err},
                   ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
