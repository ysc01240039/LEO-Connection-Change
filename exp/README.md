# exp/ — 新增工作说明（T2 / T3 对比基线矩阵）

> **本文件目的**：明确本次新增工作、对既有仓库代码的改动、以及**已弃置**的旧实现，
> 确保他人 clone 后**可直接运行、不误判**。
>
> 最后更新：2026-09-16

---

## 一、本次新增工作

### T2 切换基线对比矩阵（已完成，Python/ns-3 双轨验证）

**5 基线（1 本职 + 2 现代化业内常用 + 2 近年论文算法）+ 1 消融臂**：

| `ho_policy` | 类别 | 来源 |
|---|---|---|
| `predictive` | 本职 | 本项目：星历预测 + 先建后断 + 星间认证上下文预迁移（RACH-less） |
| `predictive_nopremig` | **消融臂（非基线）** | 本项目：与 `predictive` **同提前量/同候选选择**，仅**关闭预迁移**（切换重 RACH） |
| `cho` | 业内① | 3GPP Rel-16/17 条件切换（TS 38.331） |
| `rel17` | 业内② | 3GPP Rel-17 NTN 标准（反应式 + 四步 RACH，已落地） |
| `dqn` | 论文① | N. Badini, M. Jaber, M. Marchese, F. Patrone, "User-Centric Satellite Handover for Multiple Traffic Profiles Using Deep Q-Learning," **IEEE TAES** 60(6):8591–8604, 2024. DOI:10.1109/TAES.2024.3434771 |
| `graph` | 论文② | S. Eydian, M. Hosseini, G. Karabulut Kurt, "Handover Strategy for LEO Satellite Networks Using Bipartite Graph and Hysteresis Margin," **IEEE OJCOMS** 6:1470–1484, 2025. DOI:10.1109/OJCOMS.2025.3541962 |

- 编排脚本：`perf/crossval_ho.py`（`POLICIES` 已扩为 6 项）
- 结果产物：`perf/crossval_ho_table.txt`、`perf/crossval_ho_results.json`（**以产物为准**）
- 选型说明见 `docs/技术决策确认.md` §3.1 与 `docs/学术基线对照.md` §2.1

**双轨结果快照**（wenchuan / oneweb / seed42；完整表见 `perf/crossval_ho_table.txt`）

| 指标 | predictive | predictive_nopremig | cho | rel17 | dqn | graph |
|---|---|---|---|---|---|---|
| 切换事件数 Py/ns3 | 14931/15092 | 14931/15092 | 14299/14455 | 13616/13780 | 14560/14822 | 13979/14362 |
| 切换中断均值_ms Py/ns3 | **0.01/0** | **0.01/0** | **15.76/15.29** | 380.5/380.5 | **102.2/104** | **16.18/15.25** |
| 切换总时延均值_ms Py/ns3 | **12.14/12.05** | 381/381 | 381.1/381.1 | 380.5/380.5 | 380.9/380.8 | 381.3/381.1 |
| 预迁移命中率 Py/ns3 | **1/1** | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| 乒乓切换率 Py/ns3 | 0.0413/0.0406 | 0.0413/0.0406 | 0.0004/0.0008 | 0/0 | 0/0 | 0/0 |

> **主线结论（两轨一致）**：`predictive` 与 `predictive_nopremig` **切换中断完全相同（0.01/0 ms）**，仅**切换总时延**不同（**12.14 vs 381 ms**）→ ★**归因更正**★：**中断归零来自预测提前量（先建后断 20s 重叠窗）**，而**预迁移的贡献是压缩切换时延 97%**。详见 `docs/学术基线对照.md` §2.1。
> `cho`/`graph` 的 ~15ms 中断尾部两轨已对齐（原 0.09 vs 26.4ms 已消除，见 §六）。
> ★**2026-09-22 双轨对齐**★：`dqn` 原 Python 侧另带在线 Q 表（ns-3 无）致中断 **141.7 vs 104**（差 36%）；移除在线门控后收敛为 **102.2 vs 104**（差 1.7%），见 `docs/技术决策确认.md` §3.2.1 第二轮核查。

**乒乓指标构成分解（★2026-09-16 新增★）**

