# 方案 A 实现说明：网格窗（两轨可见性严格一致）

> 状态：**已实施并验证**（2026-09-16）。
> 目标：消除 T2 残差 ②③④ 的**共同根因**——两轨可见窗来源不同。

## 一、根因（已确认）

| 轨 | 原可见窗观察点 | 代码 |
|---|---|---|
| Python | **单一场景中心点**，供全部 1200 终端共用 | `run_sim.py` `compute_access(sats, sc["lat"], sc["lon"], sc["alt_m"], ts)` → `run_protocol(windows, …)` |
| ns-3 | **每终端各自位置** | `ns3_io.gen_terminals` 按 `spread_deg=0.6` 生成每终端位置 → `leo_access.cc` `precomputeWindows()` 用 `g_termPos[termId]` 各算各的窗 |

终端铺开 ±0.6°（≈±67km）→ 两轨窗差异显著 → ns-3 更常"执行时刻无候选"→ 强制切换尾部概率 6.4% vs 0.01%（中断均值 26.4ms vs 0.09ms）；切换次数缺口、T3 成功率偏移同源。

## 二、落地方式（最终实现：Python 供数、ns-3 消费）

原则：**窗集只有一个来源**，避免两轨各自实现吸附逻辑产生浮点/实现分歧。

1. 终端**吸附**到 5×5 格点：中心 ±{0, ±0.3°, ±0.6°}，共 25 个（`GRID_N=5`、`GRID_STEP_DEG=0.3`）
   - 吸附规则：`i = round((lat-clat)/step)`（截断到 ±2），`j` 同理 —— 见 `sim/orbit.snap_cell`
2. Python 算出 25 个格点窗 → 写入 `data/sim/ns3_in/grid_windows.csv`
3. Python 轨：`run_protocol(..., cell_windows=...)`，每终端按吸附格点取窗
4. ns-3 轨：读同一 `grid_windows.csv`，按终端吸附格点取窗（缺失时回退每终端自算并告警）

> 与"两轨各自按同规则吸附再各自算窗"相比，本实现让窗**逐字相同**（同一份 CSV），
> 彻底排除"两轨仰角公式/浮点差异在 15s 分箱边界翻转"的残余风险。

## 三、改动清单（已落地）

| 文件 | 改动 |
|---|---|
| `sim/orbit.py` | `GRID_N`/`GRID_STEP_DEG`、`snap_cell()`、`compute_grid_windows()` |
| `sim/protocol.py` | `run_protocol(..., cell_windows=None, grid=None)`；`term_ll`（每终端 lat/lon）；事件循环首行按终端切换 `segs = segs_by_term.get(k, segs_center)`；`total_dur` 取两种窗源的最大 LOS |
| `run_sim.py` | `_sim_core` 与 `main` 各一处：算 `compute_grid_windows(...)` 并传入 |
| `sim/ns3_io.py` | `gen_terminals` 增写 `cell_i,cell_j` 两列；新增 `gen_grid_windows(cell_windows, out_csv)` |
| `run_ns3.py` | 写输入时同步生成 `grid_windows.csv`（25 格点 / ~1900 窗，约 +4s） |
| `.ns3_ref/leo_access.cc` | 全局 `g_termCell`/`g_cellWins`；`readGridWindows()`；`precomputeWindows()` 优先用共享窗、命中即不自算；`terminals.csv` 解析可选列；`main` 载入并打印命中统计 |

## 四、实施中发现并修掉的 bug（务必留意）

**`g_termCell` 键口径错误（已修）**：终端格点最初按 **CSV 的 `term_id`** 存入，但 `g_termPos` 是按 **ns-3 nodeId** 索引（卫星节点先创建，终端 nodeId 从 651 起）。
→ 命中数恰好 = `[0,1199] ∩ [651,1850]` = **549**，其余 651 终端回退自算（日志 `终端命中=549 回退自算=651`）。
→ 修法：CSV 解析阶段先存 `termCellTmp`（按 term_id），建节点时再 `g_termCell[nodeId] = termCellTmp[i]`。
→ 修复后日志：`终端命中=1200 回退自算=0`。

**经验**：ns-3 侧凡"按终端索引"的容器，一律以 `nodeId` 为准（与 `g_termPos`、`g_termWins` 一致），CSV 的 `term_id` 只用于解析阶段。

## 五、验证

```bash
# 单轨冒烟（应看到"终端命中=1200 回退自算=0"）
python run_ns3.py wenchuan oneweb --seed 42 --no-viz --ho-policy predictive
# 双轨交叉验证
python perf/crossval_ho.py
```

**注意**：启用后窗集由"中心点"变为"格点"，**全部 T2/T3 数值均已变动**，故已重跑并更新
`perf/crossval_ho_table.txt`、`exp/t3_access/t3_table.txt` 及相关文档中的指标表。
