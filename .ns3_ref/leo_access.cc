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

static double g_maskDeg = 25.0;
static double g_carrierHz = 2.0e9;
static double g_hoLeadS = 20.0;
static double g_tickS = 1.0;
static double g_accessProcMs = 3.0;
static double g_simDur = 3600.0;
// ---- T4 认证 / RACH 基线 / 碰撞拥塞（与 Python 轨 scenario.py 同参）----
static double     g_authExtraMs    = 0.0;    // 星上轻量凭证校验额外时延(ms)
static double     g_step4ExtraMs   = 400.0;  // 四步 RACH 附加时延(ms，RAR 等待+竞争解决)
static uint32_t   g_rachSteps      = 2;      // 2=两步预补偿；4=Rel-17 四步基线
static double     g_forgedRatio    = 0.0;    // 伪造终端占比（dev_sig 校验失败→拦截）
static bool       g_collisionOn    = false;  // 碰撞/拥塞模型开关
static uint32_t   g_rachCapacity   = 64;     // 每 10ms 时间片星上前导码受理上限
static double     g_retryIntervalMs = 500.0; // 碰撞退避均匀抽样上限(ms)
static uint32_t   g_retryMax       = 20;     // 碰撞重试上限（超限判失败）
static std::map<std::pair<uint32_t,uint32_t>, uint32_t> g_slotLoad; // (sat,10ms槽)->已受理数
static std::mt19937 g_runRng(20260901);                            // 运行时退避 RNG
static std::uniform_real_distribution<double> g_u01(0.0, 1.0);
static std::ofstream g_trace;
static uint64_t g_dbg_attempt = 0, g_dbg_req = 0, g_dbg_forged_blocked = 0, g_dbg_collision_fail = 0;

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

// ============================ 协议头 ============================
class LeoHeader : public Header {
public:
  uint8_t  msgType = 0;   // 1=ACCESS_REQ 2=ACCESS_GRANT 3=HO_PREP 4=HO_CONFIRM 5=ACCESS_DENY
  uint32_t termId = 0;
  uint32_t satId  = 0;
  uint16_t seq    = 0;
  double   dopplerHz = 0;
  double   taSec  = 0;    // Timing Advance（预补偿）
  uint8_t  forged = 0;    // 1=伪造终端（星上 dev_sig 校验失败 → 拦截）
  uint8_t  steps  = 0;    // RACH 模式（2=两步；4=四步，供星上附加时延建模）