`乒乓切换率` 是把「切回曾服务星」与「相邻间隔<30s 的快速连切」两个判据**合并**成一个 flag 的结果，掩盖了二者性质差异。现 `sim/eval.py`（唯一指标实现，双轨共用）**分解**输出：

| 指标 | predictive (Py/ns3) | 含义 |
|---|---|---|
| `乒乓切换率` | 0.0413 / 0.0406 | 原合并判据 |
| `乒乓_快速连切率` | 0.0413 / 0.0406 | 相邻切换间隔 < `PINGPONG_MIN_GAP_S` |
| `乒乓_切回率` | 0.0334 / 0.0326 | 窗口内切回曾服务过的星（T6 真正要抑制的对象） |

> 结论：predictive 的乒乓**几乎全部是快速连切分量**——这是它用「多切一次」换取「零中断」的**显式代价**（实测其相邻切换间隔中位 289.7s 正常、仅 4.22% <10s），**须与 `切换中断均值` 并读**，不作单独缺陷指控；基线乒乓为 0 是因其**不提前切换**（LOS 末端强制切），代价是中断 380.5ms。复现：`python exp/diag_pingpong.py <run_dir>`。

### T3 接入握手对比（双轨已完成，3 场景变量隔离）— `exp/t3_access/`

3 方案对比（1 已落地 + 1 论文 + 本项目自身）：

| `rach_scheme` | 类别 | 来源 |
|---|---|---|
| `twostep_precomp` | 本项目 | 两步 RACH + GNSS/星历开环 TA 预补偿 |
| `rel17_4step` | 已落地基线 | 3GPP Rel-17 NTN 四步 RACH |
| `msgarep_2step` | 论文 | T. Kim, S. H. Chae, I. Bang, "Two-Step Random Access With Message Replication for LEO Satellite Networks," **IEEE WCL** 14(4):1134–1138, 2025. DOI:10.1109/LWC.2025.3535231 |

- 编排脚本：`exp/t3_access/run_t3.py`（**3 场景** × 3 方案 × 2 轨；Python 并行、ns-3 串行）
- 结果产物：`exp/t3_access/t3_table.txt`、`exp/t3_access/t3_results.json`
- 完整说明（含容量单位假设、敏感度实测、时延口径）：[`exp/t3_access/README.md`](t3_access/README.md)

**双轨结果（oneweb / seed42，`[errors] {}`）**

场景① `wenchuan_storm2` 窄带（`rach_capacity=4`）：**资源效率主导**

| 指标 | twostep_precomp(本项目) Py/ns3 | rel17_4step(已落地) Py/ns3 | msgarep_2step(论文) Py/ns3 |
|---|---|---|---|
| 接入时延均值_ms | **193.5 / 222.3** | 607.6 / 673 | 116.5 / 266.1 |
| 接入成功率 | **0.7234 / 0.6987** | 0.4083 / 0.4445 | 0.158 / 0.2052 |
| RACH吞吐_终端每秒 | **0.2261 / 0.2222** | 0.1276 / 0.1414 | 0.0494 / 0.0653 |

场景② `wenchuan_storm2_wide` 容量不受限（`rach_capacity=256`）：时延 12.42/12.40 · 399.5/403.5 · 13.31/14.76 ms，**三方案成功率均 1.0** → 证明 msgarep 的窄带劣势**纯来自 ×M 资源代价**（时延恢复至 ≈本项目），非机制无效。

场景③ `wenchuan_storm2_contend` 高冲突（+前导码 64→8）：**★与预期相反，据实记录★**

| 指标 | twostep_precomp | rel17_4step | msgarep_2step |
|---|---|---|---|
| 接入成功率 Py/ns3 | **1 / 1** | 0.9561 / 0.9424 | **0.8025 / 0.7651（低于 rel17）** |

