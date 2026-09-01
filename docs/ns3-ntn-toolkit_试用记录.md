# ns3-ntn-toolkit 试用 / 替换评估记录

> 指令：获取 ns3-ntn-toolkit 可用模块，尝试替换本项目 `sim/` 的 L3 协议层（SWAP POINT）；
> 若替换且验证正确后效果更好/更稳定，则删原骨架并更新相关文件；否则保留原骨架并记录。
> 日期：2026-09-01
> **结论：保留原骨架，未替换（本沙箱无法构建运行 C++ 协议模块；纯 Python 模块可用但与现有实现无明确增益）。**

---

## 1. 环境探测（事实）

| 项 | 结果 |
|---|---|
| WSL2（Ubuntu-24.04） | 可用；`/home/mark/ns-3-dev` 存在，版本 **ns-3.47**，但 `contrib/` 为空（无 nr/satellite/mmWave） |
| Docker 守护进程 | **down**（Docker Desktop 未运行）；`registry-1.docker.io` 无响应（Docker Hub 不可达） |
| github.com（Bash 出网） | **不通**（`Failed to connect to github.com:443 ... Could not connect to server`） |
| gitlab.com（Bash 出网） | **通**（git 操作正常，toolkit 的 gitlab 镜像可 clone） |
| 通用 http 代理 | 仅有 CodeBuddy MCP 代理，非 git/https 通用出网代理；WebFetch 能访问但 Bash 直连被限 |

## 2. 已获取的可用模块（gitlab 克隆，存于 `vendor/ns3-ntn-toolkit/`）

| 模块 | 路径 | 对应任务 | 状态 |
|---|---|---|---|
| ntn-cho-framework | `vendor/ns3-ntn-toolkit/ntn-cho-framework` | **T5/T6** 预测式切换 / 切换决策 | ✅ 源码已拿　❌ 不可构建（缺 SNS3 satellite + mmWave） |
| ntn-constellation | `vendor/ns3-ntn-toolkit/ntn-constellation` | **T2** 场景与轨道建模 | ✅ 源码已拿　✅ 纯 Python，冒烟测试通过 |
| ntn-rrc | `vendor/ns3-ntn-toolkit/ntn-rrc` | **T3** 两步接入 TA 预补偿 | ✅ 源码已拿　❌ 不可构建（依赖 SNS3） |
| ntn-sagin | `vendor/ns3-ntn-toolkit/ntn-sagin` | **T9** 空基扩展层 | ✅ 源码已拿　❌ 不可构建（依赖 toolkit 底座） |

> 注：主仓 github.com/Muhammaduazir69/ns3-ntn-toolkit 与 Docker 镜像均不可达；上述 4 个为 gitlab 镜像上能拿到的最小可用单元。

## 3. 为什么 C++ 协议模块在本沙箱无法构建/运行（关键证据）

- `ntn-cho-framework/CMakeLists.txt:57` 链接 `${libsatellite}`；其 `README.md:54` 明确写 **"Required: the SNS3 `satellite` (github.com/sns3/sns3-satellite)"**，并依赖 in-tree `mmwave`（github NYU/CTTC）与 `ntn-traffic`（`NtnRealStackHelper`）。
- **SNS3 satellite 与 mmWave 均托管在 github（当前沙箱 Bash 不通）**；toolkit 源码构建文档亦声明 SNS3 satellite 是"编译期依赖、未 vendored"，须自行获取。
- 预构建 Docker 镜像（`uzairdocker69/ns3-ntn-toolkit:latest`，自带 5G-LENA nr + SNS3 satellite + mmWave + 工具链）是最快可用路径，但 **Docker 守护进程 down 且 Docker Hub 不可达**，无法 pull。
- 综上：本环境既无 github 拉取能力、也无 Docker，C++ 协议模块无法构建/运行，故**无法执行"替换 + 验证正确"**这一前提。

## 4. ntn-constellation（纯 Python）验证

在受管 venv（`envs/default/Scripts/python.exe`）冒烟测试通过：

```
import numpy, skyfield, sgp4        → core deps ok
import ntn_constellation as n       → IMPORT_OK ['CelesTrakFeed','Constellation','Satellite',
                                                'build_isl_topology','cesium_export', ...]
```

它做的是 **TLE → SGP4/SDP4 传播 → SNS3 场景布局 / CesiumJS CZML 导出**。
这与本项目已验证的 `sim/orbit.py`（skyfield/sgp4 可见性）**功能重叠**，且当前无需 SNS3/Cesium 导出格式，
故**无明确的"更正确 / 更稳定"增益**——不替换。

## 5. 保真度诚实注记（影响"是否更正确"的判断）

`ntn-cho` README 自述：切换建模在**决策/时序层**；底层走 mmWave **ideal RRC**（无空口条件重配置 PDU），
RACH 为**时延记账**（中断 = 斜距 RTT + 处理，非真实 PRACH / Msg1–4）。
即其物理层同样是**抽象模型**，与本项目现有 `.ns3_ref/leo_access.cc`（自定义 LeoChannel）保真度相当——
替换不会获得"严格更正确"的协议实现，仅是切换判决更 3GPP 对齐（TTE-aware / Rel-17 CondEvents）。
结合"无法构建"这一硬约束，**替换收益 < 成本**。

## 6. 决策（用户指令的"否则"分支）

- **不删除原骨架**：`run_sim.py` / `sim/` / `.ns3_ref/leo_access.cc` 全部保留。
- **保留已克隆模块**于 `vendor/`（已在 `.gitignore` 隔离），作为未来集成储备。
- 本次走"保留并记录"分支，不更新 AGENTS.md / run_ns3.py 等引用（待真正集成时再更新）。

## 7. 后续可集成路径（待 github / Docker 可用时）

1. **最快**：启动 Docker Desktop 并 `pull uzairdocker69/ns3-ntn-toolkit:latest`，在容器内跑
   `ntn-cho-real-stack`、`ntn-sagin` 参考场景，验证可用性。
2. **或源码构建**：在 WSL 用 git（需 github 通）拉取 SNS3 satellite + mmWave + 5G-LENA nr，
   按 toolkit `INSTALL.md` 构建 ns-3.43。
3. **替换接口**：将 `ntn-cho` 接为 `sim/` 的 L3 协议（SWAP POINT），保持
   `data/sim/ns3_out/access_trace.csv` 字段契约不变
   （`event_type / terminal / tag / t_s / serving_sat / target_sat / value_ms / doppler_hz / slant_km / result / predict_mismatch / pingpong`）；
   可选将 `ntn-constellation` 用于 `sim/orbit.py` 的星历导出。
4. **A/B 对比**：同场景配置分别跑原 Python 骨架与 ns-3 替换版，对比
   接入时延 / 切换中断 / 乒乓率 / 预测失配率 / 成功率，确认实现正确且更优后再删原。

## 8. 当前基线指标（A/B 对比基准，来自 `data/sim/`）

- Python 参考版 `metrics.json`：接入时延 avg **16.68 ms** / p95 24.1 ms，接入成功率 **1.0**，
  切换中断 avg 0.0，乒乓 **0.0**，预测失配 **5.52%**，接入事件 1200，切换事件 60000。
- ns-3 scratch 版 `metrics_ns3.json`：见 `data/sim/metrics_ns3.json`（自定义 LeoChannel 实现）。
