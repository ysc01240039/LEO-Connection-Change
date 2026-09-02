/* -*- Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
 * ============================================================================
 *  ns-3 LEO 快速接入与预测式切换仿真（真实 TLE 驱动）
 *
 *  集成方式（面向应急救灾低轨卫星-地面融合组网快速接入系统）：
 *   - 卫星移动性由真实 TLE 计算的 ECEF 轨迹逐步驱动；
 *   - 自定义 LEO 链路模型：传播时延 = 斜距 / 光速，仰角门限决定通断；
 *   - 终端应用实现「两步接入握手（含 Timing Advance / 多普勒预补偿）」与
 *     「先建后断预测式切换」；卫星应用回送 Grant / Confirm；
 *   - 全部时序由 ns-3 离散事件调度器（Simulator）实测，事件落盘 trace。
 *
 *  说明：本 ns-3-dev 未安装 satellite / nr 模块，故链路层为自定义 LEO 信道
 *  （非 3GPP NTN PHY）；时序、节点、移动性、包传输均为真实 ns-3 引擎。
 *  如需比特级保真，可用 satellite / nr 模块替换 LeoChannel。
 * ============================================================================
 */
#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/mobility-module.h"

#include <cmath>
#include <cstring>
#include <map>
#include <vector>
#include <string>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <random>
#include <chrono>
#include <tuple>

using namespace ns3;

class LeoApp;   // 前向声明：LeoChannel 引用

NS_LOG_COMPONENT_DEFINE("LeoAccess");

// ============================ 常量 ============================
static const double C_KM_S = 299792.458;   // 光速 km/s
static const double PI = 3.14159265358979323846;

// ============================ 数据结构 ============================
struct Ecef { double x, y, z; };
struct Window { uint32_t satId; double aos; double los; };

// 全局状态（单翻译单元，scratch 脚本可接受）
static std::map<uint32_t, std::vector<Ecef>> g_eph;     // satNodeId -> 轨迹(km)
static std::map<uint32_t, double>                 g_stepDt; // satNodeId -> 采样步长(s)
static std::map<uint32_t, Ecef>                  g_termPos; // termNodeId -> ECEF(km)
static std::map<uint32_t, std::vector<Window>>   g_termWins; // termNodeId -> 可见窗
static std::map<uint32_t, Ptr<LeoApp>>           g_apps;     // nodeId -> App
static NodeContainer                            g_satNodes;  // 卫星节点容器
static std::map<uint32_t, uint32_t>             g_satId2Idx; // satNodeId -> 索引

// ---- 协议/物理参数默认值（★镜像 sim/config.py，仅供直接运行 scratch 时兜底★）----
// 正式驱动（run_ns3.py）会将全部参数经命令行显式透传覆盖此处默认值，
// 修改参数请改 sim/config.py / sim/scenario.py，勿只改此处（双轨会漂移）。
static double g_maskDeg = 25.0;
static double g_carrierHz = 2.0e9;
static double g_hoLeadS = 20.0;
static double g_tickS = 1.0;
static double g_accessProcMs = 3.0;
static double g_simDur = 3600.0;
// ---- T4 认证 / RACH 基线 / 碰撞拥塞（与 Python 轨 scenario.py 同参）----
static double     g_authExtraMs    = 0.0;    // 星上轻量凭证校验额外时延(ms，实测折算)
static uint32_t   g_rachSteps      = 2;      // 2=两步预补偿；4=Rel-17 四步基线
static double     g_forgedRatio    = 0.0;    // 伪造终端占比
static double     g_compromisedShare = 0.15; // 伪造终端中持有效密钥比例（→漏检率）
static bool       g_collisionOn    = false;  // 碰撞/拥塞模型开关
static uint32_t   g_rachCapacity   = 64;     // 每 10ms 时间片星上受理上限
static double     g_retryIntervalMs = 500.0; // 碰撞退避均匀抽样上限(ms)
static uint32_t   g_retryMax       = 20;     // 碰撞重试上限（超限判失败）
// ---- ★审计修复 2026-09-02：新增机理参数（与 sim/config.py 同参）----
static double     g_ephemErrS      = 0.0;    // 星历预测误差 σ(s)：0=完美预测
static double     g_wEl            = 0.5;    // 选星仰角权重
static double     g_wDwell         = 0.5;    // 选星驻留权重
static double     g_hoHyst         = 0.0;    // 切换迟滞（score 单位）
static uint32_t   g_rarWindowMs    = 160.0;  // 四步 RAR 响应窗口(ms)
static uint32_t   g_contTimerMs    = 200.0;  // 四步竞争解决定时器(ms)
static uint32_t   g_nPreamble      = 64;     // 前导码数量（四步竞争）
static double     g_eirpDbm        = 68.0;   // 卫星 EIRP(dBm)
static double     g_gtDbiK         = -20.0;  // 终端 G/T(dB/K)
static double     g_noiseTempK     = 290.0;  // 噪声温度(K)
static double     g_bitRateBps     = 100e3;  // 业务速率(bps)
static bool       g_linkModelOn    = true;   // 链路模型开关
static bool       g_preMigrate     = true;   // D3：认证上下文星间预迁移开关（镜像 Python 轨）
// D3：每星独立认证上下文 {satId -> {term_id -> counter}}，预迁移机制的状态载体。
// ★P2★ 键用 term_id（内部稳定标识）而非信令假名——假名每次切换轮换，内部索引须稳定
// （镜像 Python protocol.sat_ctx，键=term_id）。
static std::map<uint32_t, std::map<uint32_t, uint32_t>> g_satCtx;
static uint64_t   g_rngSeed        = 20260901; // 运行种子（★可配置，原固定 12345★）
static uint32_t   g_macBytes       = 4;      // MAC 截断长度(字节) → 盲猜 2^-32
static std::map<std::pair<uint32_t,uint32_t>, uint32_t> g_slotLoad; // (sat,10ms槽)->已受理数
static std::map<std::tuple<uint32_t,uint32_t,uint32_t>, uint32_t> g_preambleUse; // (sat,槽,前导)->终端
static std::map<uint32_t, uint32_t> g_lastCounter;  // pseudo -> 已见最大 counter（重放检测）
static std::mt19937 g_runRng(20260901);                            // 运行时退避 RNG
static std::uniform_real_distribution<double> g_u01(0.0, 1.0);

// ---- ★生存优先分级调度（★镜像 sim/protocol.py _slot_ok / PRIO_BACKOFF★）----
// 原 ns-3 轨仅给终端贴 high/med/low 标签，但 RACH 拥塞下各 tier 平等竞争，
// 「指挥/救援终端优先接入」并未真正生效。现镜像 Python 轨 priority-aware RACH（三池版）：
//   · high/med/low 各占专用预留池（比例见 g_prioReserveFrac[3]，同 config.PRIORITY_RESERVE_FRAC），
//     严格隔离 → high>med>low 单调成立（消除原 2 池 med<low 假象）；
//   · 高优退避更短 → 重发更快、接入时延更低。
// priority_on=False 时为「无优先级基线」（所有 tier 共用单池 g_slotLoad），用于量化增益。
static bool       g_priorityOn     = true;  // 生存优先调度开关（default true，同 config）
static double     g_prioReserveFrac[3] = {0.25, 0.10, 0.65}; // high/med/low 专用预留比例（同 config.PRIORITY_RESERVE_FRAC，与 Python 轨一致）
static double     g_prioBackoff[3] = {0.3, 0.6, 1.0}; // high/med/low 退避缩放（同 config.PRIO_BACKOFF）
static std::map<std::pair<uint32_t,uint32_t>, uint32_t> g_slotP[3]; // 三档专用池 (sat,10ms槽)->已受理数
// ---- ★科学版 dp 自适应阈值（★镜像 sim/protocol.py priority_mode="dp" + sim/prio_opt.py★）----
// 替代静态比例 g_prioReserveFrac：用 Kaufman-Roberts 生灭过程精确解，按在线 EWMA 估计的
// 各档到达率，每窗口重算最优 guard-channel 阈值 (g_h, g_m)，低危负载时回收高危闲置预留。
static std::string g_priorityMode = "dp"; // "static" | "dp"（default=dp：Kaufman-Roberts 自适应保护位，与 Python 轨 _slot_ok dp 分支机理一致；双轨对齐）
static double g_prioEps      = 0.12;  // 高危阻塞率 QoS 上界 ε（同 config.PRIO_EPS）
static double g_prioBeta     = 0.30;  // EWMA 新窗口权重（同 config.PRIO_BETA）
static double g_prioWm       = 1.00, g_prioWl = 1.00; // 目标函数权重(中,低)（同 config.PRIO_WEIGHTS）
static double g_prioLoadCal  = 0.80;  // 损失模型负载标定：模型偏保守，乘此系数对齐仿真实测（同 config.PRIO_LOAD_CAL）
static double g_prioAdaptWinS= 1.00;  // 阈值自适应窗口(s)（同 config.PRIO_ADAPT_WIN_S）
static std::map<std::pair<uint32_t,uint32_t>, uint32_t> g_dpOcc;     // (sat,10ms槽)->总占用
static std::map<std::pair<uint32_t,uint32_t>, double>   g_dpEwma;    // (sat,tier)->λ EWMA
static std::map<std::pair<uint32_t,uint32_t>, double>   g_dpWincnt;  // (sat,tier)->当前窗口到达计数
static std::map<uint32_t, int64_t> g_dpWin;        // sat -> 当前窗口索引
static std::map<uint32_t, std::pair<int,int>> g_dpGuards; // sat -> (g_h, g_m) 当前最优阈值
static std::map<uint32_t, uint64_t> g_dpReclaim;  // sat -> med/low 占用高危预留区回收次数
static double   g_dpSumGh = 0.0;  // 跨窗口 g_h 累加（报告平均预留）
static uint64_t g_dpNGuard = 0;   // 阈值重算次数
static uint32_t g_dpDefGh = 2, g_dpDefGm = 1; // 默认阈值（static 比例推导，首窗口前兜底）
static uint32_t g_dpSlotPerWin = 100; // 每窗口 10ms 槽数 = adaptWinS/0.01
static std::ofstream g_trace;
static uint64_t g_dbg_attempt = 0, g_dbg_req = 0, g_dbg_forged_blocked = 0,
                g_dbg_collision_fail = 0, g_dbg_forged_missed = 0,
                g_dbg_confirm_fail = 0, g_dbg_rerach = 0;