> **核心发现（诚实结论）**：本项目对副本分集采用**忠实资源记账**（M=4 份副本各占**独立**前导资源），故副本的**资源外部性**（多占 4 份前导、推高整体冲突）**超过**其**分集增益** → 把前导冲突变为主导失败源后，**msgarep 成功率反而低于 rel17_4step**。这是**实证结论而非实现缺陷**；也在三个场景下保证了本项目**不靠踩低论文基线取胜**（本项目三场景均不劣于论文方法，且差距可由机制解释：预补偿消除冲突 vs 副本以资源换分集）。若论文原意是「副本复用同一前导」，资源外部性会小得多——该**解释差异已显式披露**，未做替代解释对照（属可选扩展）。
> **关键假设**：为使成功率能区分方案，时隙准入按方案扣减容量单位（2步=1 / 4步=2 / 副本=M=4，`RACH_SLOT_UNITS`）；把副本单位改回 1 时 msgarep 成功率为 **0.7217**（≈本项目），即其窄带低值来自 **×M 资源开销**。
> **时延口径**：`接入时延均值_ms` **仅统计成功终端**（契约 2.1），成功率差异大时存在**幸存者偏差**（窄带场景 Python 轨 msgarep 116.5 ms"最低"、ns-3 轨 266.1 ms，两轨排序不一致）→ **跨方案比较须并读 `接入成功率` 与 `RACH吞吐_终端每秒`**。原「两轨排序逐项一致／本项目最短」的表述**对时延不成立，已更正**。

---

## 二、对既有仓库文件的改动（提交前请 review）

> ⚠️ 以下为 `git status` 实测的**就地修改**的既有文件（共 9 个，会出现在 `git diff`）。
> 若只想单独提交新增工作，请对照本表裁剪。

| 文件 | 改动内容 |
|---|---|
| `sim/protocol.py` | ① 切换策略 dispatch 由 2 扩为 5（`predictive`/`cho`/`rel17`/`dqn`/`graph`）；② 删除 `elevation`/`hysteresis` 分支；③ 新增常量 `_LAMBDA_HO`/`_DQN_*`/`_T_SAFE`/`GRAPH_LOAD_PEN`/`GRAPH_HYST`；④ 新增运行态 `qtab`/`sat_load`；⑤ **T3**：新增 `rach_scheme` 参数（`MSGAREP_M`/`RACH_SCHEMES` 常量 + 接入段三方案分派 + `_preamble_contend(force=)`），**不改既有 `rach_steps` 语义**；⑥ **方案A**：`run_protocol` 新增 `cell_windows`/`grid` 形参，事件循环首行按终端切 `segs`；⑦ **★2026-09-16 第 3 轮★**：新增**消融臂** `predictive_nopremig`；`n_preamble` 支持场景覆盖（高冲突对照）；文件头记录 3 项调查结论（消融臂归因更正 / 乒乓分解 / graph 负载项撤销） |
| `sim/eval.py` | **★新增★** 乒乓**构成分解**指标 `乒乓_快速连切率` / `乒乓_切回率`（按终端重放切换序列，仅用既有 trace 字段，双轨共用） |
| `sim/config.py` | **★新增★** 记录 T2 调查结论注释（候选最小剩余可见过滤的实证否决）；无功能性改动 |
| `sim/scenario.py` | **★新增★** 两个 T3 对照场景：`wenchuan_storm2_wide`（容量不受限）、`wenchuan_storm2_contend`（容量不受限 + 前导码 8 高冲突） |
| `sim/orbit.py` | **方案A**：新增 `GRID_N`/`GRID_STEP_DEG`、`snap_cell()`、`compute_grid_windows()`（原函数未改） |
| `sim/ns3_io.py` | **方案A**：`gen_terminals` 增写 `cell_i,cell_j` 两列；新增 `gen_grid_windows()`。**★2026-09-16★**：`write_ns3_scenario` 增记 `n_preamble`（溯源） |
| `.ns3_ref/leo_access.cc` | ① `DecideHandover` 重构为 5 分支；② 新增全局 `g_nTerms`/`g_satLoad`、常量 `kLambdaHo`/`kTSafe`/`kGraphLoadPen`/`kGraphHyst`、辅助 `elNormLocal`/`vlong`/`bestByLoadScore`；③ 修正过期策略注释与启动日志；④ **T3**：新增 `g_rachScheme`/`kMsgArepM` + 接入段三方案分派 + `--rachScheme` CLI；⑤ **方案A**：新增 `g_termCell`/`g_cellWins` + `readGridWindows()`；⑥ **★2026-09-16 第 3 轮★**：支持 `hoPolicy=predictive_nopremig`（消融臂）；记录 graph 负载项撤销的注释 |
| `run_sim.py` | ① `ho_policy` 等参数透传；② **T3**：新增 `--rach-scheme` CLI 并纳入参数覆盖集；③ **方案A**：`_sim_core`/`main` 各算并传入 `compute_grid_windows(...)` |
| `run_ns3.py` | ① 修复策略参数穿透 bug（原 `ov.get()` 丢弃 CLI 入参）；② **T3**：新增 `--rach-scheme` 并透传 `--rachScheme`；③ **方案A**：写输入时生成 `grid_windows.csv`；④ **★2026-09-16 第 3 轮★**：`--nPreamble` 改为读场景 `n_preamble`（高冲突对照） |
| `docs/技术决策确认.md` | §3.1（+消融臂行/归因更正）、§3.2（+容量与冲突对照、时延口径修正）、§3.3（方案A）、**§3.4（乒乓构成分解）**、**§3.5（graph 负载项 binding 化尝试与撤销）** |
| `docs/学术基线对照.md` | §2.1「T2 对比矩阵落地」重写（补全 graph 文献作者/DOI、dqn 免责标注、消融臂归因更正、GRU-PCHO DOI 存疑标注）；§二 标题改为「降中断 / 降切换时延」 |
| `docs/仿真接口约定.md` | **★2026-09-16★** 指标口径补充：时延「仅成功终端」的**幸存者偏差**使用约束；新增 `乒乓_快速连切率`/`乒乓_切回率` 构成分解；场景 JSON 增 `n_preamble`；§5 增 T2/T3 **近似实现与资源记账**边界声明；§6 增 T2/T3 对照运行命令 |
| `docs/双轨交叉验证对照表.md` | **★2026-09-16 重跑刷新★**：按方案A 网格窗口径重跑 4 场景双轨、刷新全部指标；新增 §0 刷新说明；§5/§6 改为指向 T2/T3 权威矩阵表；§6 改用**消融臂**给出预迁移单变量结论（含归因更正） |
| `README.md` | 顶部新增「新增工作入口」指引，指向 `exp/README.md`（便于他人发现新工作） |

