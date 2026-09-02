# ns-3 轨构建与口径说明

> 日期：2026-09-03｜定位：**参考实现**（双轨对照中的 C++ 侧），非独立可复现的仿真引擎。

## 一、口径定位（重要）

本项目"双轨"中，**Python 轨（`run_sim.py` + `sim/`）是唯一可复现、可审计、可评审环境直接运行的仿真主体**；ns-3 轨（`.ns3_ref/leo_access.cc`）是 Python 协议模型 `sim/protocol.py` 的 **C++ 镜像参考实现**，用于验证"同一协议逻辑在离散事件仿真语义下结论一致"。

因此：
- **论文/申报/答辩的指标以 Python 轨为准**；
- ns-3 轨用于「双轨一致性」佐证，其结论 = "同模型 C++ 移植版与 Python 版结果可比"，**不是两套独立设计的互证**；
- 若评审环境无法编译 ns-3，此轨可如实说明为"参考实现，需特定环境"。

## 二、leo_access.cc 是什么

- 1319 行**单翻译单元** ns-3 scratch 程序：`main()` + `LeoApp` / `LeoChannel` / `LeoHeader` 辅助类同文件；
- 实现：两步/四步 RACH、前导竞争退避、T4 HMAC-SHA256 凭证（自实现 FIPS 180-4）、优先级三池（Kaufman-Roberts）、预测式切换 + 迟滞 + 乒乓/失配标记、链路预算 Eb/N0；
- 真实调用 ns-3 `Simulator::Schedule/Run` 离散事件与 `Header/Application/Object` 框架。

## 三、构建前提（无法在云端/评审环境复现）

| 依赖 | 说明 |
|---|---|
| ns-3 环境 | 需完整 ns-3（作者本机为 WSL Ubuntu + ns-3-dev），本仓库**不含** ns-3 本体与 vendor 模块（`.gitignore` 已排除 `vendor/`） |
| 编译方式 | 把 `leo_access.cc` 放入 ns-3 的 `scratch/` 目录，用 `./ns3 build` / `waf` 编译后运行 |
| 数据流 | `run_ns3.py` 生成 `scenario.json` / `ephemeris.csv` / `terminals.csv` 供 C++ 消费，回采 trace 后由 `sim/ns3_io.py` 委托 `eval.compute_metrics` 统计算法（消除双轨口径漂移） |

## 四、已知的环境绑定（不可移植）

- `run_ns3.py` 硬编码作者 WSL 绝对路径（`/mnt/e/pytorchFile/NationalCreation1`、`/home/mark/ns-3-dev`）；
- `perf/` 下诊断脚本硬编码 `C:/Users/ASUS/...`；
- 这些路径**只在本机有效**，换环境须按 `sim/config.py` 的相对路径方案改造后再跑。

## 五、复现步骤（本机）

```bash
# 1. 生成 ns-3 输入（复用 Python 轨的星历与场景）
python run_ns3.py wenchuan oneweb

# 2. 将 leo_access.cc 放入 ns-3 scratch/ 编译
cp .ns3_ref/leo_access.cc <ns-3>/scratch/
cd <ns-3> && ./ns3 build scratch_leo_access

# 3. 运行（参数透传自 run_ns3.py）
./ns3 run scratch_leo_access -- <参数...>
```

> 注意：当前代码（`a1de2bf`，P1/P2 修复后）的 trace 契约已扩到 **16 列**，`leo_access.cc:1290` 需与 `sim/interfaces.py:15` 对齐；旧对照表（2026-09-01）记载的 14 列已过期。

## 六、结论

ns-3 轨成熟度 = **②代码就绪 → ③有待本机重跑**：代码完整可编译，但本仓库无运行产物（trace/metrics 均被 `.gitignore` 排除），"4 场景 WSL 跑通"这一声明无法在评审环境独立复核。建议在最终交付时，**要么**在本机重跑并附产物，**要么**按上文口径明确降级为"参考实现"。