  static TypeId GetTypeId(){
    static TypeId tid = TypeId("ns3::LeoHeader").SetParent<Header>().SetGroupName("Sim")
      .AddConstructor<LeoHeader>();
    return tid;
  }
  virtual TypeId GetInstanceTypeId() const { return GetTypeId(); }
  virtual void Print(std::ostream &os) const { os << "Leo msg=" << (int)msgType; }
  virtual uint32_t GetSerializedSize() const { return 1+4+4+2+8+8+1+1; }
  virtual void Serialize(Buffer::Iterator s) const {
    s.WriteU8(msgType); s.WriteU32(termId); s.WriteU32(satId);
    s.WriteU16(seq);
    s.WriteU64((uint64_t)(int64_t)(dopplerHz*1e6));
    s.WriteU64((uint64_t)(int64_t)(taSec*1e9));
    s.WriteU8(forged); s.WriteU8(steps);
  }
  virtual uint32_t Deserialize(Buffer::Iterator s){
    msgType = s.ReadU8(); termId = s.ReadU32(); satId = s.ReadU32();
    seq = s.ReadU16();
    dopplerHz = (double)(int64_t)s.ReadU64()/1e6;
    taSec = (double)(int64_t)s.ReadU64()/1e9;
    forged = s.ReadU8(); steps = s.ReadU8();
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
             m_retryCnt(0), m_servingSat(0),
             m_servingLos(0), m_accessFinT(0), m_seq(0) {}
  virtual ~LeoApp() {}

  void SetTerminal(uint32_t idx, const std::string& tag, double burstT){
    m_isTerminal = true; m_termIdx = idx; m_tag = tag; m_burstT = burstT;
  }
  void SetSatellite(uint32_t idx){ m_isTerminal = false; m_satIdx = idx; }
  void SetForged(bool f){ m_forged = f; }

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
  void TermFail(){
    double t = Simulator::Now().GetSeconds();
    g_trace << "ACCESS," << m_termIdx << "," << m_tag << "," << std::fixed
            << std::setprecision(3) << t << ",-1,-1,"
            << std::setprecision(2) << -1.0 << ",0.0,0.0,fail,0,0,0,"
            << (m_forged?1:0) << "\n";
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
    // 碰撞/拥塞限流（与 Python 轨同参）：星上按 (卫星, 10ms 时间片) 限流；
    // 伪造终端不占信道（星上直接拦截，同 Python 轨“dev_sig 校验失败→不占信道”）
    if (!m_forged && g_collisionOn){
      uint32_t slot10 = (uint32_t)(t / 0.01);
      auto key = std::make_pair(best, slot10);
      uint32_t used = g_slotLoad.count(key) ? g_slotLoad[key] : 0;
      if (used >= g_rachCapacity){
        if (m_retryCnt >= g_retryMax){      // 重试超限 → 接入失败
          g_dbg_collision_fail++;
          TermFail();
          return;
        }
        m_retryCnt++;
        double backoff = g_u01(g_runRng) * g_retryIntervalMs / 1000.0; // 均匀退避（同 Python）
        Simulator::Schedule(Seconds(backoff), &LeoApp::AttemptAccess, this);
        return;
      }
      g_slotLoad[key] = used + 1;            // 受理（占用该时隙容量）
    }
    m_servingSat = best;
    double rg = rangeKm(g_termPos[m_nodeId], satPosAt(best, t));
    double delay = rg / C_KM_S;
    g_dbg_req++;
    // 两步接入：终端发 REQ（携带 GNSS 估计的 TA/多普勒/伪造标记/RACH 模式），卫星回 GRANT
    LeoHeader req; req.msgType=1; req.termId=m_termIdx; req.satId=best; req.seq=++m_seq;
    req.dopplerHz = dopplerHz(best, g_termPos[m_nodeId], t);
    req.taSec = 2.0 * delay; // 开环预补偿
    req.forged = m_forged ? 1 : 0;
    req.steps = (uint8_t)g_rachSteps;
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
        DoHandover(best, t, t, bestLos, t);
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
  void PredictAndHandover(double t){
    double tLos = m_servingLos;
    // 决策时刻（同 Python: t_ho = max(LOS − ho_lead, 连接建立时刻)）
    double tHo = std::max(tLos - g_hoLeadS, m_accessFinT);
    // 候选 A：LOS 时刻仍可见的非服务星（重叠覆盖，先建后断，中断≈0）
    uint32_t cand = 0; double candLos = -1;
    auto visLos = visibleAt(m_nodeId, tLos);
    auto itWin = g_termWins.find(m_nodeId);
    for (auto& v : visLos){
      uint32_t s = std::get<0>(v);
      if (s == m_servingSat) continue;
      double los = std::get<2>(v);
      // 防御（同 Python）：候选 LOS 必须严格晚于服务星 LOS，否则为预测失败，不入选
      if (los <= tLos + 1e-9) continue;
      // 防御（同 Python overlap）：候选须在决策时刻已可见（aos ≤ tHo），
      // 未升起星一律交候选 B（等升起）兜底，避免切到“未升起星”导致服务链掉落
      double aosOf = -1;
      if (itWin != g_termWins.end()){
        for (const auto& w : itWin->second){
          if (w.satId == s && w.aos <= tLos && tLos <= w.los){ aosOf = w.aos; break; }
        }
      }
      if (aosOf < 0 || aosOf > tHo + 1e-9) continue;
      if (los > candLos){ candLos = los; cand = s; }   // 驻留最长（稳定优先，抑制乒乓）
    }
    if (cand != 0){
      // 重叠候选在 LOS 时刻已可见 → 可连时刻即 LOS（先建后断）
      DoHandover(cand, t, tLos, candLos, tHo);
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
    DoHandover(nid, t, naos, nlos, tLos);
  }

private:
  bool m_active;
  bool m_isTerminal;
  uint32_t m_nodeId;
  uint32_t m_termIdx;
  uint32_t m_satIdx;
  std::string m_tag;
  double m_burstT;
  bool m_accessed;
  bool m_forged;          // T4：伪造终端标记（星上 dev_sig 校验失败 → 拦截）
  uint32_t m_retryCnt;    // 碰撞退避重试计数（超 g_retryMax 判失败）
  uint32_t m_servingSat;
  double m_servingLos;
  double m_accessFinT;    // 本段连接建立完成时刻（GRANT 收到时）——用于决策时刻下界
  uint16_t m_seq;

  void DoHandover(uint32_t cand, double t, double candConnect, double candLos, double tHo){
    double rg = rangeKm(g_termPos[m_nodeId], satPosAt(cand, t));
    double delay = rg / C_KM_S;
    // 预测失配（契约 2.1，与 Python 轨一致）：决策选中的候选 vs 执行时刻（服务星 LOS）
    // 仰角最优可见星：
    auto visLos = visibleAt(m_nodeId, m_servingLos);
    uint32_t bestEL = 0; double be = -1e9;
    for (auto& v : visLos){
      uint32_t s = std::get<0>(v);
      if (s == m_servingSat) continue;
      double el = elevationDeg(g_termPos[m_nodeId], satPosAt(s, m_servingLos));
      if (el > be){ be = el; bestEL = s; }
    }
    bool mismatch = (bestEL != 0) && (bestEL != cand);
    // 仰角代价（契约 2.1，与 Python 轨 ho_el_cost_deg 同口径）：仰角最优 − 选中星，@LOS 时刻
    double elCost = 0.0;
    if (bestEL != 0){
      elCost = be - elevationDeg(g_termPos[m_nodeId], satPosAt(cand, m_servingLos));
      if (elCost < 0) elCost = 0.0;
    }
    // 乒乓（契约 2.1）：候选在决策时刻剩余可见 < 60s
    bool pingpong = (candLos - tHo) < 60.0;
    // 中断（契约 2.1）：业务间隙 = max(0, 新链可用 − 旧链丢失)，先建后断 → 通常 0
    double confirmT = t + 2.0*delay + g_accessProcMs/1000.0;
    double avail = std::max(candConnect, confirmT);
    double interrupt_ms = std::max(0.0, avail - m_servingLos) * 1000.0;
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
            << std::setprecision(3) << rg << ",success,"
            << (mismatch?1:0) << "," << (pingpong?1:0) << ","
            << std::setprecision(2) << elCost << ",0\n";
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
      g_trace << "ACCESS," << m_termIdx << "," << m_tag << "," << std::fixed
              << std::setprecision(3) << t << "," << h.satId << "," << h.satId << ","
              << std::setprecision(2) << delay_ms << ","
              << std::setprecision(1) << std::abs(h.dopplerHz) << ","
              << std::setprecision(3) << rg << ",success,0,0,0,0\n";
      m_accessed = true;
      m_accessFinT = t;
      Simulator::Schedule(Seconds(g_tickS), &LeoApp::Tick, this);
    } else if (h.msgType == 4){ // HO_CONFIRM
      // 中断已在 DoHandover 决策时记录；此处仅确认（无需重复写）
    } else if (h.msgType == 5){ // ACCESS_DENY：伪造终端被拦截（不进入接入流程）
      TermFail();
    }
  }

  void HandleSatelliteRx(const LeoHeader& h, uint32_t srcId){
    double t = Simulator::Now().GetSeconds();
    if (h.msgType == 1){ // ACCESS_REQ
      // 星上处理时序（与 Python 轨 access_delay = 2×传播 + 星上处理 + 认证/四步附加 同参）
      double authWait  = g_authExtraMs / 1000.0;                       // 轻量凭证校验
      double rachWait  = authWait + (g_rachSteps >= 4 ? g_step4ExtraMs / 1000.0 : 0.0); // 四步附加
      double rg = rangeKm(g_termPos[srcId], satPosAt(m_nodeId, t));
      double delay = rg / C_KM_S;
      if (h.forged){ // T4：dev_sig 校验失败 → 拒绝（不进入两步/四步流程，不占 RACH 容量）
        g_dbg_forged_blocked++;
        LeoHeader deny; deny.msgType=5; deny.termId=h.termId; deny.satId=m_nodeId;
        deny.seq=h.seq; deny.forged=1;
        Ptr<Packet> p = Create<Packet>(); p->AddHeader(deny);
        Simulator::Schedule(Seconds(authWait), &LeoChannel::Tx, g_channel, m_nodeId, srcId, p, delay);
        return;
      }
      // 正常终端：GRANT（放行；四步基线的 RAR 等待+竞争解决附加时延在星上处理阶段建模）
      LeoHeader grant; grant.msgType=2; grant.termId=h.termId; grant.satId=m_nodeId;
      grant.seq=h.seq; grant.dopplerHz = dopplerHz(m_nodeId, g_termPos[srcId], t);
      grant.taSec = 2.0*delay; grant.steps = h.steps;
      Ptr<Packet> p = Create<Packet>(); p->AddHeader(grant);
      Simulator::Schedule(Seconds(g_accessProcMs/1000.0 + rachWait),
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
  double authExtraMs=0, step4ExtraMs=400, forgedRatio=0;
  double retryIntervalMs=500;
  uint32_t rachSteps=2, rachCapacity=64, retryMax=20, nTerms=80;
  int32_t collisionOn=0;

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
  cmd.AddValue("authExtraMs", "认证附加时延(ms)", authExtraMs);
  cmd.AddValue("rachSteps", "RACH 模式(2=两步 4=四步)", rachSteps);
  cmd.AddValue("step4ExtraMs", "四步附加时延(ms)", step4ExtraMs);
  cmd.AddValue("collisionOn", "碰撞/拥塞模型开关", collisionOn);
  cmd.AddValue("rachCapacity", "每10ms时隙受理上限", rachCapacity);
  cmd.AddValue("retryIntervalMs", "退避间隔上限(ms)", retryIntervalMs);
  cmd.AddValue("retryMax", "碰撞重试上限", retryMax);
  cmd.Parse(argc, argv);

  g_maskDeg = maskDeg; g_carrierHz = carrierHz; g_hoLeadS = hoLead;
  g_tickS = tickS; g_accessProcMs = accessProcMs; g_simDur = simDur;
  g_authExtraMs = authExtraMs; g_step4ExtraMs = step4ExtraMs;
  g_rachSteps = rachSteps; g_forgedRatio = forgedRatio;
  g_collisionOn = (collisionOn != 0); g_rachCapacity = rachCapacity;
  g_retryIntervalMs = retryIntervalMs; g_retryMax = retryMax;

  std::cout << "[LeoAccess] ns-3 离散事件接入/切换仿真启动" << std::endl;
  std::cout << "  输入目录=" << indir << " 输出目录=" << outdir << std::endl;
  std::cout << "  掩角=" << maskDeg << "° 时长=" << simDur << "s 步长=" << stepS
            << "s 突发=[" << burstStart << "," << burstStart+burstWin << "]s" << std::endl;
  std::cout << "  两步/四步=" << rachSteps << " 伪造占比=" << forgedRatio
            << " 认证附加=" << authExtraMs << "ms 四步附加=" << step4ExtraMs << "ms" << std::endl;
  std::cout << "  碰撞开关=" << collisionOn << " 容量=" << rachCapacity
            << "/10ms 退避=" << retryIntervalMs << "ms 重试上限=" << retryMax << std::endl;

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
  std::mt19937 rng(12345);
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
    app->SetForged(g_forgedRatio > 0 && u(rng) < g_forgedRatio); // T4：按占比抽样伪造终端
    n->AddApplication(app);
    app->SetStartTime(Seconds(0));
    app->SetStopTime(Seconds(simDur));
  }

  // 打开 trace
  g_trace.open(outdir + "/access_trace.csv");
  g_trace << "event_type,terminal,tag,t_s,serving_sat,target_sat,value_ms,doppler_hz,slant_km,result,predict_mismatch,pingpong,ho_el_cost_deg,forged\n";

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
            << " 伪造拦截=" << g_dbg_forged_blocked << " 碰撞重试超限失败=" << g_dbg_collision_fail << std::endl;
  return 0;
}
