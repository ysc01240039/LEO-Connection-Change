"""乒乓事件的构成拆解（复现 docs/技术决策确认.md §3.4 的调查）。

用途：判断某策略的「乒乓切换率」究竟是
  (a) 真·切回抖动（窗口内切回曾服务过的星，T6 要抑制的对象），还是
  (b) 由提前切换引起的快速连切（相邻切换间隔 < PINGPONG_MIN_GAP_S，保零中断的代价）。
只看合并后的单一 flag 无法区分二者，故按终端重放切换序列分别统计。

用法：
  python exp/diag_pingpong.py <run_dir>
    <run_dir> 为 run_sim.py 产出的目录（含 access_trace.csv），例如
    data/sim/runs/wenchuan_s42_r1_20260916T090948Z

背景与结论见 docs/技术决策确认.md §3.4（结论：predictive 4.13% 乒乓几乎全部是 (b)，
是其用「多切一次」换取「零中断」的显式代价，故不改算法、改为在指标层分解呈现）。
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

WINDOW_S = 60.0    # 与 config.PINGPONG_WINDOW_S 一致
MIN_GAP_S = 30.0   # 与 config.PINGPONG_MIN_GAP_S 一致


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    rundir = Path(sys.argv[1])
    ho = defaultdict(list)
    with open(rundir / "access_trace.csv", newline="", encoding="utf-8") as f:
        for d in csv.DictReader(f):
            if d["event_type"] != "HANDOVER":
                continue
            ho[d["terminal"]].append((float(d["t_s"]), d["serving_sat"],
                                      d["target_sat"], int(float(d["pingpong"]))))

    n_total = sum(len(v) for v in ho.values())
    n_flag = n_rapid = n_back = 0
    gaps = []
    for evs in ho.values():
        evs.sort()
        hist = []                                   # [(target_sat, t_s)]
        for i, (t, _srv, tgt, flag) in enumerate(evs):
            if i > 0 and (t - evs[i - 1][0]) < MIN_GAP_S:
                n_rapid += 1
            if any(s == tgt and (t - ts) <= WINDOW_S for s, ts in hist):
                n_back += 1
            if flag:
                n_flag += 1
            if i > 0:
                gaps.append(t - evs[i - 1][0])
            hist.append((tgt, t))

    print(f"rundir: {rundir}")
    print(f"HANDOVER 事件总数        : {n_total}")
    print(f"trace 标记 pingpong=1    : {n_flag}  ({n_flag / n_total:.4f})")
    print(f"  └ 快速连切(间隔<{MIN_GAP_S:.0f}s): {n_rapid}  ({n_rapid / n_total:.4f})")
    print(f"  └ 切回曾服务星(≤{WINDOW_S:.0f}s): {n_back}  ({n_back / n_total:.4f})")
    if gaps:
        gaps.sort()

        def q(p):
            return gaps[min(len(gaps) - 1, int(p * len(gaps)))]
        print(f"\n同终端相邻切换间隔(s): min={gaps[0]:.1f} p5={q(.05):.1f} "
              f"p25={q(.25):.1f} 中位={q(.5):.1f} p75={q(.75):.1f} max={gaps[-1]:.1f}")
        for th in (10, 30, 60):
            print(f"  间隔<{th}s 占比: {sum(1 for g in gaps if g < th) / len(gaps):.4f}")


if __name__ == "__main__":
    main()