### 新增文件（`git status` 显示为 untracked，与本改动集相互独立）

| 路径 | 说明 |
|---|---|
| `exp/README.md` | **本文件**（新增工作 + 改动 + 弃置 说明） |
| `exp/do_commit.sh` | 一键提交脚本（只暂存本次相关文件） |
| `exp/diag_ho.py` | T2 残差② 的两轨 HANDOVER trace 逐事件 diff 诊断脚本 |
| `exp/diag_pingpong.py` | **★新增★** 乒乓事件构成拆解（切回 vs 快速连切），复现 `docs/技术决策确认.md` §3.4 的调查 |
| `exp/方案A_网格窗实现说明.md` | **方案A（网格窗，两轨可见性严格一致）实现说明**：根因 / 落地方式（Python 供数、ns-3 消费）/ 改动清单 / **实施中修掉的 `nodeId` 键口径 bug** / 验证 |
| `exp/t3_access/` | T3 接入 3 方案双轨实验（脚本 + 结果 + README） |
| `perf/crossval_ho.py` | T2 五策略双轨交叉验证编排器（**新增文件**） |
| `perf/crossval_ho_table.txt` / `perf/crossval_ho_results.json` | 交叉验证产物（**新增文件**） |
| （同目录下 `analyze_fixes.py` / `check_priority.py` / `dp_consistency.py` / `render_report.py` / `test_dp.py`） | 既有未跟踪的审计脚本，非本次新增，勿混入本改动集 |

---

## 三、已弃置的旧实现（**勿再使用**）

| 弃置项 | 弃置原因 | 替代 |
|---|---|---|
| `ho_policy=elevation` | 1990s 传统仰角阈值硬切 | **已删除**（保留 CLI 字段仅为兼容，无实际分支） |
| `ho_policy=hysteresis` | 2008 LTE 滞回，且因默认参数与 `cho` **塌缩为同一结果**（无区分度） | **已删除**；`cho` 取代 |
| 常量 `step4_extra_ms = 400.0` | 单一无源常量直接定义核心结论 | 改为机理函数 `step4_extra_ms()`（RAR 窗口 + 竞争解决定时器 + 4×斜距） |
| 配置项 `elev_th` | `elevation` 删除后成为死配置 | **已从默认参数表删除**；CLI 字段保留兼容，但自 2026-09-22 起**显式传入会打印 `[WARN]` 弃用提示**（不再静默失效） |
| 报告中的「无硬编码」标签 | 与事实不符 | 已删，改为「常量溯源表」 |

