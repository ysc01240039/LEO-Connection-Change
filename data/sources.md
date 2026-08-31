# 数据来源清单（sources.md）

> 登记规范：URL + 日期 + 用途 + 获取工具（依据 Agents.md 数据管理规范）
> 登记日期：2026-08-31

| 来源 | URL | 获取日期 | 用途 | 获取工具 |
|------|-----|----------|------|----------|
| SNS-3 仓库 | https://github.com/sns3/sns3-satellite | 2026-08-31 | 仿真平台候选（前置任务清单） | WebSearch + GitHub API |
| SNS-3 官方文档 | http://www.sns3.org/doc/satellite-usage.html | 2026-08-31 | 安装流程与文档完整度评估 | WebSearch |
| Hypatia 仓库 | https://github.com/snkas/hypatia | 2026-08-31 | 仿真平台候选（前置任务清单） | WebSearch + GitHub API |
| Hypatia 论文（IMC 2020） | https://bdebopam.github.io/papers/imc2020-hypatia.pdf | 2026-08-31 | 双轨仿真方法论、星座参数来源 | WebSearch |
| ns3-leo 原始仓库（已失效，仅留档） | https://gitlab.ibr.cs.tu-bs.de/tschuber/ns-3-leo | 2026-08-31 | 无法完整获取，已从候选移除 | WebSearch + 用户实测反馈 |
| ns3-leo 维护 fork（已归档只读，仅留档） | https://github.com/dadada/ns-3-leo | 2026-08-31 | 已 archived（2023-11 停更），已从候选移除 | GitHub API |
| 5G-LENA nr 模块（替代项目） | https://gitlab.com/cttc-lena/nr | 2026-08-31 | 仿真平台候选：完整 3GPP NR 协议栈 + NTN/LEO 支持 | GitLab API（2026-08-29 活跃核实） |
| 5G-LENA GSoC 2025 NTN 项目报告 | https://www.nsnam.org/wiki/GSOC2025Ntn | 2026-08-31 | NTN Helper、gsoc-leo-demo-example、LEO 移动性移植说明 | WebSearch |
| 5G-LENA 发布记录（Zenodo） | https://zenodo.org/records/7780746 | 2026-08-31 | 版本兼容矩阵（兼容 ns-3.48）、NTN 示例特性 | WebSearch |
| ns-3 官网 | https://www.nsnam.org/ | 2026-08-31 | ns-3.48 内置 LEO 移动性模型情报 | WebSearch |
| 3GPP NTN 技术综述 | https://3gpp.org/technologies/ntn-overview | 2026-08-31 | CHO 触发条件、移动性管理机制 | WebSearch |
| Frontiers：LEO 切换挑战（2025） | https://dx.doi.org/10.3389/frspt.2025.1580005 | 2026-08-31 | 切换判决方案对比（几何/CHO/SCB） | WebSearch |
| arXiv 2412.00820（6G NTN 综述） | https://arxiv.org/abs/2412.00820 | 2026-08-31 | 预配置切换序列、CHO 分析 | WebSearch |
| ITU Journal 2026（星地 6G 编排） | https://www.itu.int/dms_pub/itu-s/opb/jnl/S-JNL-VOL7.ISSUE2-2026-A13-PDF-E.pdf | 2026-08-31 | NTN 切换综述、灾害应急用例背书 | WebSearch |
| Annales 2024（5G 星地融合） | https://www.annales.org/enjeux-numeriques/2024/en-2024-03/2024-03-24.pdf | 2026-08-31 | RACH-less 切换、Rel-17/18 机制 | WebSearch |
| ShareTechnote NTN RACH | https://w.sharetechnote.com/html/NTN/NTN_RACH.html | 2026-08-31 | 两步 RACH、Rel-17 预补偿强制要求 | WebSearch |
| NXG Connect 5G-NTN 同步 | https://www.nxgconnect.com/post/5g-ntn-dl-ul-timing-synchronization | 2026-08-31 | TA/多普勒预补偿参数体系 | WebSearch |
| MSC-RA（IEEE Systems J. 2019） | https://ui.adsabs.harvard.edu/link_gateway/2019ISysJ..13.2617Z/PUB_HTML | 2026-08-31 | 多星协同随机接入 | WebSearch |
| APL-CRDSA（IEEE Access 2019） | https://ieeexplore.ieee.org/document/8719893 | 2026-08-31 | CRDSA 吞吐改进数据 | WebSearch |
| LC-CRDSA3（Sensors 2021） | https://www.mdpi.com/1424-8220/21/4/1040 | 2026-08-31 | 负载感知接入控制 | WebSearch |
| ACRDA 协同波束成形（Sensors 2023） | https://www.mdpi.com/1424-8220/23/7/3549/xml | 2026-08-31 | ALOHA 家族演进脉络 | WebSearch |
| LEO 间接认证综述（Ad Hoc Networks 2026） | https://www.sciencedirect.com/science/article/pii/S1570870526000648 | 2026-08-31 | 直接/间接认证分类与前沿方向 | WebSearch |
| SatCom 认证综述（IEEE Access 2024） | https://ieeexplore.ieee.org/document/10693434 | 2026-08-31 | PLA 物理层认证、FHAP 切换认证 | WebSearch |
| CelesTrak | https://celestrak.org | 2026-08-31 | 真实星座 TLE 星历数据源 | 知识登记（待 T7 批量抓取） |
| starlink.sx 覆盖可视化 | https://starlink.sx | 2026-08-31 | Demo 可视化参考 | WebSearch |
| 指导教师成果①（航空学报 2026） | 计划书参考文献 [1] | 2026-08-31 | 三层弹性架构母本 | 计划书引用 |
| 指导教师成果②（IEEE IoT J. 2025） | DOI: 10.1109/JIOT.2025.3627629 | 2026-08-31 | 动态假名思想借鉴、威胁模型 | 计划书引用 |
