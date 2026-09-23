"""契约与口径回归测试（纯 stdlib unittest，无需 pytest）。

运行：
    python -m unittest discover -s tests -v

覆盖：
  1. 模块导入冒烟（含双轨入口脚本）；
  2. trace 契约 17 列（含 service），且列名在 .ns3_ref/leo_access.cc 中均有对应输出；
  3. **口径回归**：《双轨交叉验证对照表》§2（Python 轨）与 results/*.json 逐格一致；
  4. 《对照表》§3（ns-3 轨）与所引 run 的 metrics.json 一致（run 目录缺失时自动跳过）。

设计意图：把「文档数字 == 权威数据」这条硬要求（假期任务书硬要求①）固化为可执行断言，
使后续任何一方漂移都能被 CI 立刻发现。
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOL = 0.006  # 文档多为 2/4 位四舍五入，容差取 0.006

KEYS = ["接入成功率", "接入时延均值_ms", "接入时延P95_ms", "切换中断均值_ms", "切换事件数",
        "预迁移命中率", "预测失配率", "伪造终端拦截率", "RACH吞吐_终端每秒", "切换总时延均值_ms"]

PY_FILES = {
    "wenchuan": "results/wenchuan_metrics.json",
    "henan": "results/henan_metrics.json",
    "wenchuan_storm2": "results/wenchuan_storm2_metrics.json",
    "wenchuan_storm4": "results/wenchuan_storm4_metrics.json",
}
SCENARIOS = tuple(PY_FILES)


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _table_rows(md, start_marker, end_marker):
    seg = md.split(start_marker, 1)[1].split(end_marker, 1)[0]
    rows = []
    for line in seg.splitlines():
        if line.startswith("|") and "---" not in line and "场景" not in line and "关键指标" not in line:
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def _ns3_run_ids(md):
    """从 §3 区域解析 run_id（形如 <场景>_s<种子>_ns3_<UTC时间戳>Z）。"""
    seg = md.split("## 3. ns-3", 1)[1].split("## 4.", 1)[0]
    runs = {}
    for m in re.finditer(r"([a-z0-9_]+?)_s\d+_ns3_\d{8}T\d{6}Z", seg):
        if m.group(1) in SCENARIOS:
            runs[m.group(1)] = m.group(0)
    return runs


class TestImports(unittest.TestCase):
    def test_import_smoke(self):
        import sim.channel      # noqa: F401
        import sim.config       # noqa: F401
        import sim.eval         # noqa: F401
        import sim.interfaces   # noqa: F401
        import sim.ns3_io       # noqa: F401
        import sim.orbit        # noqa: F401
        import sim.protocol     # noqa: F401
        import sim.scenario     # noqa: F401
        import sim.viz          # noqa: F401

    def test_entrypoints_import(self):
        import run_sim          # noqa: F401
        import run_ns3          # noqa: F401


class TestTraceContract(unittest.TestCase):
    def test_17_columns(self):
        from sim.interfaces import TRACE_COLS
        self.assertEqual(len(TRACE_COLS), 17, "trace 契约应为 17 列（2026-09-23 加入 service）")

    def test_service_column_enables_t8(self):
        """契约含 service 列 → 两轨 T8 均可按业务拆分（16 列时代会整体回落 sms）。"""
        from sim.interfaces import TRACE_COLS
        from sim import ns3_io
        from sim.eval import service_continuity_metrics
        self.assertIn("service", TRACE_COLS)
        self.assertEqual(ns3_io.TRACE_FIELDS, list(TRACE_COLS),
                         "ns3_io 回采字段表须与契约同源（勿再本地复制副本）")
        self.assertIn("m_service", _read(".ns3_ref/leo_access.cc"),
                      "ns-3 侧须把 m_service 写入 trace")
        m = service_continuity_metrics([
            {"event_type": "HANDOVER", "value_ms": 1.0, "service": "voice"},
            {"event_type": "HANDOVER", "value_ms": 2.0, "service": "image"},
            {"event_type": "HANDOVER", "value_ms": 3.0, "service": "sms"},
        ])
        for k in ("话音业务连续性满足率", "图像业务连续性满足率", "短信业务连续性满足率"):
            self.assertIn(k, m, "T8 应能按业务拆分：缺 %s" % k)

    def test_service_column_documented(self):
        """17 列契约（含 service）须在接口约定中写明。"""
        doc = _read("docs/仿真接口约定.md")
        self.assertIn("`service`", doc, "service 列须写入 docs/仿真接口约定.md 的契约表")

    def test_columns_present_in_cpp(self):
        cpp = _read(".ns3_ref/leo_access.cc")
        from sim.interfaces import TRACE_COLS
        missing = [c for c in TRACE_COLS if c not in cpp]
        self.assertEqual(missing, [], "这些列名未出现在 .ns3_ref/leo_access.cc：%s" % missing)


class TestNumberConsistency(unittest.TestCase):
    """硬要求①：文档数字必须与权威数据逐格一致。"""

    def _assert_rows_match(self, label, rows, ref_fn):
        bad = []
        for row in rows:
            code = row[0]
            if code not in SCENARIOS:
                continue
            ref = ref_fn(code)
            for i, key in enumerate(KEYS):
                got, exp = float(row[1 + i]), float(ref[key])
                if abs(got - exp) > TOL:
                    bad.append("%s/%s/%s 表=%s 数据=%s" % (label, code, key, got, exp))
        self.assertEqual(bad, [], "口径不符（%d 处）：\n  " % len(bad) + "\n  ".join(bad))

    def test_python_track_matches_results(self):
        md = _read("docs/双轨交叉验证对照表.md")
        rows = _table_rows(md, "## 2. Python", "## 3.")
        self._assert_rows_match("§2", rows, lambda c: json.loads((ROOT / PY_FILES[c]).read_text(encoding="utf-8")))

    def test_ns3_track_matches_run(self):
        md = _read("docs/双轨交叉验证对照表.md")
        rows = _table_rows(md, "## 3. ns-3", "> run_id")
        runs = _ns3_run_ids(md)
        missing = [c for c, tag in runs.items() if not (ROOT / "data/sim/runs" / tag / "metrics.json").exists()]
        if len(runs) < 4 or missing:
            self.skipTest("ns-3 run 目录不在或未解析全（CI 无 data/sim/runs，属预期）：%s" % missing)
        self._assert_rows_match(
            "§3", rows,
            lambda c: json.loads((ROOT / "data/sim/runs" / runs[c] / "metrics.json").read_text(encoding="utf-8")))


    def test_multiseed_artifact_self_consistent(self):
        """多种子产物自洽：10 独立种子 + Rel-17 对照，且与头条数字（12.06 / 380.9）一致。"""
        path = ROOT / "results/多种子统计_storm2_10seed_20260923.json"
        if not path.exists():
            self.skipTest("多种子产物不在（CI 无该文件，属预期）")
        d = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(d["seeds"]["独立种子数"], 10)
        rel = d["rel17_对照_10种子"]
        self.assertEqual(rel["种子数"], 10)
        core = float(rel["核心方案_无优先"]["切换总时延均值_ms"])
        base = float(rel["Rel17四步基线"]["切换总时延均值_ms"])
        self.assertAlmostEqual(core, 12.06, delta=0.01, msg="本方案切换总时延应为 12.06 ms")
        self.assertAlmostEqual(base, 380.914, delta=0.01, msg="Rel-17 基线切换总时延应为 380.91 ms")
        drop = 1.0 - core / base
        self.assertTrue(0.96 < drop < 0.98, f"预迁移降幅应落在 96%~98%（实测 {drop * 100:.2f}%）")

if __name__ == "__main__":
    unittest.main(verbosity=2)