---

## 四、新人上手（直接可用）

1. **Python 轨（推演/数据）**
   ```bash
   python run_sim.py wenchuan oneweb --seed 42 --no-viz --ho-policy predictive
   # ho-policy ∈ {predictive, cho, rel17, dqn, graph}
   ```
2. **ns-3 轨（协议级）**
   ```bash
   python run_ns3.py wenchuan oneweb --seed 42 --no-viz --ho-policy cho
   ```
   > 该脚本会自动把 `.ns3_ref/leo_access.cc` 同步到 WSL 的 `/home/mark/ns-3-dev/scratch/` 并重建。
3. **双轨交叉验证**
   ```bash
   python perf/crossval_ho.py     # 跑 5 策略 × 2 轨，产出对比表与 JSON
   ```
4. **依赖**：受管 Python venv（`skyfield`/`sgp4`/`matplotlib`/`pandas`/`numpy`/`requests`/`scipy`）。

---

## 五、注意事项（避免误判）

- **`./ns3 build` 会报 "no work to do" 是正常的**——实际编译的是 WSL 侧 `scratch/` 副本，须经 `run_ns3.py` 的 `cmp+cp` 同步后才重编；交叉验证走 `run_ns3.py` 故已正确生效。
- **反应式策略（rel17 等）的两轨切换事件数有 8–10% 固有差**：两轨为独立 RNG/星历采样，排序与量级一致，属固有特性，非 bug。
- `dqn` 为 DQN **收敛策略的确定性等价实现**（非在线神经网络训练，亦非论文原方法的 **MADQN 集中训练/多智能体**）→ 仅作**机制对照**，不与论文比绝对数值（已免责标注）。
- `graph` 在本模型（无卫星容量约束）下 KM 数学退化为加权 argmax，其唯一杠杆「负载惩罚项」为**弱归一、不 binding**。曾试改瞬时负载 + 峰值归一使其生效，**实证净负面**（Python 轨中断 16.2→112.2 ms、最大 60.4 s，且与 ns-3 发散）→ **已撤回**，`graph` 定位为**近似实现**（见 `docs/技术决策确认.md` §3.5）。
- `predictive` 乒乓率 4.13% 高于基线是**设计代价而非缺陷**：它用「多切一次（快速连切）」换取「零中断」，须与 `切换中断均值` 并读；`sim/eval.py` 已把乒乓分解为 `乒乓_快速连切率`/`乒乓_切回率`（见 `docs/技术决策确认.md` §3.4）。

---

## 六、打包与提交

- `exp/new_work.patch`：**14 个既有文件**的完整 diff（`git diff` 生成，可直接 `git apply` 复核）。
- `exp/` 目录本身即**新增工作**（README + T2/T3 诊断脚本 + T3 实验 + 结果 + patch）。
- `perf/crossval_ho.py`、`perf/crossval_ho_table.txt`、`perf/crossval_ho_results.json` 为 T2 新增文件（需一并 `git add`）。
- ⚠️ **行尾约定**：本仓库 `main` 的文本文件为 **LF**，而本机 `core.autocrlf=true`。
  提交前请确认暂存内容是 LF（否则整篇文件会被判为「已替换」）：
  `git add --renormalize -- <paths>` 后 `git diff --stat main..HEAD` 与
  `git diff --ignore-cr-at-eol --stat main..HEAD` 应**完全一致**。

**一键提交脚本**：`exp/do_commit.sh`（默认 **提交 + 推送**；**一条命令**）
```bash
bash exp/do_commit.sh              # 切/建独立分支 + 只暂存本次相关文件 + 提交 + 推送
bash exp/do_commit.sh --no-push    # 同上，仅本地提交不推送
```

