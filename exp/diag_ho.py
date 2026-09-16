"""T2 残差② 诊断：逐事件对比两轨 HANDOVER trace，定位首个分歧。

用法：
  python exp/diag_ho.py [policy]     # 默认 cho
"""
import subprocess, sys, csv
from pathlib import Path
from collections import defaultdict

WORK = Path("E:/pytorchFile/NationalCreation1")
PY = r"C:\Users\ASUS\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
SCEN, GRP, SEED = "wenchuan", "oneweb", 42
POL = sys.argv[1] if len(sys.argv) > 1 else "cho"


def _run(cmd, marker):
    r = subprocess.run(cmd, cwd=WORK, capture_output=True, text=True, timeout=900,
                       encoding="utf-8", errors="replace")
    for line in reversed(r.stdout.splitlines()):
        if marker in line and "->" in line:
            p = line.split("->", 1)[1].strip()
            if p and (":\\" in p or p.startswith("/")):
                return Path(p)
    raise SystemExit(f"no rundir; tail:\n{r.stdout[-500:]}\n{r.stderr[-500:]}")


def load_ho(rd):
    """terminal -> [(t_s, target_sat, value_ms), ...]，仅 HANDOVER。"""
    d = defaultdict(list)
    with open(rd / "access_trace.csv", newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["event_type"] == "HANDOVER":
                d[int(row["terminal"])].append(
                    (float(row["t_s"]), str(row["target_sat"]), float(row["value_ms"])))
    return d


def _sample(rd):
    with open(rd / "access_trace.csv", newline="", encoding="utf-8") as f:
        rd_ = csv.DictReader(f)
        for row in rd_:
            if row["event_type"] == "HANDOVER":
                return {k: row[k] for k in ("terminal", "t_s", "serving_sat", "target_sat", "value_ms")}
    return {}


py_rd = _run([PY, "run_sim.py", SCEN, GRP, "--seed", str(SEED), "--no-viz", "--ho-policy", POL],
             "写接口 ->")
n3_rd = _run([PY, "run_ns3.py", SCEN, GRP, "--seed", str(SEED), "--no-viz", "--ho-policy", POL],
             "产物 ->")
py, n3 = load_ho(py_rd), load_ho(n3_rd)

np_ = sum(len(v) for v in py.values())
nn_ = sum(len(v) for v in n3.values())
print(f"policy={POL}  HANDOVER总数 Py={np_} ns3={nn_}")
print(f"Py  sample: {_sample(py_rd)}")
print(f"ns3 sample: {_sample(n3_rd)}")

# 首个分歧：按 terminal 逐个比事件序列
first = None
n_div_term = 0
div_examples = []
for k in sorted(set(py) | set(n3)):
    a, b = py.get(k, []), n3.get(k, [])
    if a != b:
        n_div_term += 1
        if first is None:
            # 找该终端首个不同下标
            for i in range(max(len(a), len(b))):
                ea = a[i] if i < len(a) else None
                eb = b[i] if i < len(b) else None
                if ea != eb:
                    first = (k, i, ea, eb, len(a), len(b))
                    break
        if len(div_examples) < 6:
            div_examples.append((k, len(a), len(b)))

print(f"\n有分歧的终端数: {n_div_term} / {len(set(py)|set(n3))}")
if first:
    k, i, ea, eb, la, lb = first
    print(f"\n首个分歧: terminal={k} 第{i}个HANDOVER")
    print(f"  Py  : t_s={ea[0]} target={ea[1]} value_ms={ea[2]}  (该终端共{la}次)")
    print(f"  ns3 : t_s={eb[0]} target={eb[1]} value_ms={eb[2]}  (该终端共{lb}次)")
    print(f"  Δt_s={abs(ea[0]-eb[0]):.3f}s")
print("\n分歧样例(terminal, Py次数, ns3次数):", div_examples)
