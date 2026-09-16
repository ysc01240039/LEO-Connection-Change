"""T2 切换基线：双轨（Python / ns-3）交叉验证编排器。

对 6 个 ho_policy（5 基线 + 1 消融臂）分别跑 Python 轨(run_sim.py) 与 ns-3 轨(run_ns3.py)，
提取关键切换指标并并排对比，判断是否符实（双轨一致、物理可解释）。

  predictive           本职：星历预测提前量 + 先建后断 + 星间认证上下文预迁移（RACH-less）
  predictive_nopremig  ★消融臂★：与 predictive 同提前量/同候选选择，但**关闭预迁移**（切换重 RACH）
                       → 分离「预测提前量收益」与「预迁移零中断收益」
  cho                  3GPP Rel-16/17 条件切换
  rel17                3GPP Rel-17 NTN 反应式
  dqn                  Badini et al., IEEE TAES 2024（DQN 收敛策略的确定性等价）
  graph                Eydian et al., IEEE OJCOMS 2025（二部图加权匹配 + 滞回边）

用法：
  python perf/crossval_ho.py
产物：
  perf/crossval_ho_results.json   （原始指标 + 对比表）
  perf/crossval_ho_table.txt      （人读对比表）
"""
import subprocess, json, re, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

WORK = Path("E:/pytorchFile/NationalCreation1")
PY = r"C:\Users\ASUS\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
POLICIES = ["predictive", "predictive_nopremig", "cho", "rel17", "dqn", "graph"]
SCENARIO = "wenchuan"
GROUP = "oneweb"
SEED = 42

HO_KEYS = [
    "切换事件数", "切换中断均值_ms", "切换中断最大_ms", "切换中断非零比例",
    "乒乓切换率", "乒乓_快速连切率", "乒乓_切回率", "预测失配率", "预迁移命中率",
    "切换总时延均值_ms", "切换重连额外时延_ms", "重连确认失败率", "仰角代价均值_deg",
]


def _run(cmd):
    r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=900,
                      encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(f"[ERR] {cmd}\n{r.stderr[-800:]}\n")
    return r


def _find_rundir(stdout, marker):
    # ★修复（2026-09-15）★：原正则 /[^\s]+ 在 Windows 路径（无前导 /）下会误匹配
    # "[5/6] 写接口 ->" 中的 "/6]"。改为取 marker 之后、经 "->" 分隔的真实路径片段，
    # 并要求其为合法路径（含 ":\\" 或 "/"），兼容 Windows 与 WSL 两种路径。
    for line in reversed(stdout.splitlines()):
        if marker in line and "->" in line:
            p = line.split("->", 1)[1].strip()
            if p and (":\\" in p or p.startswith("/")):
                return p
    return None


def run_python(pol):
    r = _run([PY, "run_sim.py", SCENARIO, GROUP, "--seed", str(SEED),
              "--no-viz", "--ho-policy", pol])
    rd = _find_rundir(r.stdout, "写接口 ->")
    if not rd:
        return None, r.stdout[-400:]
    mpath = Path(rd) / "metrics.json"
    if not mpath.exists():
        return None, f"no metrics.json in {rd}"
    return json.loads(mpath.read_text(encoding="utf-8")), r.stdout


def run_ns3(pol):
    r = _run([PY, "run_ns3.py", SCENARIO, GROUP, "--seed", str(SEED),
              "--no-viz", "--ho-policy", pol])
    rd = _find_rundir(r.stdout, "产物 ->")
    if not rd:
        return None, r.stdout[-800:]
    mpath = Path(rd) / "metrics.json"
    if not mpath.exists():
        return None, f"no metrics.json in {rd}"
    return json.loads(mpath.read_text(encoding="utf-8")), r.stdout


def main():
    results = {"python": {}, "ns3": {}, "errors": {}}
    # Python 六策略并行
    with ThreadPoolExecutor(max_workers=6) as ex:
        fut = {pol: ex.submit(run_python, pol) for pol in POLICIES}
        for pol in POLICIES:
            m, err = fut[pol].result()
            if m is None:
                results["errors"][f"python_{pol}"] = err
            else:
                results["python"][pol] = m
    # ns-3 六策略顺序（共享 WSL 构建目录，避免并发 ./ns3 run 竞争）
    for pol in POLICIES:
        m, err = run_ns3(pol)
        if m is None:
            results["errors"][f"ns3_{pol}"] = err
        else:
            results["ns3"][pol] = m

    # ---- 对比表 ----
    lines = []
    head = f"{'指标':<18}" + "".join(f"{p:>16}" for p in POLICIES)
    lines.append(head)
    lines.append("-" * len(head))
    for k in HO_KEYS:
        row_py = [results["python"].get(p, {}).get(k, "—") for p in POLICIES]
        row_ns = [results["ns3"].get(p, {}).get(k, "—") for p in POLICIES]
        def fmt(v):
            if isinstance(v, float):
                return f"{v:.4g}"
            return str(v)
        lines.append(f"{k+' (Py)':<18}" + "".join(f"{fmt(v):>16}" for v in row_py))
        lines.append(f"{k+' (ns3)':<18}" + "".join(f"{fmt(v):>16}" for v in row_ns))
        lines.append("")

    table = "\n".join(lines)
    (WORK / "perf" / "crossval_ho_table.txt").write_text(table, encoding="utf-8")
    (WORK / "perf" / "crossval_ho_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(table)
    print("\n[errors]", json.dumps(results["errors"], ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