**剩余项（已处理）**：
1. ~~T3 的 ns-3 轨同步~~ —— **已完成**（`.ns3_ref/leo_access.cc` 已镜像 `msgarep_2step`：`g_rachScheme` + `kMsgArepM` + 副本分集分派）。
2. ~~**T2 残差②③④**（两轨可见窗来源不同）~~ —— **已修复（方案 A 已实施）**：
   - **根因**：Python 用**单一场景中心点**窗服务全部终端；ns-3 按**每终端位置**各算窗。终端铺开 ±0.6°（≈±67km）→ 边界判定翻转。
   - **修法**：终端均匀**吸附**到 5×5 格点（中心 ±{0,±0.3°,±0.6°}）→ Python 算出 25 个格点窗写入
     `data/sim/ns3_in/grid_windows.csv` → **两轨读同一份**（ns-3 侧 1200/1200 命中，0 回退）。
   - **效果**（见 `perf/crossval_ho_table.txt`）：`cho` 中断均值 **Py 15.76 / ns-3 15.29 ms**（原 0.09 / 26.4）、
     非零比例 **3.86% / 3.72%**（原 0.01% / 6.4%）；切换事件数缺口由 **8–10% 收窄至 1.1–3.6%**；总时延/预迁移命中率两轨基本逐字一致。
   - 完整说明（含落地中发现并修掉的 `nodeId` 键口径 bug）：[`方案A_网格窗实现说明.md`](方案A_网格窗实现说明.md)。

**仍未完全消除（固有残余，已如实标注）**：
- 反应式策略切换事件数 1.1–3.6% 缺口、`graph` 仰角代价 1.73 vs 1.03、
  `cho/graph` 重连确认失败率 Py 0.034 vs ns-3 0.002 —— 源于两轨**独立 RNG 流 + 终端位置流不同**（Python 用 `rng_seed^0x5EED0000`，
  ns-3 用 `random.Random(seed)`）以及 `graph` 的镜像近似。**不影响任何主结论**（方案排序与量级两轨一致）。
- ~~`dqn` 中断均值 141.7 vs 104~~ → **已消除**（2026-09-22：移除 Python 侧在线 Q 表门控，两轨统一为收敛策略的确定性等价；现 **102.2 vs 104**，差 1.7%）。

---

## 七、第 3 轮修正与调查结论（2026-09-16，本轮新增）

本轮共 **1 项功能新增 + 2 项指标/文档修正 + 3 项调查（含 2 项实证否决、1 项据实反预期）**，全部双轨同步：

| 项 | 类型 | 内容 | 结论 |
|---|---|---|---|
| A | **新增（代码）** | **消融臂** `ho_policy=predictive_nopremig`（同预测提前量、关闭星间预迁移） | ★**更正归因**★：中断归零来自**预测提前量**（两者中断均 0.01 ms）；**预迁移**贡献是**切换时延 ↓97%**（381→12 ms）。原「预迁移使中断↓~100%」表述已更正 |
| B | 指标（代码） | `sim/eval.py` 新增乒乓**构成分解** `乒乓_快速连切率`/`乒乓_切回率` | predictive 4.13% 乒乓**几乎全为快速连切**（保零中断的代价），须与中断并读 |
| C | 调查（**已否决**） | predictive 乒乓「候选最小剩余可见」过滤 | 阈值 50s 无效果；阈值 400s 使中断 0.01→1.88 ms、乒乓反升 → **判定为设计权衡，不改算法**（`sim/config.py` 记录） |
| D | 调查（**已否决**） | graph 负载项「瞬时负载 + 峰值归一」binding 化 | Python 中断 16.2→**112.2 ms**（最大 60.4 s）**且与 ns-3 发散** → **净负面，已完全撤回**（`docs/技术决策确认.md` §3.5） |
| E | **新增（T3）** | 2 个变量隔离场景（`wenchuan_storm2_wide` / `_contend`）+ `n_preamble` 场景可覆盖 | ★**据实反预期**★：高冲突下 msgarep 成功率**低于** rel17（0.80 vs 0.96）——副本的**资源外部性**抵消**分集增益** |
| F | 文档 | 文献核实：补全 `graph` 作者/DOI（10.1109/OJCOMS.2025.3541962）；标注 GRU-PCHO DOI `10.1109/10615358` **存疑待复核**；`dqn` 免责标注 | 三篇论文基线**均已联网核实为真** |

> **本轮的方法学价值**：A 把「零中断」与「低时延」两类收益**分离**；C/D 是**被实证否决并如实记录**的尝试（避免把无效改动当成果）；E 是**反预期的诚实结论**（不靠踩低论文基线取胜）。三者共同强化「可证伪 + 不误导」的交付基线。