// ★P1/P2 双轨对齐统计★：镜像 Python protocol.py summary 字段
static uint64_t g_dbg_premig_hit = 0, g_dbg_premig_miss = 0, g_dbg_pseudo_rotation = 0;
static double   g_dbg_ho_total_ms = 0.0, g_dbg_rerach_extra_ms = 0.0;

// ============================ 向量/几何辅助 ============================
static double norm3(const Ecef& a){ return std::sqrt(a.x*a.x+a.y*a.y+a.z*a.z); }
static Ecef  sub(const Ecef& a, const Ecef& b){ return {a.x-b.x,a.y-b.y,a.z-b.z}; }
static double dot3(const Ecef& a, const Ecef& b){ return a.x*b.x+a.y*b.y+a.z*b.z; }
static Ecef cross3(const Ecef& a, const Ecef& b){
  return {a.y*b.z-a.z*b.y, a.z*b.x-a.x*b.z, a.x*b.y-a.y*b.x};
}

static double elevationDeg(const Ecef& t, const Ecef& s){
  double tu = norm3(t);
  Ecef up = {t.x/tu, t.y/tu, t.z/tu};
  Ecef zhat = {0,0,1};
  Ecef east = cross3(zhat, up); double en = norm3(east); east = {east.x/en, east.y/en, east.z/en};
  Ecef north = cross3(up, east);
  Ecef sv = sub(s, t);
  double e = dot3(sv, east), n = dot3(sv, north), u = dot3(sv, up);
  return std::atan2(u, std::sqrt(e*e + n*n)) * 180.0 / PI;
}

static double rangeKm(const Ecef& a, const Ecef& b){ return norm3(sub(a,b)); }

// 防御：Windows 写的 CSV 可能带 \r，getline 后剔除
static void strip_cr(std::vector<std::string>& c){
  for (auto& s : c) if (!s.empty() && s.back() == '\r') s.pop_back();
}

// 在时刻 t 插值卫星位置
static Ecef satPosAt(uint32_t satId, double t){
  auto it = g_eph.find(satId);
  if (it == g_eph.end() || it->second.empty()) return {0,0,0};
  const std::vector<Ecef>& v = it->second;
  double dt = g_stepDt[satId];
  double f = t / dt;
  if (f <= 0) return v.front();
  if (f >= v.size()-1) return v.back();
  size_t i0 = (size_t)std::floor(f);
  double frac = f - i0;
  const Ecef& a = v[i0]; const Ecef& b = v[i0+1];
  return {a.x+(b.x-a.x)*frac, a.y+(b.y-a.y)*frac, a.z+(b.z-a.z)*frac};
}

// 多普勒(带符号)：沿 LOS 的相对径向速度 * f/c
static double dopplerHz(uint32_t satId, const Ecef& t, double tNow){
  double dt = 1.0;
  Ecef p1 = satPosAt(satId, tNow - dt);
  Ecef p2 = satPosAt(satId, tNow + dt);
  Ecef vrel = {(p2.x-p1.x)/(2*dt), (p2.y-p1.y)/(2*dt), (p2.z-p1.z)/(2*dt)}; // km/s
  double rg = rangeKm(t, satPosAt(satId, tNow));
  if (rg < 1e-6) return 0;
  Ecef u = sub(satPosAt(satId, tNow), t);
  double un = norm3(u); u = {u.x/un, u.y/un, u.z/un};
  double vlos = dot3(vrel, u);
  return -vlos / C_KM_S * g_carrierHz;
}

// 某终端在时刻 t 的可见卫星（窗口包含 t），返回 (satId, remaining, los)
static std::vector<std::tuple<uint32_t,double,double>>
visibleAt(uint32_t termId, double t){
  std::vector<std::tuple<uint32_t,double,double>> out;
  auto it = g_termWins.find(termId);
  if (it == g_termWins.end()) return out;
  for (const auto& w : it->second){
    if (w.aos <= t && t <= w.los){
      out.push_back({w.satId, w.los - t, w.los});
    }
  }
  return out;
}

// ============================ 生存优先 RACH 容量判定（★镜像 Python protocol._slot_ok★） ============================
// 优先级映射：high=0 / med=1 / low=2（同 config.TIER_ORDER）。
// priority_on=False：所有 tier 共用单池 g_slotLoad（无优先级基线）。
static double prioBackoffScale(uint32_t prio){
  if (!g_priorityOn) return 1.0;
  if (prio < 3) return g_prioBackoff[prio];
  return 1.0;
}

// ============================ ★科学版 dp 调度：Kaufman-Roberts 生灭过程精确解 ============================
// 1D 生灭链稳态占用 π[0..c]（精确，非近似）：birth(k)=A_h·1[k<c]+A_m·1[k<c-g_h]+A_l·1[k<c-g_h-g_m]，
// death(k)=k+1；π(b+1)=π(b)·birth(b)/death(b+1)。阻塞：B_h=π(c)，B_m=Σ_{b≥c-g_h}π，B_l=Σ_{b≥c-g_h-g_m}π。
// 最优阈值 (g_h*,g_m*)=argmin( w_m·B_m+w_l·B_l ) s.t. B_h≤ε；不可行时退化为 argmin B_h（保高危）。
static void dpBirthDeathPi(uint32_t c, double Ah, double Am, double Al,
                            int gh, int gm, std::vector<double>& pi){
  pi.assign(c + 1, 0.0);
  pi[0] = 1.0;
  int c_h = (int)c - gh, c_l = (int)c - gh - gm;
  for (uint32_t b = 0; b < c; ++b){
    double birth = 0.0;
    if (b < c)            birth += Ah;
    if (b < (uint32_t)c_h) birth += Am;
    if (b < (uint32_t)c_l) birth += Al;
    double death = (double)b + 1.0;
    pi[b + 1] = (death > 0.0) ? pi[b] * (birth / death) : 0.0;
  }
  double s = 0.0; for (double v : pi) s += v;
  if (s <= 0.0){ pi.assign(c + 1, 0.0); pi[0] = 1.0; return; }
  for (double& v : pi) v /= s;
}
static double r8(double x){ return std::round(x * 1e8) / 1e8; } // 与 Python optimal_guards round(_,8) 对齐
static void dpOptimalGuards(uint32_t c, double Ah, double Am, double Al,
                            double wm, double wl, double eps,
                            int& out_gh, int& out_gm,
                            double& out_bh, double& out_bm, double& out_bl){
  out_gh = 0; out_gm = 0; out_bh = 1.0; out_bm = 1.0; out_bl = 1.0;
  int    best_k0 = 2; double best_k1 = 1e9, best_k2 = 1e9;
  std::vector<double> pi;
  for (int gh = 0; gh <= (int)c; ++gh){
    for (int gm = 0; gm <= (int)c - gh; ++gm){
      dpBirthDeathPi(c, Ah, Am, Al, gh, gm, pi);
      double bh  = pi[c];
      int c_h = (int)c - gh, c_l = (int)c - gh - gm;
      double bm = 0.0, bl = 0.0;
      for (int b = c_h; b <= (int)c; ++b) if (b >= 0) bm += pi[b];
      for (int b = c_l; b <= (int)c; ++b) if (b >= 0) bl += pi[b];
      bool feasible = (bh <= eps);
      double obj = wm * bm + wl * bl;
      int    key0 = feasible ? 0 : 1;
      double key1 = r8(feasible ? obj : bh);
      double key2 = r8(feasible ? bh  : obj);
      bool better = false;
      if (key0 < best_k0) better = true;
      else if (key0 == best_k0){
        if (key1 < best_k1 - 1e-12) better = true;
        else if (std::fabs(key1 - best_k1) <= 1e-12 && key2 < best_k2 - 1e-12) better = true;
      }
      if (best_k0 == 2 || better){
        best_k0 = key0; best_k1 = key1; best_k2 = key2;
        out_gh = gh; out_gm = gm; out_bh = bh; out_bm = bm; out_bl = bl;
      }
    }
  }
}
static void dpRecompute(uint32_t sat){
  double ah = g_dpEwma[{sat, 0u}] * g_prioLoadCal;
  double am = g_dpEwma[{sat, 1u}] * g_prioLoadCal;
  double al = g_dpEwma[{sat, 2u}] * g_prioLoadCal;
  int gh, gm; double bh, bm, bl;
  dpOptimalGuards(g_rachCapacity, ah, am, al, g_prioWm, g_prioWl, g_prioEps,
                  gh, gm, bh, bm, bl);
  g_dpGuards[sat] = {gh, gm};
  g_dpSumGh += (double)gh;
  g_dpNGuard += 1;
}

static bool slotOk(uint32_t satId, double t, uint32_t prio){
  if (!g_collisionOn) return true;
  uint32_t slot10 = (uint32_t)(t / 0.01);
  if (!g_priorityOn){
    auto key = std::make_pair(satId, slot10);
    uint32_t used = g_slotLoad.count(key) ? g_slotLoad[key] : 0;
    if (used >= g_rachCapacity) return false;
    g_slotLoad[key] = used + 1;
    return true;
  }
  if (g_priorityMode == "dp"){
    // --- 窗口推进：结算上一窗口（仅非首窗口）→ EWMA 更新 + 重算最优阈值 ---
    int win = (int)(t / g_prioAdaptWinS);
    int sat_win = (g_dpWin.count(satId) ? (int)g_dpWin[satId] : -1);
    if (sat_win == -1 || win != sat_win){
      if (sat_win != -1){
        for (int tier = 0; tier < 3; ++tier){
          double raw = g_dpWincnt[{satId, (uint32_t)tier}] / (double)g_dpSlotPerWin;
          auto eit = g_dpEwma.find({satId, (uint32_t)tier});
          double prev = (eit == g_dpEwma.end()) ? 0.0 : eit->second;
          g_dpEwma[{satId, (uint32_t)tier}] = (eit == g_dpEwma.end())
              ? raw : (g_prioBeta * raw + (1.0 - g_prioBeta) * prev);
        }
        dpRecompute(satId);
      }
      g_dpWin[satId] = win;
      for (int tier = 0; tier < 3; ++tier) g_dpWincnt[{satId, (uint32_t)tier}] = 0.0;
    }
    // --- guard-channel 准入（窗口/到达计数在 AttemptAccess 按「首次尝试」更新）---
    int gh = (int)g_dpDefGh, gm = (int)g_dpDefGm;
    auto git = g_dpGuards.find(satId);
    if (git != g_dpGuards.end()){ gh = git->second.first; gm = git->second.second; }
    uint32_t occ = g_dpOcc.count({satId, slot10}) ? g_dpOcc[{satId, slot10}] : 0;
    bool ok;
    if      (prio == 0) ok = occ < g_rachCapacity;
    else if (prio == 1) ok = occ < g_rachCapacity - (uint32_t)gh;
    else                ok = occ < g_rachCapacity - (uint32_t)gh - (uint32_t)gm;
    if (ok){
      g_dpOcc[{satId, slot10}] = occ + 1;
      // 回收计数：med/low 落入高危预留区 [c-gh, c) → 闲置预留被复用
      if (prio != 0 && occ >= g_rachCapacity - (uint32_t)gh)
        g_dpReclaim[satId] = g_dpReclaim.count(satId) ? g_dpReclaim[satId] + 1 : 1;
      return true;
    }
    return false;
  }
  // 三档专用池容量（余数归 low，保证总容量=rach_capacity）
  uint32_t cap[3];
  cap[0] = std::max(1u, (uint32_t)(g_rachCapacity * g_prioReserveFrac[0]));
  cap[1] = std::max(1u, (uint32_t)(g_rachCapacity * g_prioReserveFrac[1]));
  cap[2] = (g_rachCapacity > cap[0] + cap[1]) ? (g_rachCapacity - cap[0] - cap[1]) : 1u;
  // 严格三池：先占本档专用池；不向低优档借用（避免重演 med<low）
  for (uint32_t p = prio; p < 3; ++p){
    auto key = std::make_pair(satId, slot10);
    uint32_t used = g_slotP[p].count(key) ? g_slotP[p][key] : 0;
    if (used < cap[p]){ g_slotP[p][key] = used + 1; return true; }
  }
  return false;
}

// ============================ SHA-256 / HMAC-SHA256 ============================
// ★审计修复 2026-09-02★：原 T4 认证为「读终端自报 forged 比特」的 Mock，
// 拦截率恒 1.0（同义反复）。以下为真实密码学校验（FIPS 180-4 标准实现）。
static const uint32_t SHA256_K[64] = {
  0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
  0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
  0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
  0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
  0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
  0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
  0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
  0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};
static uint32_t rotr32(uint32_t x, int n){ return (x >> n) | (x << (32 - n)); }

static void sha256(const uint8_t* data, size_t n, uint8_t out[32]){
  uint32_t h[8] = {0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
                   0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19};
  auto compress = [&](const uint8_t* p){
    uint32_t w[64];
    for (int i = 0; i < 16; i++)
      w[i] = (uint32_t(p[4*i])<<24)|(uint32_t(p[4*i+1])<<16)|(uint32_t(p[4*i+2])<<8)|p[4*i+3];
    for (int i = 16; i < 64; i++){
      uint32_t s0 = rotr32(w[i-15],7) ^ rotr32(w[i-15],18) ^ (w[i-15]>>3);
      uint32_t s1 = rotr32(w[i-2],17) ^ rotr32(w[i-2],19) ^ (w[i-2]>>10);
      w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a=h[0],b=h[1],c=h[2],d=h[3],e=h[4],f=h[5],g=h[6],hh=h[7];
    for (int i = 0; i < 64; i++){
      uint32_t S1 = rotr32(e,6) ^ rotr32(e,11) ^ rotr32(e,25);
      uint32_t ch = (e & f) ^ ((~e) & g);
      uint32_t t1 = hh + S1 + ch + SHA256_K[i] + w[i];
      uint32_t S0 = rotr32(a,2) ^ rotr32(a,13) ^ rotr32(a,22);
      uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
      uint32_t t2 = S0 + mj;
      hh=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    h[0]+=a; h[1]+=b; h[2]+=c; h[3]+=d; h[4]+=e; h[5]+=f; h[6]+=g; h[7]+=hh;
  };
  uint64_t bits = uint64_t(n) * 8;
  size_t full = n / 64;
  for (size_t i = 0; i < full; i++) compress(data + i*64);
  uint8_t tail[128]; size_t rem = n - full*64;
  memset(tail, 0, sizeof(tail));
  memcpy(tail, data + full*64, rem);
  tail[rem] = 0x80;
  size_t padLen = (rem < 56) ? 64 : 128;
  for (int i = 0; i < 8; i++) tail[padLen-8+i] = uint8_t(bits >> (56 - 8*i));
  compress(tail);
  if (padLen == 128) compress(tail + 64);
  for (int i = 0; i < 8; i++){
    out[4*i]   = uint8_t(h[i]>>24); out[4*i+1] = uint8_t(h[i]>>16);
    out[4*i+2] = uint8_t(h[i]>>8);  out[4*i+3] = uint8_t(h[i]);
  }
}

// HMAC-SHA256(key, msg) → out[32]（FIPS 198-1）
static void hmacSha256(const uint8_t* key, size_t klen,
                       const uint8_t* msg, size_t mlen, uint8_t out[32]){
  uint8_t k0[64]; memset(k0, 0, 64);
  if (klen > 64){ sha256(key, klen, k0); }
  else { memcpy(k0, key, klen); }
  uint8_t ipad[64], opad[64];
  for (int i = 0; i < 64; i++){ ipad[i] = k0[i]^0x36; opad[i] = k0[i]^0x5c; }
  uint8_t inner[32];
  std::vector<uint8_t> buf(64 + mlen);
  memcpy(buf.data(), ipad, 64); memcpy(buf.data() + 64, msg, mlen);
  sha256(buf.data(), buf.size(), inner);
  uint8_t buf2[96];
  memcpy(buf2, opad, 64); memcpy(buf2 + 64, inner, 32);
  sha256(buf2, 96, out);
}

// ============================ T4 凭证体系 ============================
// dev_key = HMAC(root, "devkey"||term_id)；mac = HMAC(dev_key, pseudo||counter)[:MAC_BYTES]
static void deriveRootKey(uint8_t out[32]){
  const char* s = "LEO-EMERG-ROOT";
  uint8_t sd[64];
  size_t sl = strlen(s);
  memcpy(sd, s, sl);
  // 把种子编码进消息：root = SHA256("LEO-EMERG-ROOT" || seed)
  uint8_t msg[64+8];
  memcpy(msg, s, sl);
  uint64_t v = g_rngSeed;
  for (int i = 0; i < 8; i++) msg[sl+i] = uint8_t(v >> (56-8*i));
  sha256(msg, sl+8, out);
}
static void deriveDevKey(const uint8_t root[32], uint32_t termId, uint8_t out[32]){
  uint8_t msg[64];
  memcpy(msg, root, 32);
  memcpy(msg+32, "devkey", 6);
  memcpy(msg+38, &termId, 4);
  hmacSha256(root, 32, msg, 42, out);
}
// ★P2 假名轮换★：epoch 加入派生输入 → 假名随轮换版本变化（切换前后假名不同，前向不可关联）。
static void makePseudo(const uint8_t root[32], uint32_t termId, uint32_t epoch, uint8_t out[4]){
  uint8_t msg[46], mac[32];
  memcpy(msg, root, 32);
  memcpy(msg+32, "pseudo", 6);
  memcpy(msg+38, &termId, 4);
  memcpy(msg+42, &epoch, 4);
  hmacSha256(root, 32, msg, 46, mac);
  memcpy(out, mac, 4);
}
// ★P2 哈希链续认证★：首联下发种子 + 切换时单向推进（对应 Python auth.gen_chain_seed/chain_next）
static void genChainSeed(const uint8_t root[32], uint32_t termId, uint8_t out[32]){
  uint8_t msg[41];
  memcpy(msg, root, 32);
  memcpy(msg+32, "chain", 5);
  memcpy(msg+37, &termId, 4);
  sha256(msg, 41, out);
}
static void chainNext(const uint8_t seed[32], uint8_t out[32]){
  uint8_t msg[37];
  memcpy(msg, "CHAIN", 5);
  memcpy(msg+5, seed, 32);
  sha256(msg, 37, out);
}
static uint32_t readBE32(const uint8_t* p){
  return (uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3];
}
static void writeBE32(uint8_t* p, uint32_t v){
  p[0]=uint8_t(v>>24); p[1]=uint8_t(v>>16); p[2]=uint8_t(v>>8); p[3]=uint8_t(v);
}
// sign: HMAC(dev_key, pseudo(4B) || counter(8B))[:MAC_BYTES] → 32bit
static uint32_t signCred(const uint8_t devKey[32], uint32_t pseudo, uint64_t counter){
  uint8_t msg[12], mac[32];
  writeBE32(msg, pseudo);
  for (int i = 0; i < 8; i++) msg[4+i] = uint8_t(counter >> (56-8*i));
  hmacSha256(devKey, 32, msg, 12, mac);
  return readBE32(mac);
}
// 星上校验：'o'=ok, 'm'=bad_mac, 'r'=replay
static char verifyOnboard(const uint8_t devKey[32], uint32_t pseudo, uint64_t counter, uint32_t mac){
  auto it = g_lastCounter.find(pseudo);
  if (it != g_lastCounter.end() && counter <= it->second) return 'r';
  if (signCred(devKey, pseudo, counter) != mac) return 'm';
  g_lastCounter[pseudo] = counter;
  return 'o';
}

// ============================ L2 链路模型（★审计修复★） ============================
// 原信道层为死代码（无 SNR/丢包，仰角无物理后果）。以下接入完整链路预算：
//   Eb/N0 = EIRP − FSPL − 30 + 10log10(1/kT) + G/T − 10log10(Rb)
//   BER(BPSK) = Q(sqrt(2·γ))；MAC 误码破坏概率 = 1−(1−BER)^(8·MAC_BYTES)
static double fsplDb(double slantKm){
  double lambda_m = 299792458.0 / g_carrierHz;
  return 20.0 * std::log10(4.0 * PI * slantKm * 1000.0 / lambda_m);
}
static double ebnoDb(double slantKm){
  double rxDbm = g_eirpDbm - fsplDb(slantKm);
  double rxDbw = rxDbm - 30.0;
  double noiseDbwHz = 10.0*std::log10(1.380649e-23) + 10.0*std::log10(g_noiseTempK);
  return rxDbw - noiseDbwHz + g_gtDbiK - 10.0*std::log10(g_bitRateBps);
}
static double qfunc(double x){ return 0.5 * std::erfc(x / std::sqrt(2.0)); }
static double berOf(double ebnoDbVal){
  return qfunc(std::sqrt(2.0 * std::pow(10.0, ebnoDbVal/10.0)));
}
static double macFailProb(double slantKm){
  if (!g_linkModelOn || slantKm <= 0) return 0.0;
  double p = berOf(ebnoDb(slantKm));
  if (p <= 0) return 0.0;
  if (p >= 1) return 1.0;
  return 1.0 - std::pow(1.0 - p, double(8*g_macBytes));
}
// 四步相对两步的附加时延（★机理化：原为常量 400ms★）：
// RAR 窗口 + 竞争解决定时器 + 两个额外几何往返（由斜距实算）
static double step4ExtraMsCalc(double delayMs){
  return double(g_rarWindowMs) + double(g_contTimerMs) + 4.0 * delayMs;
}


// ============================ 协议头 ============================
class LeoHeader : public Header {
public:
  uint8_t  msgType = 0;   // 1=ACCESS_REQ 2=ACCESS_GRANT 3=HO_PREP 4=HO_CONFIRM 5=ACCESS_DENY
  uint32_t termId = 0;
  uint32_t satId  = 0;
  uint16_t seq    = 0;
  double   dopplerHz = 0;
  double   taSec  = 0;    // Timing Advance（预补偿）
  uint8_t  steps  = 0;    // RACH 模式（2=两步；4=四步，供星上附加时延建模）
  // ---- ★审计修复 2026-09-02：T4 真实凭证字段（原仅有自报 forged 比特）----
  uint32_t pseudoId = 0;  // 假名标识
  uint32_t counter  = 0;  // 单调计数（防重放）
  uint32_t mac      = 0;  // HMAC-SHA256 截断 32bit

  static TypeId GetTypeId(){
    static TypeId tid = TypeId("ns3::LeoHeader").SetParent<Header>().SetGroupName("Sim")
      .AddConstructor<LeoHeader>();
    return tid;
  }
  virtual TypeId GetInstanceTypeId() const { return GetTypeId(); }
  virtual void Print(std::ostream &os) const { os << "Leo msg=" << (int)msgType; }
  virtual uint32_t GetSerializedSize() const { return 1+4+4+2+8+8+1+4+4+4; }
  virtual void Serialize(Buffer::Iterator s) const {
    s.WriteU8(msgType); s.WriteU32(termId); s.WriteU32(satId);
    s.WriteU16(seq);
    s.WriteU64((uint64_t)(int64_t)(dopplerHz*1e6));
    s.WriteU64((uint64_t)(int64_t)(taSec*1e9));
    s.WriteU8(steps);
    s.WriteU32(pseudoId); s.WriteU32(counter); s.WriteU32(mac);
  }
  virtual uint32_t Deserialize(Buffer::Iterator s){
    msgType = s.ReadU8(); termId = s.ReadU32(); satId = s.ReadU32();
    seq = s.ReadU16();
    dopplerHz = (double)(int64_t)s.ReadU64()/1e6;
    taSec = (double)(int64_t)s.ReadU64()/1e9;
    steps = s.ReadU8();
    pseudoId = s.ReadU32(); counter = s.ReadU32(); mac = s.ReadU32();
    return GetSerializedSize();
  }
};

// ============================ LEO 链路（应用层自定义信道） ============================
class LeoChannel : public Object {
public:
  static TypeId GetTypeId(){
    static TypeId tid = TypeId("ns3::LeoChannel").SetParent<Object>().SetGroupName("Sim")
      .AddConstructor<LeoChannel>();
    return tid;
  }
  void Register(uint32_t nodeId, Ptr<LeoApp> app){ m_apps[nodeId] = app; }
  void Tx(uint32_t srcId, uint32_t dstId, Ptr<Packet> pkt, double delayS); // 定义见 LeoApp 之后
  std::map<uint32_t, Ptr<LeoApp>> m_apps;
};
NS_OBJECT_ENSURE_REGISTERED(LeoChannel);

static Ptr<LeoChannel> g_channel = CreateObject<LeoChannel>();

// ============================ 终端 / 卫星 应用 ============================
class LeoApp : public Application {
public:
  static TypeId GetTypeId(){
    static TypeId tid = TypeId("ns3::LeoApp").SetParent<Application>().SetGroupName("Sim")
      .AddConstructor<LeoApp>();
    return tid;
  }
  LeoApp() : m_active(false), m_isTerminal(false), m_nodeId(0), m_termIdx(0), m_satIdx(0),
             m_tag("low"), m_burstT(0), m_accessed(false), m_forged(false),
             m_compromised(false), m_retryCnt(0), m_servingSat(0),
             m_servingLos(0), m_accessFinT(0), m_prio(2), m_seq(0) {}
  virtual ~LeoApp() {}

  void SetTerminal(uint32_t idx, const std::string& tag, double burstT){
    m_isTerminal = true; m_termIdx = idx; m_tag = tag; m_burstT = burstT;
    // ★生存优先★：tag → 优先级（同 config.TIER_ORDER：high=0/med=1/low=2）
    m_prio = (tag == "high") ? 0u : (tag == "med") ? 1u : 2u;
  }
  void SetSatellite(uint32_t idx){ m_isTerminal = false; m_satIdx = idx; }
  // ★审计修复★：伪造终端分两类——盲伪造（无密钥，MAC 随机）与密钥泄露（持有效 key，
  // 密码层不可检出 → 漏检）。原实现仅一个自报布尔。
  void SetForged(bool f, bool compromised){ m_forged = f; m_compromised = compromised; }
  // 终端初始化凭证（合法终端与密钥泄露型伪造终端持有效 dev_key）
  void InitCredential(const uint8_t root[32]){
    deriveDevKey(root, m_termIdx, m_devKey);
    m_epoch = 0;
    uint8_t ps[4]; makePseudo(root, m_termIdx, m_epoch, ps);
    m_pseudo = readBE32(ps);
    genChainSeed(root, m_termIdx, m_chainSeed);  // ★P2★ 首联 MsgB 下发哈希链种子
  }

  // 被链路调度调用的接收
  void Receive(Ptr<Packet> pkt, uint32_t srcId){
    LeoHeader h; pkt->RemoveHeader(h);
    if (m_isTerminal){
      HandleTerminalRx(h, srcId);
    } else {
      HandleSatelliteRx(h, srcId);
    }
  }

  // 接入失败：写 fail 事件（无服务星 -1 / 无有效时延 -1），终止该终端后续尝试
  void TermFail(const char* authResult = "none"){
    double t = Simulator::Now().GetSeconds();
    g_trace << "ACCESS," << m_termIdx << "," << m_tag << "," << std::fixed
            << std::setprecision(3) << t << ",-1,-1,"
            << std::setprecision(2) << -1.0 << ",0.0,0.0,fail,0,0,0,"
            << (m_forged?1:0) << "," << authResult << ",0.0\n";
    m_accessed = true;
  }

  void AttemptAccess(){
    if (!m_active || m_accessed) return;
    g_dbg_attempt++;
    double t = Simulator::Now().GetSeconds();
    auto vis = visibleAt(m_nodeId, t);
    if (vis.empty()){
      Simulator::Schedule(Seconds(1.0), &LeoApp::AttemptAccess, this); // 重试
      return;
    }
    // 选仰角最高者作为服务星
    uint32_t best = 0; double bestEl = -1e9;
    for (auto& v : vis){
      uint32_t sid = std::get<0>(v);
      double el = elevationDeg(g_termPos[m_nodeId], satPosAt(sid, t));
      if (el > bestEl){ bestEl = el; best = sid; }
    }
    // 碰撞/拥塞限流（与 Python 轨同参）：星上按 (卫星, 10ms 时间片) 限流。
    // ★审计修复★：伪造终端同样占信道（星上须先接收再校验拒绝），与真实系统一致；
    // 原实现「伪造不占信道」高估了系统抗伪造风暴能力。
    // ★生存优先★：优先级感知 RACH 容量判定（镜像 Python protocol._slot_ok）
    // ★科学版 dp★：仅「首次尝试」(m_retryCnt==0) 计入到达率 EWMA（重试是阻塞后果，不可回馈估计器）
    if (g_priorityMode == "dp" && m_retryCnt == 0){
      g_dpWincnt[{best, m_prio}] = g_dpWincnt[{best, m_prio}] + 1.0;
    }
    if (g_collisionOn && !slotOk(best, t, m_prio)){
      if (m_retryCnt >= g_retryMax){      // 重试超限 → 接入失败
        g_dbg_collision_fail++;
        TermFail("collision_fail");
        return;
      }
      m_retryCnt++;
      double backoff = g_u01(g_runRng) * g_retryIntervalMs / 1000.0
                       * prioBackoffScale(m_prio); // 高优退避更短（同 Python PRIO_BACKOFF）
      Simulator::Schedule(Seconds(backoff), &LeoApp::AttemptAccess, this);
      return;
    }
    // ---- ★审计修复★：四步 RACH 前导竞争（机理化，原为常量附加时延）----
    // 同 (sat, 时隙, 前导) 被多终端选中 → msg3 竞争解决失败 → 退避重来。
    if (g_rachSteps >= 4){
      uint32_t slot10 = (uint32_t)(t / 0.01);
      uint32_t pre = g_runRng() % g_nPreamble;
      auto pkey = std::make_tuple(best, slot10, pre);
      auto pit = g_preambleUse.find(pkey);
      if (pit != g_preambleUse.end() && pit->second != m_termIdx){
        if (m_retryCnt >= (uint32_t)g_retryMax){
          TermFail("contention_fail");
          return;
        }
        m_retryCnt++;
        double backoff = g_u01(g_runRng) * g_retryIntervalMs / 1000.0
                         * prioBackoffScale(m_prio);
        Simulator::Schedule(Seconds(backoff), &LeoApp::AttemptAccess, this);
        return;
      }
      g_preambleUse[pkey] = m_termIdx;
    }
    m_servingSat = best;
    double rg = rangeKm(g_termPos[m_nodeId], satPosAt(best, t));
    double delay = rg / C_KM_S;
    g_dbg_req++;
    // 两步接入：终端发 REQ（携带 GNSS 估计的 TA/多普勒/RACH 模式与 T4 凭证），卫星回 GRANT
    LeoHeader req; req.msgType=1; req.termId=m_termIdx; req.satId=best; req.seq=++m_seq;
    req.dopplerHz = dopplerHz(best, g_termPos[m_nodeId], t);
    req.taSec = 2.0 * delay; // 开环预补偿
    req.steps = (uint8_t)g_rachSteps;
    // ---- T4 凭证（★审计修复★：真实签名，替代自报比特）----
    req.pseudoId = m_pseudo;
    req.counter  = m_retryCnt + 1;
    req.mac = m_compromised
              ? signCred(m_devKey, m_pseudo, req.counter)      // 密钥泄露型：合法签名
              : (m_forged
                 ? (uint32_t)(g_runRng() >> 32)                // 盲伪造：随机 MAC
                 : signCred(m_devKey, m_pseudo, req.counter)); // 合法终端：真实签名
    // ---- 合法终端：链路误码可能破坏 MAC → 星上误拒（虚警率来源）----
    if (!m_forged && macFailProb(rg) > g_u01(g_runRng)){
      req.mac ^= (uint32_t(1) << (g_runRng() % 32));           // 翻转一个比特
    }
    Ptr<Packet> p = Create<Packet>(); p->AddHeader(req);
    g_channel->Tx(m_nodeId, best, p, delay);
  }

  void Tick(){
    if (!m_active || !m_accessed) return;
    double t = Simulator::Now().GetSeconds();
    auto vis = visibleAt(m_nodeId, t);
    // 当前服务星剩余可见时间
    double servingRemain = -1;
    for (auto& v : vis){
      if (std::get<0>(v) == m_servingSat){ servingRemain = std::get<1>(v); m_servingLos = std::get<2>(v); }
    }
    if (servingRemain < 0){
      // 服务星已不可见：立即选最佳重连（不应发生，预测应已切换）；缺口如实记录
      if (!vis.empty()){
        uint32_t best=0; double be=-1e9, bestLos=-1;
        for (auto& v : vis){
          uint32_t s=std::get<0>(v);
          double el=elevationDeg(g_termPos[m_nodeId], satPosAt(s,t));
          if (el>be){ be=el; best=s; bestLos=std::get<2>(v); }
        }
        // 应急重连：候选此刻可见 → 可连时刻即 t；决策时刻亦为 t（缺口 = max(0, t − 旧LOS)）
        DoHandover(best, t, t, m_servingLos, bestLos, t);
      }
      Simulator::Schedule(Seconds(g_tickS), &LeoApp::Tick, this);
      return;
    }
    if (servingRemain <= g_hoLeadS){
      // 预测式切换（T5/T6）—— 与 Python 轨 sim/protocol.py 完全同一判决规则：
      //   1) 候选池 = 在服务星 LOS 时刻仍可见的其他星（重叠，先建后断）；
      //      LOS 时无其他可见候选 → 退化为「最早升起星」兜底（缺口如实记录）；
      //   2) 选优指标 = 未来驻留时长（los − t_ho）最大者（稳定优先，抑制乒乓）；
      PredictAndHandover(t);
    }
    Simulator::Schedule(Seconds(g_tickS), &LeoApp::Tick, this);
  }

  // 与 Python 轨 sim/protocol.py 逐条同规则的候选选择与切换（失配/乒乓/中断口径一致）
  // ★审计修复★：联合打分 score = w_el·el_norm + w_dwell·dwell_norm（与 Python 轨一致）。
  // 原实现 argmax(los) 系统性选中最低仰角星（仰角代价 26°）。
  double hoScore(uint32_t satId, double at){
    double el = elevationDeg(g_termPos[m_nodeId], satPosAt(satId, at));
    double elNorm = std::max(0.0, std::min(1.0, (el - g_maskDeg) / (90.0 - g_maskDeg)));
    auto it = g_termWins.find(m_nodeId);
    double los = -1;
    if (it != g_termWins.end()){
      for (const auto& w : it->second)
        if (w.satId == satId && w.aos <= at && at <= w.los){ los = w.los; break; }
    }
    double dw = (los > 0) ? std::max(0.0, std::min(1.0, (los - at) / 600.0)) : 0.0;
    return g_wEl * elNorm + g_wDwell * dw;
  }

  void PredictAndHandover(double t){
    double tLosTrue = m_servingLos;
    // ★审计修复★：星历预测误差（TLE 老化 → LOS 估计偏差，零均值高斯）。
    // 原实现用完美未来窗口，预测永不失败 → 中断恒为 0（结构性恒等，非算法成果）。
    double tLos = tLosTrue;
    if (g_ephemErrS > 0){
      std::normal_distribution<double> nd(0.0, g_ephemErrS);
      tLos += nd(g_runRng);
    }
    // 决策时刻（同 Python: t_ho = max(预测LOS − ho_lead, 连接建立时刻)）
    double tHo = std::max(tLos - g_hoLeadS, m_accessFinT);
    // ★P0-1 修复★：切换冷却——距上次切换 < 30s 不切，抑制仿真末端
    // （两窗口几乎同时结束 + 星历误差抖动）造成的乒乓震荡（与 Python 轨同规则）。
    if (!m_hoHist.empty() && (tHo - m_hoHist.back().second) < 30.0){
      return;
    }
    // 候选 A：预测 LOS 时刻仍可见的非服务星（重叠覆盖，先建后断）
    uint32_t cand = 0; double candScore = -1e9, candLos = -1;
    auto visLos = visibleAt(m_nodeId, tLos);
    auto itWin = g_termWins.find(m_nodeId);
    for (auto& v : visLos){
      uint32_t s = std::get<0>(v);
      if (s == m_servingSat) continue;
      double los = std::get<2>(v);
      // 防御（同 Python）：候选 LOS 必须严格晚于服务星预测 LOS
      if (los <= tLos + 1e-9) continue;
      double aosOf = -1;
      if (itWin != g_termWins.end()){
        for (const auto& w : itWin->second){
          if (w.satId == s && w.aos <= tLos && tLos <= w.los){ aosOf = w.aos; break; }
        }
      }
      if (aosOf < 0 || aosOf > tHo + 1e-9) continue;
      double sc = hoScore(s, tHo);
      if (sc > candScore){ candScore = sc; candLos = los; cand = s; }  // 联合打分最优
    }
    if (cand != 0){
      // ★迟滞★：新目标得分未超出当前服务星足够余量 → 不切换（抑制抖动/乒乓）
      if (g_hoHyst > 0){
        double curScore = hoScore(m_servingSat, tHo);
        if (candScore < curScore + g_hoHyst) return;
      }
      // ★审计修复（2026-09-02 第 2 轮，镜像 Python 轨）★：重叠候选在决策时刻 tHo 已可见，
      // 预测式「先建后断」应即刻建链（可连时刻 = tHo），而非等到预测 LOS。
      // 原 candConnect=tLos 使 hoLead 提前量被完全旁路（中断≈max(0, LOS 预测高估误差)）。
      RotateCredential();  // ★P2★ 假名轮换 + 哈希链推进（每次切换）
      if (g_preMigrate) g_satCtx[cand][m_termIdx] = g_satCtx[m_servingSat][m_termIdx];  // D3 预迁移（键=term_id）
      DoHandover(cand, t, tHo, tLosTrue, candLos, tHo);
      return;
    }
    // 候选 B：无重叠候选 → 最早升起星兜底（覆盖盲区，中断如实记录）
    uint32_t nid = 0; double naos = 1e18, nlos = -1;
    auto it = g_termWins.find(m_nodeId);
    if (it != g_termWins.end()){
      for (const auto& w : it->second){
        if (w.satId == m_servingSat) continue;
        if (w.aos > tLos && w.aos < naos){ naos = w.aos; nid = w.satId; nlos = w.los; }
      }
    }
    if (nid == 0) return;   // 全仿真无后续可见星，保持当前连接至结束
    RotateCredential();  // ★P2★ 假名轮换 + 哈希链推进（每次切换）
    if (g_preMigrate) g_satCtx[nid][m_termIdx] = g_satCtx[m_servingSat][m_termIdx];  // D3 预迁移（键=term_id）
    DoHandover(nid, t, naos, tLosTrue, nlos, tLos);
  }

  // ★P2 假名轮换 + 哈希链推进★：每次切换递增 epoch、重派生信令假名、哈希链单向推进
  // （镜像 Python protocol.py 切换分支：term_epoch+1 → make_pseudo(epoch) → chain_next）。
  void RotateCredential(){
    uint8_t root[32]; deriveRootKey(root);
    m_epoch++;
    uint8_t ps[4]; makePseudo(root, m_termIdx, m_epoch, ps);
    m_pseudo = readBE32(ps);
    uint8_t ns[32]; chainNext(m_chainSeed, ns); memcpy(m_chainSeed, ns, 32);
    g_dbg_pseudo_rotation++;  // ★P2★ 假名轮换总次数（可审计「每次切换轮换」）
  }

private:
  bool m_active;
  bool m_isTerminal;
  uint32_t m_nodeId;
  uint32_t m_termIdx;
  uint32_t m_satIdx;
  std::string m_tag;
  uint32_t m_prio;     // 生存优先级（0=high,1=med,2=low）
  double m_burstT;
  bool m_accessed;
  bool m_forged;          // T4：伪造终端标记（抽样标签，仅用于 trace 分流与指标统计）
  bool m_compromised;     // ★审计修复★：密钥泄露型伪造（持有效 dev_key，密码层不可检出）
  uint8_t m_devKey[32];   // 终端派生密钥（合法终端与泄露型伪造持有效密钥）
  uint32_t m_pseudo;      // 假名标识（信令中替代明文长期身份）
  uint32_t m_epoch;       // ★P2★ 假名轮换版本（首联=0，每次切换 +1）
  uint8_t m_chainSeed[32];// ★P2★ 哈希链当前链头（首联下发种子，切换单向推进）
  uint32_t m_retryCnt;    // 碰撞退避重试计数（超 g_retryMax 判失败）
  uint32_t m_servingSat;
  double m_servingLos;
  double m_accessFinT;    // 本段连接建立完成时刻（GRANT 收到时）——用于决策时刻下界
  uint16_t m_seq;
  // ★审计修复★：乒乓重定义所需历史 [(satId, 切换时刻)]
  std::vector<std::pair<uint32_t,double>> m_hoHist;

  // ★签名变更（审计修复）★：新增 losTrue（真实 LOS，区别于预测 LOS），用于中断计算。
  void DoHandover(uint32_t cand, double t, double candConnect, double losTrue,
                  double candLos, double tHo){
    double rg = rangeKm(g_termPos[m_nodeId], satPosAt(cand, t));
    double delay = rg / C_KM_S;
    // 预测失配（契约 2.1，与 Python 轨一致）：决策选中的候选 vs 执行时刻（服务星 LOS）
    // 仰角最优可见星：
    auto visLos = visibleAt(m_nodeId, losTrue);
    uint32_t bestEL = 0; double be = -1e9;
    for (auto& v : visLos){
      uint32_t s = std::get<0>(v);
      if (s == m_servingSat) continue;
      double el = elevationDeg(g_termPos[m_nodeId], satPosAt(s, losTrue));
      if (el > be){ be = el; bestEL = s; }
    }
    bool mismatch = (bestEL != 0) && (bestEL != cand);
    // 仰角代价（契约 2.1，与 Python 轨 ho_el_cost_deg 同口径）：仰角最优 − 选中星，@LOS 时刻
    double elCost = 0.0;
    if (bestEL != 0){
      elCost = be - elevationDeg(g_termPos[m_nodeId], satPosAt(cand, losTrue));
      if (elCost < 0) elCost = 0.0;
    }
    // ★审计修复★：乒乓重定义（原判据与选优规则自相矛盾，恒为 0，不可证伪）：
    //   1) 窗口内切回曾服务过的星；2) 相邻两次切换间隔 < 阈值
    bool pingpong = false;
    for (auto& hp : m_hoHist){
      if (hp.first == cand && (tHo - hp.second) <= 60.0) pingpong = true;
    }
    if (!m_hoHist.empty() && (tHo - m_hoHist.back().second) < 30.0) pingpong = true;
    m_hoHist.push_back({cand, tHo});
    // 中断（契约 2.1）：业务间隙 = max(0, 新链可用 − 旧链真实丢失)
    // ★审计修复★：原 avail = max(candConnect, t+2d+proc) 在 ho_lead>12ms 时恒 ≤ LOS，
    // 属结构性恒等而非算法成果。现中断相对**真实** LOS 计算（预测高估 → 中断>0）。
    // ★审计修复（2026-09-02 第 2 轮，镜像 Python 轨）★：重连确认须在目标可达（candConnect）
    // 之后才能收发，故新链可用 = candConnect + execS（原从 tHo 起算取 max）。
    double execS;
    bool hasCtx = g_satCtx.count(cand) && g_satCtx[cand].count(m_termIdx) > 0;  // D3（键=term_id）
    // ★P1 修复（镜像 Python protocol.py）★：重连时延 if/else 二选一，不再重复计数基础段。
    // 原实现 execS 先初始化基础段、无预迁移时再累加基础段+rerach → 重复计数。
    if (hasCtx){
      // 有预迁移：新星持预置上下文，一次比对即确认（RACH-less）
      execS = 2.0*delay + g_accessProcMs/1000.0 + g_authExtraMs/1000.0;
      g_dbg_premig_hit++;
    } else {
      // 无预迁移：终端须在新星重新随机接入（四步 RACH 完整流程总时延）
      double rerach = step4ExtraMsCalc(delay*1000.0);
      execS = rerach/1000.0 + g_accessProcMs/1000.0 + g_authExtraMs/1000.0;
      g_dbg_premig_miss++;
      g_dbg_rerach_extra_ms += rerach - 2.0*delay*1000.0;  // 重连额外 = step4 − 2×传播（镜像 Python rerach_extra_ms）
    }
    g_dbg_ho_total_ms += execS * 1000.0;  // 切换总时延累加（镜像 Python ho_total_ms_sum）
    double avail = candConnect + execS;
    double interrupt_ms = std::max(0.0, avail - losTrue) * 1000.0;
    // ---- T7 重连确认（★审计修复★：原无条件 success，无任何失败路径）----
    // 目标星一次比对令牌：低仰角 → BER 高 → 令牌误码 → 确认失败 → 重传产生额外中断。
    // 这使「仰角代价」兑现为真实后果（原为记账数字）。
    const char* hoResult = "success";
    if (macFailProb(rg) > g_u01(g_runRng)){
      hoResult = "confirm_fail";
      g_dbg_confirm_fail++;
      interrupt_ms += execS * 1000.0;
    }
    // 先建后断：PREP 提前发出
    LeoHeader prep; prep.msgType=3; prep.termId=m_termIdx; prep.satId=cand; prep.seq=++m_seq;
    prep.dopplerHz = dopplerHz(cand, g_termPos[m_nodeId], t); prep.taSec = 2.0*delay;
    Ptr<Packet> p = Create<Packet>(); p->AddHeader(prep);
    g_channel->Tx(m_nodeId, cand, p, delay);
    // t_s 列 = 决策时刻 tHo（与 Python 轨 HANDOVER.t_s 语义一致）
    g_trace << "HANDOVER," << m_termIdx << "," << m_tag << "," << std::fixed
            << std::setprecision(3) << tHo << "," << m_servingSat << "," << cand << ","
            << std::setprecision(2) << interrupt_ms << ","
            << std::setprecision(1) << std::abs(dopplerHz(cand, g_termPos[m_nodeId], t)) << ","
            << std::setprecision(3) << rg << "," << hoResult << ","
            << (mismatch?1:0) << "," << (pingpong?1:0) << ","
            << std::setprecision(2) << elCost << ",0,none,"
            << std::setprecision(2) << (g_linkModelOn ? ebnoDb(rg) : 0.0) << "\n";
    m_servingSat = cand;
    m_servingLos = candLos;
  }

  void HandleTerminalRx(const LeoHeader& h, uint32_t srcId){
    double t = Simulator::Now().GetSeconds();
    if (h.msgType == 2){ // ACCESS_GRANT
      // 接入时延口径（契约 2.1，与 Python 轨一致）：端到端 =
      // (GRANT 完成时刻 − 终端首次发起时刻 m_burstT)×1000，含退避/等待与握手全程
      double delay_ms = (t - m_burstT) * 1000.0;
      if (delay_ms < 0) delay_ms = 0;   // 防御：不可见的时钟负差
      double rg = rangeKm(g_termPos[m_nodeId], satPosAt(h.satId, t));
      // ★漏检语义★：密钥泄露型伪造终端通过校验 → 从网络看是成功接入（forged=1,
      // auth_result=ok_missed）；接入成功率仅统计合法终端（forged=0），互不污染。
      const char* authRes = (m_forged && m_compromised) ? "ok_missed" : "ok";
      if (m_forged && m_compromised) g_dbg_forged_missed++;
      g_satCtx[h.satId][m_termIdx] = m_retryCnt + 1;  // D3：服务星记录终端认证上下文（键=term_id）
      g_trace << "ACCESS," << m_termIdx << "," << m_tag << "," << std::fixed
              << std::setprecision(3) << t << "," << h.satId << "," << h.satId << ","
              << std::setprecision(2) << delay_ms << ","
              << std::setprecision(1) << std::abs(h.dopplerHz) << ","
              << std::setprecision(3) << rg << ",success,0,0,0,"
              << (m_forged?1:0) << "," << authRes << ","
              << std::setprecision(2) << (g_linkModelOn ? ebnoDb(rg) : 0.0) << "\n";
      m_accessed = true;
      m_accessFinT = t;
      Simulator::Schedule(Seconds(g_tickS), &LeoApp::Tick, this);
    } else if (h.msgType == 4){ // HO_CONFIRM
      // 中断已在 DoHandover 决策时记录；此处仅确认（无需重复写）
    } else if (h.msgType == 5){ // ACCESS_DENY：校验失败（伪造拦截或误码虚警）
      // ★审计修复★：合法终端被误拒允许退避重试（虚警有恢复路径），重试超限才判失败
      if (!m_forged && m_retryCnt < (uint32_t)g_retryMax){
        m_retryCnt++;
        double backoff = g_u01(g_runRng) * g_retryIntervalMs / 1000.0
                         * prioBackoffScale(m_prio);
        m_accessed = false;
        Simulator::Schedule(Seconds(backoff), &LeoApp::AttemptAccess, this);
        return;
      }
      TermFail(m_forged ? "bad_mac" : "false_reject");
    }
  }

  void HandleSatelliteRx(const LeoHeader& h, uint32_t srcId){
    double t = Simulator::Now().GetSeconds();
    if (h.msgType == 1){ // ACCESS_REQ
      // 星上处理时序（与 Python 轨 access_delay = 2×传播 + 星上处理 + 认证/四步附加 同参）
      double authWait  = g_authExtraMs / 1000.0;                       // 轻量凭证校验
      double rg = rangeKm(g_termPos[srcId], satPosAt(m_nodeId, t));
      double delay = rg / C_KM_S;
      // 四步附加时延（★审计修复★：机理化——由斜距实算 + 具名定时器，原为常量 400ms）
      double step4Wait = (g_rachSteps >= 4) ? step4ExtraMsCalc(delay*1000.0)/1000.0 : 0.0;
      // ---- T4：真实 HMAC-SHA256 校验（★审计修复★：原为读自报 forged 比特）----
      uint8_t root[32]; deriveRootKey(root);
      uint8_t dk[32]; deriveDevKey(root, h.termId, dk);
      char res = verifyOnboard(dk, h.pseudoId, h.counter, h.mac);
      if (res == 'm'){
        // MAC 校验失败：盲伪造（拦截）或合法终端被信道误码误拒（虚警）
        // 由 trace 的 forged 列分流统计，此处统一回 DENY。
        g_dbg_forged_blocked++;
        LeoHeader deny; deny.msgType=5; deny.termId=h.termId; deny.satId=m_nodeId;
        deny.seq=h.seq;
        Ptr<Packet> p = Create<Packet>(); p->AddHeader(deny);
        Simulator::Schedule(Seconds(authWait), &LeoChannel::Tx, g_channel, m_nodeId, srcId, p, delay);
        return;
      }
      if (res == 'r'){  // 重放 → 拦截
        g_dbg_forged_blocked++;
        LeoHeader deny; deny.msgType=5; deny.termId=h.termId; deny.satId=m_nodeId;
        deny.seq=h.seq;
        Ptr<Packet> p = Create<Packet>(); p->AddHeader(deny);
        Simulator::Schedule(Seconds(authWait), &LeoChannel::Tx, g_channel, m_nodeId, srcId, p, delay);
        return;
      }
      // 正常终端：GRANT（放行；四步基线的 RAR 等待+竞争解决附加时延在星上处理阶段建模）
      LeoHeader grant; grant.msgType=2; grant.termId=h.termId; grant.satId=m_nodeId;
      grant.seq=h.seq; grant.dopplerHz = dopplerHz(m_nodeId, g_termPos[srcId], t);
      grant.taSec = 2.0*delay; grant.steps = h.steps;
      Ptr<Packet> p = Create<Packet>(); p->AddHeader(grant);
      Simulator::Schedule(Seconds(g_accessProcMs/1000.0 + authWait + step4Wait),
                          &LeoChannel::Tx, g_channel, m_nodeId, srcId, p, delay);
    } else if (h.msgType == 3){ // HO_PREP -> CONFIRM
      double rg = rangeKm(g_termPos[srcId], satPosAt(m_nodeId, t));
      double delay = rg / C_KM_S;
      LeoHeader conf; conf.msgType=4; conf.termId=h.termId; conf.satId=m_nodeId;
      conf.seq=h.seq; conf.dopplerHz = dopplerHz(m_nodeId, g_termPos[srcId], t);
      conf.taSec = 2.0*delay;
      Ptr<Packet> p = Create<Packet>(); p->AddHeader(conf);
      Simulator::Schedule(Seconds(g_accessProcMs/1000.0),
                          &LeoChannel::Tx, g_channel, m_nodeId, srcId, p, delay);
    }
  }

  virtual void StartApplication(){
    m_active = true;
    m_nodeId = GetNode()->GetId();
    g_apps[m_nodeId] = this;
    g_channel->Register(m_nodeId, this);
    if (m_isTerminal){
      Simulator::Schedule(Seconds(m_burstT), &LeoApp::AttemptAccess, this);
      Simulator::Schedule(Seconds(m_burstT), &LeoApp::Tick, this);
    }
  }
  virtual void StopApplication(){ m_active = false; }
};

// LeoChannel::Tx 需要在 LeoApp 完整定义后实现（用到 &LeoApp::Receive）
void LeoChannel::Tx(uint32_t srcId, uint32_t dstId, Ptr<Packet> pkt, double delayS){
  auto it = m_apps.find(dstId);
  if (it == m_apps.end()) return;
  if (delayS < 0) delayS = 0;
  Simulator::Schedule(Seconds(delayS), &LeoApp::Receive, it->second, pkt, srcId);
}

// ============================ 位置更新 ============================
static std::map<uint32_t, std::vector<Ecef>> g_ephById; // satNodeId 备份（同上 g_eph）
static void UpdatePositions(uint32_t satNodeId, uint32_t stepIdx){
  auto it = g_eph.find(satNodeId);
  if (it == g_eph.end()) return;
  const std::vector<Ecef>& v = it->second;
  if (stepIdx >= v.size()) return;
  auto mi = g_satId2Idx.find(satNodeId);
  if (mi == g_satId2Idx.end()) return;
  Ptr<Node> node = g_satNodes.Get(mi->second);
  Ptr<ConstantPositionMobilityModel> mm = node->GetObject<ConstantPositionMobilityModel>();
  if (mm) mm->SetPosition(Vector(v[stepIdx].x, v[stepIdx].y, v[stepIdx].z));
}

// ============================ 输入解析 ============================
static bool readEphemeris(const std::string& path, const std::map<std::string,uint32_t>& satNameToId){
  std::ifstream f(path);
  if (!f) return false;
  std::string line; std::getline(f, line); // header
  std::string name; double x,y,z; std::string field;
  while (std::getline(f, line)){
    if (line.empty()) continue;
    std::stringstream ss(line);
    std::vector<std::string> c;
    while (std::getline(ss, field, ',')) c.push_back(field);
    strip_cr(c);
    if (c.size() < 5) continue;
    try {
      name = c[0]; x = std::stod(c[2]);
      y = std::stod(c[3]); z = std::stod(c[4]);
    } catch (...) { continue; }
    auto it = satNameToId.find(name);
    if (it == satNameToId.end()) continue;
    uint32_t id = it->second;
    g_eph[id].push_back({x,y,z});
  }
  for (auto& kv : g_eph){
    uint32_t id = kv.first; const auto& v = kv.second;
    g_stepDt[id] = (v.size()>1) ? (g_simDur / (v.size()-1)) : 1.0;
  }
  return true;
}

static bool precomputeWindows(){
  for (auto& kv : g_termPos){
    uint32_t termId = kv.first; const Ecef& t = kv.second;
    std::vector<Window> wins;
    for (auto& ev : g_eph){
      uint32_t satId = ev.first; const auto& v = ev.second;
      if (v.empty()) continue;
      double dt = g_stepDt[satId];
      bool in = false; double aos=0, los=0;
      for (size_t i=0; i<v.size(); ++i){
        double tt = i*dt;
        double el = elevationDeg(t, v[i]);
        if (el >= g_maskDeg){
          if (!in){ in=true; aos=tt; }
          los = tt;
        } else {
          if (in){ wins.push_back({satId, aos, los}); in=false; }
        }
      }
      if (in) wins.push_back({satId, aos, los});
    }
    g_termWins[termId] = wins;
  }
  return true;
}

// ============================ 主程序 ============================
int main(int argc, char* argv[]){
  std::string indir = ".";
  std::string outdir = ".";
  double maskDeg=25, simDur=3600, stepS=15, burstStart=5, burstWin=60;
  double hoLead=20, tickS=1, carrierHz=2.0e9, accessProcMs=3;
  double authExtraMs=0, forgedRatio=0;
  double retryIntervalMs=500;
  uint32_t rachSteps=2, rachCapacity=64, retryMax=20, nTerms=80;
  int32_t collisionOn=0;
  int32_t priorityOn=1;   // ★生存优先★ default true（同 Python scenario.priority_on 默认）
  // ---- ★审计修复 2026-09-02：新增机理参数（与 sim/config.py 同参）----
  double ephemErrS=0, wEl=0.5, wDwell=0.5, hoHyst=0, compromisedShare=0.15;
  double eirpDbm=68, gtDbiK=-20, bitRateBps=100e3;
  uint32_t rarWindowMs=160, contTimerMs=200, nPreamble=64;
  int32_t linkModelOn=1;
  uint32_t preMigrate=1;   // D3：认证上下文预迁移开关（默认开启）
  uint64_t rngSeed=20260901;   // ★原固定 12345，现可配置（多种子/置信区间实验）★

  CommandLine cmd;
  cmd.AddValue("indir", "输入目录", indir);
  cmd.AddValue("outdir", "输出目录", outdir);
  cmd.AddValue("maskDeg", "仰角门限(度)", maskDeg);
  cmd.AddValue("simDur", "仿真时长(s)", simDur);
  cmd.AddValue("stepS", "星历采样步长(s)", stepS);
  cmd.AddValue("burstStart", "突发起始(s)", burstStart);
  cmd.AddValue("burstWin", "突发窗口(s)", burstWin);
  cmd.AddValue("hoLead", "预测切换提前量(s)", hoLead);
  cmd.AddValue("tickS", "切换检测周期(s)", tickS);
  cmd.AddValue("carrierHz", "载波频率(Hz)", carrierHz);
  cmd.AddValue("accessProcMs", "星上处理时延(ms)", accessProcMs);
  cmd.AddValue("nTerms", "终端数", nTerms);
  // T4 / RACH / 碰撞（与 Python 轨 scenario.py 同参）
  cmd.AddValue("forgedRatio", "伪造终端占比", forgedRatio);
  cmd.AddValue("compromisedShare", "伪造中持有效密钥比例(漏检率)", compromisedShare);
  cmd.AddValue("authExtraMs", "认证附加时延(ms)", authExtraMs);
  cmd.AddValue("rachSteps", "RACH 模式(2=两步 4=四步)", rachSteps);
  cmd.AddValue("collisionOn", "碰撞/拥塞模型开关", collisionOn);
  cmd.AddValue("priorityOn", "生存优先分级调度开关", priorityOn);
  cmd.AddValue("rachCapacity", "每10ms时隙受理上限", rachCapacity);
  double prioResHigh = 0.5, prioResMed = 0.3, prioResLow = 0.2;
  cmd.AddValue("prioResHigh", "high 专用预留比例", prioResHigh);
  cmd.AddValue("prioResMed", "med 专用预留比例", prioResMed);
  cmd.AddValue("prioResLow", "low 专用预留比例", prioResLow);
  std::string prioMode = "static"; // ★科学版 dp★ 调度模式："static" | "dp"
  cmd.AddValue("prioMode", "生存优先调度模式(static/dp)", prioMode);
  cmd.AddValue("retryIntervalMs", "退避间隔上限(ms)", retryIntervalMs);
  cmd.AddValue("retryMax", "碰撞重试上限", retryMax);
  // ★审计修复：机理参数★
  cmd.AddValue("ephemErrS", "星历预测误差σ(s)", ephemErrS);
  cmd.AddValue("wEl", "选星仰角权重", wEl);
  cmd.AddValue("wDwell", "选星驻留权重", wDwell);
  cmd.AddValue("hoHyst", "切换迟滞(score单位)", hoHyst);
  cmd.AddValue("rarWindowMs", "四步RAR响应窗口(ms)", rarWindowMs);
  cmd.AddValue("contTimerMs", "四步竞争解决定时器(ms)", contTimerMs);
  cmd.AddValue("nPreamble", "前导码数量", nPreamble);
  cmd.AddValue("eirpDbm", "卫星EIRP(dBm)", eirpDbm);
  cmd.AddValue("gtDbiK", "终端G/T(dB/K)", gtDbiK);
  cmd.AddValue("bitRateBps", "业务速率(bps)", bitRateBps);
  cmd.AddValue("linkModelOn", "链路模型开关", linkModelOn);
  cmd.AddValue("preMigrate", "认证上下文预迁移开关(1/0)", preMigrate);
  cmd.AddValue("rngSeed", "随机种子", rngSeed);
  cmd.Parse(argc, argv);

  g_maskDeg = maskDeg; g_carrierHz = carrierHz; g_hoLeadS = hoLead;
  g_tickS = tickS; g_accessProcMs = accessProcMs; g_simDur = simDur;
  g_authExtraMs = authExtraMs;
  g_rachSteps = rachSteps; g_forgedRatio = forgedRatio;
  g_compromisedShare = compromisedShare;
  g_collisionOn = (collisionOn != 0); g_rachCapacity = rachCapacity;
  g_priorityOn = (priorityOn != 0);
  g_prioReserveFrac[0] = prioResHigh; g_prioReserveFrac[1] = prioResMed; g_prioReserveFrac[2] = prioResLow;
  g_priorityMode = prioMode;   // ★科学版 dp★ 模式透传
  g_dpDefGh = std::max(1u, (uint32_t)(g_rachCapacity * g_prioReserveFrac[0]));
  g_dpDefGm = std::max(1u, (uint32_t)(g_rachCapacity * g_prioReserveFrac[1]));
  g_dpSlotPerWin = std::max(1u, (uint32_t)(g_prioAdaptWinS / 0.01)); // 每窗口 10ms 槽数
  g_retryIntervalMs = retryIntervalMs; g_retryMax = retryMax;
  g_ephemErrS = ephemErrS; g_wEl = wEl; g_wDwell = wDwell; g_hoHyst = hoHyst;
  g_rarWindowMs = rarWindowMs; g_contTimerMs = contTimerMs; g_nPreamble = nPreamble;
  g_eirpDbm = eirpDbm; g_gtDbiK = gtDbiK; g_bitRateBps = bitRateBps;
  g_linkModelOn = (linkModelOn != 0); g_rngSeed = rngSeed;
  g_preMigrate = (preMigrate != 0);
  g_runRng.seed((uint32_t)rngSeed);

  std::cout << "[LeoAccess] ns-3 离散事件接入/切换仿真启动" << std::endl;
  std::cout << "  输入目录=" << indir << " 输出目录=" << outdir << std::endl;
  std::cout << "  掩角=" << maskDeg << "° 时长=" << simDur << "s 步长=" << stepS
            << "s 突发=[" << burstStart << "," << burstStart+burstWin << "]s" << std::endl;
  std::cout << "  两步/四步=" << rachSteps << " 伪造占比=" << forgedRatio
            << " 密钥泄露占比=" << compromisedShare
            << " 认证附加=" << authExtraMs << "ms" << std::endl;
  std::cout << "  碰撞开关=" << collisionOn << " 容量=" << rachCapacity
            << "/10ms 退避=" << retryIntervalMs << "ms 重试上限=" << retryMax << std::endl;
  std::cout << "  星历误差σ=" << ephemErrS << "s 选星权重 wEl=" << wEl
            << " 迟滞=" << hoHyst << " 种子=" << rngSeed
            << " 生存优先=" << (priorityOn ? "开" : "关") << " 模式=" << g_priorityMode << std::endl;

  // 读终端
  std::map<uint32_t, Ecef> termPosTmp;
  std::map<uint32_t, std::string> termTag;
  std::map<uint32_t, double> termBurst;
  {
    std::ifstream f(indir + "/terminals.csv");
    std::string line; std::getline(f, line);
    std::string field;
    while (std::getline(f, line)){
      if (line.empty()) continue;
      std::stringstream ss(line);
      std::vector<std::string> c; while (std::getline(ss, field, ',')) c.push_back(field);
      strip_cr(c);
      if (c.size() < 5) continue;
      int id = std::stoi(c[0]); double lat = std::stod(c[1]);
      double lon = std::stod(c[2]); double alt = std::stod(c[3]); std::string tag = c[4];
      // 经纬度->ECEF
      double la=lat*PI/180, lo=lon*PI/180;
      double a=6378.137, f=1.0/298.257223563, e2=f*(2-f);
      double nn=a/std::sqrt(1-e2*std::sin(la)*std::sin(la));
      double h=alt/1000.0;
      double x=(nn+h)*std::cos(la)*std::cos(lo);
      double y=(nn+h)*std::cos(la)*std::sin(lo);
      double z=(nn*(1-e2)+h)*std::sin(la);
      termPosTmp[(uint32_t)id] = {x,y,z};
      termTag[(uint32_t)id] = tag;
    }
  }

  // 读星历：先建节点，再映射 name->id
  NodeContainer satNodes, termNodes;
  // 卫星节点数未知，先读 ephemeris header 行数？简单：先扫一遍统计卫星名
  std::map<std::string,uint32_t> satNameToId;
  {
    std::ifstream f(indir + "/ephemeris.csv");
    std::string line; std::getline(f, line);
    std::string field;
    while (std::getline(f, line)){
      if (line.empty()) continue;
      std::stringstream ss(line);
      std::vector<std::string> c; while (std::getline(ss, field, ',')) c.push_back(field);
      strip_cr(c);
      if (c.size() < 1) continue;
      const std::string& name = c[0];
      if (satNameToId.find(name) == satNameToId.end())
        satNameToId[name] = (uint32_t)satNameToId.size();
    }
  }
  uint32_t nSats = satNameToId.size();
  satNodes.Create(nSats);
  termNodes.Create(nTerms);
  g_satNodes = satNodes;
  for (uint32_t i = 0; i < nSats; ++i) g_satId2Idx[satNodes.Get(i)->GetId()] = i;
  std::cout << "  卫星节点=" << nSats << " 终端节点=" << nTerms << std::endl;

  // 安装移动性（卫星用 ConstantPosition，逐步更新；终端静态）
  for (uint32_t i=0; i<nSats; ++i){
    Ptr<Node> n = satNodes.Get(i);
    Ptr<ConstantPositionMobilityModel> mm = CreateObject<ConstantPositionMobilityModel>();
    mm->SetPosition(Vector(0,0,0));
    n->AggregateObject(mm);
  }
  for (uint32_t i=0; i<nTerms; ++i){
    Ptr<Node> n = termNodes.Get(i);
    Ptr<ConstantPositionMobilityModel> mm = CreateObject<ConstantPositionMobilityModel>();
    uint32_t tid = n->GetId();
    // 终端 i 对应 CSV 中的第 i 行（terminals.csv 的 id 列即 0..nTerms-1）
    auto itp = termPosTmp.find(i);
    Ecef p = (itp != termPosTmp.end()) ? itp->second : Ecef{0,0,0};
    mm->SetPosition(Vector(p.x, p.y, p.z));
    n->AggregateObject(mm);
    g_termPos[tid] = p;   // 几何查询按 ns-3 节点 id 索引（与 visibleAt 一致）
  }

  // 读星历到 g_eph（按 nodeId）
  if (!readEphemeris(indir + "/ephemeris.csv", satNameToId)){
    std::cerr << "读取星历失败!" << std::endl; return 1;
  }
  // 建立 name->id 反向：g_eph 已用 nodeId 作 key（satNameToId 值即创建顺序的 nodeId）
  // 设置初始位置 & 调度逐步更新
  uint32_t nSteps = 0;
  for (auto& kv : g_eph){ nSteps = std::max(nSteps, (uint32_t)kv.second.size()); }
  for (auto& kv : g_eph){
    uint32_t id = kv.first; double dt = g_stepDt[id];
    for (uint32_t s=0; s<kv.second.size(); ++s){
      Simulator::Schedule(Seconds(s*dt), &UpdatePositions, id, s);
    }
  }
  std::cout << "  星历采样步数=" << nSteps << std::endl;

  precomputeWindows();

  // 安装应用
  // ★审计修复★：到达时刻 RNG 由 rngSeed 派生（原固定 12345，无法做多种子实验）
  std::mt19937 rng((uint32_t)(rngSeed ^ 0x9E3779B97F4A7C15ULL));
  uint8_t rootKey[32]; deriveRootKey(rootKey);
  for (uint32_t i=0; i<nSats; ++i){
    Ptr<LeoApp> app = CreateObject<LeoApp>();
    app->SetSatellite(i);
    satNodes.Get(i)->AddApplication(app);
    app->SetStartTime(Seconds(0));
    app->SetStopTime(Seconds(simDur));
  }
  for (uint32_t i=0; i<nTerms; ++i){
    Ptr<Node> n = termNodes.Get(i);
    std::uniform_real_distribution<double> u(0,1);
    double burstT = burstStart + u(rng)*burstWin;
    Ptr<LeoApp> app = CreateObject<LeoApp>();
    app->SetTerminal(i, termTag[i], burstT);
    // ★审计修复★：伪造终端分「盲伪造 / 密钥泄露」两类（后者密码层不可检出 → 漏检率）
    bool isForged = (g_forgedRatio > 0 && u(rng) < g_forgedRatio);
    bool isCompromised = isForged && (u(rng) < g_compromisedShare);
    app->SetForged(isForged, isCompromised);
    app->InitCredential(rootKey);   // 所有终端初始化派生密钥与假名
    n->AddApplication(app);
    app->SetStartTime(Seconds(0));
    app->SetStopTime(Seconds(simDur));
  }

  // 打开 trace
  g_trace.open(outdir + "/access_trace.csv");
  // ★契约 16 列（与 sim/interfaces.TRACE_COLS 严格一致）★
  g_trace << "event_type,terminal,tag,t_s,serving_sat,target_sat,value_ms,doppler_hz,slant_km,"
          << "result,predict_mismatch,pingpong,ho_el_cost_deg,forged,auth_result,ebno_db\n";

  std::cout << "  Simulator::Run() 开始（真实离散事件调度）..." << std::endl;
  auto t0 = std::chrono::high_resolution_clock::now();
  Simulator::Run();
  Simulator::Destroy();
  auto t1 = std::chrono::high_resolution_clock::now();
  double wall = std::chrono::duration<double>(t1-t0).count();
  g_trace.close();

  std::cout << "  仿真完成。trace -> " << outdir << "/access_trace.csv" << std::endl;
  std::cout << "  ns-3 调度墙钟时间=" << std::fixed << std::setprecision(2) << wall << "s" << std::endl;
  std::cout << "  [审计] ns-3 版本=ns-3-dev(3-dev) 模块=core/network/mobility(自定义LEO信道)"
            << " 卫星=" << nSats << " 终端=" << nTerms << std::endl;
  std::cout << "  [DBG] AttemptAccess调用=" << g_dbg_attempt << " REQ发送=" << g_dbg_req
            << " 伪造拦截=" << g_dbg_forged_blocked << " 碰撞重试超限失败=" << g_dbg_collision_fail
            << " 漏检(密钥泄露)=" << g_dbg_forged_missed
            << " 确认失败=" << g_dbg_confirm_fail << " 重连回退(RACH)=" << g_dbg_rerach << std::endl;
  // ★P1/P2 双轨对齐统计★：供 run_ns3.py 解析后回填 summary（对齐 Python protocol.py）
  std::cout << "  [P2] 预迁移命中=" << g_dbg_premig_hit << " 预迁移回退=" << g_dbg_premig_miss
            << " 假名轮换=" << g_dbg_pseudo_rotation
            << " 切换总时延和ms=" << std::fixed << std::setprecision(2) << g_dbg_ho_total_ms
            << " 重连额外时延和ms=" << std::setprecision(2) << g_dbg_rerach_extra_ms << std::endl;
  if (g_priorityMode == "dp"){
    double avgGh = (g_dpNGuard > 0) ? (g_dpSumGh / (double)g_dpNGuard) : 0.0;
    uint64_t reclaim = 0; for (auto& kv : g_dpReclaim) reclaim += kv.second;
    std::cout << "  [DP] 科学版自适应阈值：平均预留 g_h=" << std::fixed << std::setprecision(2) << avgGh
              << " 阈值重算次数=" << g_dpNGuard
              << " 闲置预留回收(med/low 复用高危区)=" << reclaim
              << " ε(QoS上界)=" << std::setprecision(2) << g_prioEps
              << " 负载标定=" << std::setprecision(2) << g_prioLoadCal << std::endl;
  }
  return 0;
}
