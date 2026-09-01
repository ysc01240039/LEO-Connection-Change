/* -*- Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
 * ============================================================================
 *  ns-3 LEO CBDP Packet-Level Evaluation (leo_cbdp_eval.cc)
 *
 *  Modes:
 *    --mode=compare : protocol comparison run (OLSR|AODV|CBDP|Dijkstra) with
 *                     REAL control-packet counting (OLSR udp/698, AODV udp/654,
 *                     CBDP injected control traffic udp/9999), warm-up period,
 *                     rxBytes-based throughput, FlowMonitor XML histograms.
 *    --mode=failure : CBDP failure-injection run. At failTime a random
 *                     failFrac fraction of ISL links goes down; after
 *                     detectDelay the CBDP routes are recomputed on the
 *                     degraded graph. Goodput is polled every 0.5 s.
 *
 *  CBDP core count is passed explicitly (--nCores) from the C++ PDE N-scan
 *  (gamma=6.0, beta=0.6): N=200->137, 400->117, 600->108, 800->100, 1000->93.
 *
 *  ISL data rate default 100 Gbps (paper Table simulation_params B_ISL).
 * ============================================================================
 */

#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/flow-monitor-module.h"
#include "ns3/internet-module.h"
#include "ns3/ipv4-list-routing-helper.h"
#include "ns3/ipv4-static-routing-helper.h"
#include "ns3/ipv4-global-routing-helper.h"
#include "ns3/network-module.h"
#include "ns3/olsr-helper.h"
#include "ns3/aodv-helper.h"
#include "ns3/point-to-point-module.h"
#include "ns3/udp-header.h"

#include <cmath>
#include <vector>
#include <set>
#include <algorithm>
#include <random>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <map>
#include <string>
#include <queue>
#include <functional>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("LeoCbdpEval");

// ============================================================================
// Physical constants
// ============================================================================
const double R_EARTH_KM = 6371.0;
const double PI = 3.14159265358979323846;
const int    ISL_PER_SAT = 4;
const uint16_t DATA_PORT = 9;
const uint16_t CBDP_CTRL_PORT = 9999;
const uint16_t OLSR_PORT = 698;
const uint16_t AODV_PORT = 654;

// CBDP protocol timing (paper Section VI)
const double T_RECONFIG = 15.0;   // s
const double T_CTRL_START = 10.0; // s

// Control message sizes (bytes), paper Table III (message types):
//   LOAD_REPORT 64B (sat -> SNC), ROUTE_DIST 128B (SNC -> sat),
//   CORE_ASSIGN 32B (SNC -> sat), CORE_TOPO 256B/pair (SNC <-> SNC).
const uint32_t LOAD_REPORT_BYTES   = 64;   // per-satellite load report
const uint32_t ROUTE_DIST_BYTES    = 128;  // routing table distribution
const uint32_t CORE_ASSIGN_BYTES   = 32;   // assignment confirmation
const uint32_t MESH_UPDATE_BYTES   = 256;  // SNC mesh update (CORE_TOPO)
const int        K_MESH            = 6;    // k_c = 6 mesh degree

// ============================================================================
// Global control-traffic counters (via Ipv4L3Protocol::Tx hook)
// ============================================================================
static uint64_t g_ctrlTxPkts  = 0;
static uint64_t g_ctrlTxBytes = 0;

void CtrlTxTrace(std::string context, Ptr<const Packet> packet, Ptr<Ipv4> ipv4, uint32_t ifIndex) {
    // Raw-byte parse (assert-free): IPv4 header + UDP dest port.
    uint32_t sz = packet->GetSize();
    if (sz < 28) return;
    uint8_t buf[68];
    uint32_t n = sz < 68 ? sz : 68;
    packet->CopyData(buf, n);
    if ((buf[0] >> 4) != 4) return;              // IPv4 only
    uint32_t ihl = (uint32_t)(buf[0] & 0x0f) * 4;
    if (ihl < 20 || ihl + 8 > n) return;
    if (buf[9] != 17) return;                    // UDP only
    uint16_t dp = (uint16_t)((buf[ihl + 2] << 8) | buf[ihl + 3]);
    if (dp == OLSR_PORT || dp == AODV_PORT || dp == CBDP_CTRL_PORT) {
        g_ctrlTxPkts++;
        g_ctrlTxBytes += sz;
    }
}

// ============================================================================
// Vec3 + position generators
// ============================================================================
struct Vec3 {
    double x, y, z;
    Vec3() : x(0), y(0), z(0) {}
    Vec3(double a, double b, double c) : x(a), y(b), z(c) {}
    double distance(const Vec3& o) const {
        double dx = x - o.x, dy = y - o.y, dz = z - o.z;
        return std::sqrt(dx*dx + dy*dy + dz*dz);
    }
};

std::vector<Vec3> GenerateSatellitePositions(int n_total, int n_layers, uint32_t seed) {
    std::vector<Vec3> positions;
    std::mt19937 rng(seed);
    std::uniform_real_distribution<double> phase_dist(0.0, 2.0 * PI);
    const double heights[5] = {500.0, 800.0, 1100.0, 1400.0, 1700.0};
    int sats_per_layer = n_total / n_layers;
    int remainder = n_total - sats_per_layer * n_layers;
    for (int l = 0; l < n_layers; l++) {
        int n_s = sats_per_layer + (l == n_layers - 1 ? remainder : 0);
        double r = R_EARTH_KM + heights[l];
        double phi_golden = PI * (3.0 - std::sqrt(5.0));
        double phase_offset = phase_dist(rng);
        for (int i = 0; i < n_s; i++) {
            double y = 1.0 - (i / std::max(n_s - 1.0, 1.0)) * 2.0;
            double radius_at_y = std::sqrt(1.0 - y * y);
            double theta = phi_golden * i + phase_offset;
            positions.push_back(Vec3(std::cos(theta) * radius_at_y * r,
                                     y * r,
                                     std::sin(theta) * radius_at_y * r));
        }
    }
    return positions;
}

// 20 ground stations (paper system model, G = 20)
std::vector<Vec3> GenerateGroundStationPositions() {
    const double lat_lon[20][2] = {
        {39.9, 116.4}, {31.2, 121.5}, {40.7, -74.0}, {51.5, -0.1}, {35.7, 139.7},
        {48.9, 2.3},   {37.8, -122.4},{55.8, 37.6},  {19.4, -99.1}, {-33.9, 151.2},
        {1.3, 103.8},  {28.6, 77.2},  {-23.6, -46.6},{55.0, -3.4}, {52.5, 13.4},
        {37.6, 127.0}, {-6.2, 106.8}, {22.3, 114.2}, {25.2, 55.3},  {35.0, 33.0}
    };
    std::vector<Vec3> gs;
    for (int i = 0; i < 20; i++) {
        double lat = lat_lon[i][0] * PI / 180.0;
        double lon = lat_lon[i][1] * PI / 180.0;
        double r = R_EARTH_KM;
        gs.push_back(Vec3(r * std::cos(lat) * std::cos(lon),
                          r * std::sin(lat),
                          r * std::cos(lat) * std::sin(lon)));
    }
    return gs;
}

// ============================================================================
// ISL edges: Kruskal MST (connectivity) + 4 nearest neighbors per satellite
// ============================================================================
struct Edge { int a, b; double dist; };

struct DSU {
    std::vector<int> p;
    explicit DSU(int n) : p(n) { for (int i = 0; i < n; i++) p[i] = i; }
    int find(int x) { return p[x] == x ? x : (p[x] = find(p[x])); }
    void uni(int a, int b) { p[find(a)] = find(b); }
    bool same(int a, int b) { return find(a) == find(b); }
};

std::vector<Edge> BuildISLEdges(const std::vector<Vec3>& sat) {
    int N = (int)sat.size();
    std::vector<Edge> edges, all;
    for (int i = 0; i < N; i++)
        for (int j = i + 1; j < N; j++)
            all.push_back({i, j, sat[i].distance(sat[j])});
    std::sort(all.begin(), all.end(),
              [](const Edge& a, const Edge& b) { return a.dist < b.dist; });
    DSU dsu(N);
    for (size_t e = 0; e < all.size(); e++) {
        if (!dsu.same(all[e].a, all[e].b)) {
            dsu.uni(all[e].a, all[e].b);
            edges.push_back(all[e]);
        }
    }
    for (int i = 0; i < N; i++) {
        std::vector<std::pair<double, int>> cand;
        for (int j = 0; j < N; j++) {
            if (i == j) continue;
            cand.push_back({sat[i].distance(sat[j]), j});
        }
        std::sort(cand.begin(), cand.end());
        int take = std::min((int)cand.size(), ISL_PER_SAT);
        for (int k = 0; k < take; k++) {
            int j = cand[k].second;
            if (i < j) edges.push_back({i, j, cand[k].first});
        }
    }
    std::sort(edges.begin(), edges.end(),
              [](const Edge& a, const Edge& b) {
                  return a.a != b.a ? a.a < b.a : a.b < b.b;
              });
    edges.erase(std::unique(edges.begin(), edges.end(),
                            [](const Edge& a, const Edge& b) {
                                return a.a == b.a && a.b == b.b;
                            }),
                edges.end());
    return edges;
}

// ============================================================================
// k-means core detection with explicit n_cores (from C++ PDE N-scan)
// ============================================================================
struct CoreInfo {
    std::vector<Vec3> core_pos;
    std::vector<int> core_sat;      // satellite id nearest to each centroid
    std::vector<int> sat_to_core;
    int n_cores;
};

CoreInfo DetectCores(const std::vector<Vec3>& sat, int n_cores, uint32_t seed) {
    CoreInfo ci;
    int N = (int)sat.size();
    n_cores = std::max(1, std::min(n_cores, N));
    ci.n_cores = n_cores;
    std::mt19937 rng(seed + 7777);
    std::uniform_int_distribution<int> pick(0, N - 1);
    std::set<int> chosen;
    while ((int)ci.core_pos.size() < n_cores) {
        int idx = pick(rng);
        if (chosen.count(idx)) continue;
        chosen.insert(idx);
        ci.core_pos.push_back(sat[idx]);
    }
    ci.sat_to_core.assign(N, 0);
    for (int iter = 0; iter < 10; iter++) {
        for (int i = 0; i < N; i++) {
            double bd = 1e18; int bc = 0;
            for (int c = 0; c < n_cores; c++) {
                double d = sat[i].distance(ci.core_pos[c]);
                if (d < bd) { bd = d; bc = c; }
            }
            ci.sat_to_core[i] = bc;
        }
        std::vector<Vec3> nu(n_cores, Vec3(0,0,0));
        std::vector<int> cnt(n_cores, 0);
        for (int i = 0; i < N; i++) {
            int c = ci.sat_to_core[i];
            nu[c].x += sat[i].x; nu[c].y += sat[i].y; nu[c].z += sat[i].z;
            cnt[c]++;
        }
        for (int c = 0; c < n_cores; c++)
            if (cnt[c] > 0) { nu[c].x /= cnt[c]; nu[c].y /= cnt[c]; nu[c].z /= cnt[c]; }
        ci.core_pos = nu;
    }
    ci.core_sat.assign(n_cores, 0);
    for (int c = 0; c < n_cores; c++) {
        double bd = 1e18; int best = 0;
        for (int i = 0; i < N; i++) {
            double d = ci.core_pos[c].distance(sat[i]);
            if (d < bd) { bd = d; best = i; }
        }
        ci.core_sat[c] = best;
    }
    return ci;
}

// ============================================================================
// Dijkstra from one source on the (possibly degraded) ISL graph.
// Returns parent[] (next hop toward source) and dist[].
// ============================================================================
void SptFrom(int src, int nSats, const std::vector<Edge>& edges,
             std::vector<int>& par, std::vector<double>& dist) {
    dist.assign(nSats, 1e18);
    par.assign(nSats, -1);
    std::vector<bool> done(nSats, false);
    dist[src] = 0.0;
    // adjacency
    std::vector<std::vector<std::pair<int,double>>> adj(nSats);
    for (auto& e : edges) {
        adj[e.a].push_back({e.b, e.dist});
        adj[e.b].push_back({e.a, e.dist});
    }
    for (int it = 0; it < nSats; it++) {
        int u = -1; double bd = 1e18;
        for (int v = 0; v < nSats; v++)
            if (!done[v] && dist[v] < bd) { bd = dist[v]; u = v; }
        if (u < 0) break;
        done[u] = true;
        for (auto& nb : adj[u])
            if (dist[u] + nb.second < dist[nb.first]) {
                dist[nb.first] = dist[u] + nb.second;
                par[nb.first] = u;
            }
    }
}

// ============================================================================
// Efficient single-source Dijkstra over a pre-built weighted adjacency list
// (binary heap, O(E log N)). Used by the ported static baselines
// (Nearest3 / LPIH / PFNSAR) to keep N=800/1000 tractable.
// adj[i] = vector of (neighbor, weight); parent[i] = next hop from i toward src.
// ============================================================================
void DijkstraSS(const std::vector<std::vector<std::pair<int,double>>>& adj,
                int src, std::vector<double>& dist, std::vector<int>& parent) {
    int n = (int)adj.size();
    dist.assign(n, 1e18);
    parent.assign(n, -1);
    std::priority_queue<std::pair<double,int>,
                        std::vector<std::pair<double,int>>,
                        std::greater<std::pair<double,int>>> pq;
    dist[src] = 0.0;
    pq.push({0.0, src});
    while (!pq.empty()) {
        auto top = pq.top(); pq.pop();
        double d = top.first; int u = top.second;
        if (d > dist[u] + 1e-12) continue;
        for (auto& e : adj[u]) {
            int v = e.first; double w = e.second;
            if (dist[u] + w < dist[v] - 1e-12) {
                dist[v] = dist[u] + w;
                parent[v] = u;
                pq.push({dist[v], v});
            }
        }
    }
}

// ============================================================================
// Global simulation context (for scheduled callbacks)
// ============================================================================
struct SimCtx {
    NodeContainer all;
    uint32_t nSats, nGs;
    std::vector<Edge> isl_edges;
    std::vector<Vec3> satPos;           // satellite ECI positions (for re-election)
    uint32_t seed;
    std::map<std::pair<uint32_t,uint32_t>, int> linkIface;
    std::map<std::pair<uint32_t,uint32_t>, Ipv4Address> linkIp;
    std::vector<Ipv4Address> gsAddr;
    std::vector<Ipv4Address> satAddr;   // first-link address of each satellite
    std::vector<uint32_t> gs_sat;
    CoreInfo cores;
    std::string protocol;
    // routes installed by CBDP: per node, set of destination addresses
    std::map<uint32_t, std::set<Ipv4Address>> cbdpRoutes;
    // failure state
    std::vector<int> failedIfacesA;     // (node, iface) pairs flattened
    std::vector<int> failedIfacesB;
    // goodput polling
    std::vector<Ptr<PacketSink>> sinks;
    double pollInterval;
    uint64_t lastRxBytes;
};

SimCtx g_ctx;

// Add one host route and record it for later removal
void AddCbdpRoute(uint32_t node, Ipv4Address dest, uint32_t next, uint32_t from) {
    // route at `node` toward `dest` via link node->next; gateway = next's IP on that link
    auto keyIf = std::make_pair(node, next);
    auto keyGw = std::make_pair(next, node);
    if (!g_ctx.linkIface.count(keyIf) || !g_ctx.linkIp.count(keyGw)) return;
    Ptr<Ipv4> ipv4 = g_ctx.all.Get(node)->GetObject<Ipv4>();
    Ipv4StaticRoutingHelper srh;
    Ptr<Ipv4StaticRouting> sr = srh.GetStaticRouting(ipv4);
    sr->AddHostRouteTo(dest, g_ctx.linkIp[keyGw], g_ctx.linkIface[keyIf]);
    g_ctx.cbdpRoutes[node].insert(dest);
}

// Remove all CBDP-installed host routes (matched by recorded destinations)
void ClearCbdpRoutes() {
    for (auto& kv : g_ctx.cbdpRoutes) {
        Ptr<Ipv4> ipv4 = g_ctx.all.Get(kv.first)->GetObject<Ipv4>();
        Ipv4StaticRoutingHelper srh;
        Ptr<Ipv4StaticRouting> sr = srh.GetStaticRouting(ipv4);
        // iterate backwards removing host routes whose dest is in the set
        for (int j = (int)sr->GetNRoutes() - 1; j >= 0; j--) {
            Ipv4RoutingTableEntry e = sr->GetRoute(j);
            if (e.GetDestNetworkMask() == Ipv4Mask("255.255.255.255") &&
                kv.second.count(e.GetDest())) {
                sr->RemoveRoute(j);
            }
        }
    }
    g_ctx.cbdpRoutes.clear();
}

// Install CBDP hierarchical routes on graph `edges` (excludes failed links)
void InstallCbdpRoutes(const std::vector<Edge>& edges) {
    uint32_t nSats = g_ctx.nSats, nGs = g_ctx.nGs;
    int n_cores = g_ctx.cores.n_cores;

    // SPT from each portal: next_to_portal[u][c]
    std::vector<std::vector<int>> next_to_portal(nSats);
    for (int c = 0; c < n_cores; c++) {
        std::vector<int> par; std::vector<double> dist;
        SptFrom(g_ctx.cores.core_sat[c], nSats, edges, par, dist);
        for (uint32_t i = 0; i < nSats; i++) next_to_portal[i].push_back(par[i]);
    }

    // Per-GS-destination member paths
    std::vector<std::vector<int>> next_to_member(nGs, std::vector<int>(nSats, -1));
    std::vector<std::vector<char>> on_path(nGs, std::vector<char>(nSats, 0));
    for (uint32_t dst = 0; dst < nGs; dst++) {
        int dst_sat = g_ctx.gs_sat[dst];
        if (g_ctx.cores.sat_to_core[dst_sat] < 0) continue; // access satellite isolated
        std::vector<int> par; std::vector<double> dist;
        SptFrom(dst_sat, nSats, edges, par, dist);
        for (uint32_t i = 0; i < nSats; i++)
            if ((int)i != dst_sat) next_to_member[dst][i] = par[i];
        int dst_core = g_ctx.cores.sat_to_core[dst_sat];
        int portal = g_ctx.cores.core_sat[dst_core];
        int cur = portal;
        while (cur != -1 && cur != dst_sat) {
            on_path[dst][cur] = 1;
            cur = par[cur];
        }
        on_path[dst][dst_sat] = 1;
    }

    // Data routes: each node -> each GS destination via the portal
    for (uint32_t u = 0; u < g_ctx.all.GetN(); u++) {
        for (uint32_t dst = 0; dst < nGs; dst++) {
            int dst_sat = g_ctx.gs_sat[dst];
            int dst_core = g_ctx.cores.sat_to_core[dst_sat];
            if (dst_core < 0) continue; // destination unreachable (isolated access satellite)
            int next = -1;
            if (u < nSats) {
                int cur = (int)u;
                if (cur == dst_sat) next = (int)(nSats + dst);
                else if (on_path[dst][cur]) next = next_to_member[dst][cur];
                else next = next_to_portal[cur][dst_core];
            } else {
                next = (int)g_ctx.gs_sat[u - nSats];
            }
            if (next >= 0 && (int)u != next)
                AddCbdpRoute(u, g_ctx.gsAddr[dst], (uint32_t)next, u);
        }
    }

    // Control routes:
    //  (a) every node -> every portal address (load reports, mesh updates)
    for (uint32_t u = 0; u < nSats; u++) {
        for (int c = 0; c < n_cores; c++) {
            int portal = g_ctx.cores.core_sat[c];
            if ((int)u == portal) continue;
            int next = next_to_portal[u][c];
            if (next >= 0) AddCbdpRoute(u, g_ctx.satAddr[portal], (uint32_t)next, u);
        }
    }
    //  (b) portal -> each member of its cluster (assignment messages):
    //      walk member->portal parent chain; at each node vj on the walk,
    //      next hop toward the member is the previous node in the walk.
    for (uint32_t m = 0; m < nSats; m++) {
        int c = g_ctx.cores.sat_to_core[m];
        if (c < 0) continue; // isolated member
        int portal = g_ctx.cores.core_sat[c];
        if ((int)m == portal) continue;
        // walk m -> portal, remember predecessor
        int prev = (int)m;
        int cur = next_to_portal[m][c];
        while (cur != -1) {
            AddCbdpRoute((uint32_t)cur, g_ctx.satAddr[m], (uint32_t)prev, (uint32_t)cur);
            if (cur == portal) break;
            prev = cur;
            cur = next_to_portal[cur][c];
        }
    }
}

// Send one CBDP control packet (best effort)
void SendCtrl(uint32_t fromNode, Ipv4Address to, uint32_t bytes) {
    Ptr<Socket> s = Socket::CreateSocket(g_ctx.all.Get(fromNode),
                                         UdpSocketFactory::GetTypeId());
    s->Connect(InetSocketAddress(to, CBDP_CTRL_PORT));
    Ptr<Packet> p = Create<Packet>(bytes);
    s->Send(p);
    s->Close();
}

// One CBDP control cycle (paper Section VI-C, Table III message types):
//   (N - n_cores) load reports (member -> portal),
//   (N - n_cores) route distributions + assignment confirmations
//   (portal -> member), n_cores * k_c mesh updates (portal <-> portal).
void CbdpControlCycle() {
    uint32_t nSats = g_ctx.nSats;
    int n_cores = g_ctx.cores.n_cores;
    // load reports
    for (uint32_t i = 0; i < nSats; i++) {
        int c = g_ctx.cores.sat_to_core[i];
        if (c < 0) continue; // isolated member
        int portal = g_ctx.cores.core_sat[c];
        if ((int)i == portal) continue;
        SendCtrl(i, g_ctx.satAddr[portal], LOAD_REPORT_BYTES);
    }
    // route distribution + assignment confirmation
    for (uint32_t m = 0; m < nSats; m++) {
        int c = g_ctx.cores.sat_to_core[m];
        if (c < 0) continue; // isolated member
        int portal = g_ctx.cores.core_sat[c];
        if ((int)m == portal) continue;
        SendCtrl((uint32_t)portal, g_ctx.satAddr[m], ROUTE_DIST_BYTES);
        SendCtrl((uint32_t)portal, g_ctx.satAddr[m], CORE_ASSIGN_BYTES);
    }
    // mesh updates: each portal to its K_MESH nearest portals
    for (int c = 0; c < n_cores; c++) {
        std::vector<std::pair<double,int>> d;
        for (int c2 = 0; c2 < n_cores; c2++) {
            if (c2 == c) continue;
            d.push_back({g_ctx.cores.core_pos[c].distance(g_ctx.cores.core_pos[c2]), c2});
        }
        std::sort(d.begin(), d.end());
        int take = std::min(K_MESH, (int)d.size());
        for (int k = 0; k < take; k++) {
            int p2 = g_ctx.cores.core_sat[d[k].second];
            SendCtrl((uint32_t)g_ctx.cores.core_sat[c], g_ctx.satAddr[p2], MESH_UPDATE_BYTES);
        }
    }
}

void ScheduleControl(double simTime) {
    for (double t = T_CTRL_START; t < simTime; t += T_RECONFIG)
        Simulator::Schedule(Seconds(t), &CbdpControlCycle);
}

// ============================================================================
// Failure injection + route recompute + goodput polling
// ============================================================================
// failMode semantics:
//   random  : fail a uniformly random set of ISLs (Fig. 8 baseline behavior)
//   core    : same number of ISLs, concentrated on SNC portal satellites
//             (fail all ISLs of successively sampled portals)
//   noncore : same number of ISLs, concentrated on non-portal satellites,
//             using only ISLs whose both endpoints are non-portal
void InjectFailures(double failFrac, uint32_t seed, const std::string& failMode) {
    std::mt19937 rng(seed + 31337);
    size_t totalE = g_ctx.isl_edges.size();
    size_t nFail = (size_t)std::round(failFrac * totalE);
    std::vector<char> isPortal(g_ctx.nSats, 0);
    for (int c = 0; c < g_ctx.cores.n_cores; c++)
        isPortal[g_ctx.cores.core_sat[c]] = 1;

    auto failEdge = [&](size_t ei) -> bool {
        Edge& e = g_ctx.isl_edges[ei];
        if (e.dist < 0.0) return false;
        int ifA = g_ctx.linkIface[{(uint32_t)e.a, (uint32_t)e.b}];
        int ifB = g_ctx.linkIface[{(uint32_t)e.b, (uint32_t)e.a}];
        g_ctx.all.Get(e.a)->GetObject<Ipv4>()->SetDown(ifA);
        g_ctx.all.Get(e.b)->GetObject<Ipv4>()->SetDown(ifB);
        g_ctx.failedIfacesA.push_back(ifA); // record for accounting only
        e.dist = -1.0; // mark failed
        return true;
    };

    size_t nFailed = 0;
    if (failMode == "random") {
        std::vector<size_t> idx(totalE);
        for (size_t i = 0; i < totalE; i++) idx[i] = i;
        std::shuffle(idx.begin(), idx.end(), rng);
        for (size_t k = 0; k < nFail && k < totalE; k++)
            if (failEdge(idx[k])) nFailed++;
    } else {
        bool coreTarget = (failMode == "core");
        std::vector<std::vector<size_t>> inc(g_ctx.nSats);
        for (size_t ei = 0; ei < totalE; ei++) {
            inc[g_ctx.isl_edges[ei].a].push_back(ei);
            inc[g_ctx.isl_edges[ei].b].push_back(ei);
        }
        std::vector<int> nodes;
        for (uint32_t i = 0; i < g_ctx.nSats; i++)
            if (coreTarget == (isPortal[i] == 1)) nodes.push_back((int)i);
        std::shuffle(nodes.begin(), nodes.end(), rng);
        for (int v : nodes) {
            if (nFailed >= nFail) break;
            for (size_t ei : inc[v]) {
                if (nFailed >= nFail) break;
                Edge& e = g_ctx.isl_edges[ei];
                if (!coreTarget && (isPortal[e.a] || isPortal[e.b])) continue;
                if (failEdge(ei)) nFailed++;
            }
        }
    }
    std::vector<int> deg(g_ctx.nSats, 0);
    for (auto& e : g_ctx.isl_edges)
        if (e.dist >= 0.0) { deg[e.a]++; deg[e.b]++; }
    int nIso = 0, nIsoPortal = 0;
    for (uint32_t i = 0; i < g_ctx.nSats; i++)
        if (deg[i] == 0) { nIso++; if (isPortal[i]) nIsoPortal++; }
    std::cout << "FAIL injected mode=" << failMode << " n=" << nFailed
              << " of " << totalE << " ISLs, isolated_sats=" << nIso
              << " isolated_portals=" << nIsoPortal << std::endl;
}

void RecomputeCbdpRoutes() {
    // build degraded edge set
    std::vector<Edge> degraded;
    for (auto& e : g_ctx.isl_edges) if (e.dist >= 0.0) degraded.push_back(e);
    // detect isolated satellites; if any portal lost all ISLs, re-execute
    // SNC detection on the surviving node set (paper Section V Phase 3:
    // heartbeat timeout triggers re-election, resetting to INIT)
    std::vector<int> deg(g_ctx.nSats, 0);
    for (auto& e : degraded) { deg[e.a]++; deg[e.b]++; }
    std::vector<char> isPortal(g_ctx.nSats, 0);
    for (int c = 0; c < g_ctx.cores.n_cores; c++)
        isPortal[g_ctx.cores.core_sat[c]] = 1;
    int nIso = 0, nIsoPortal = 0;
    for (uint32_t i = 0; i < g_ctx.nSats; i++)
        if (deg[i] == 0) { nIso++; if (isPortal[i]) nIsoPortal++; }
    if (nIsoPortal > 0) {
        std::vector<int> aliveIdx;
        std::vector<Vec3> alivePos;
        for (uint32_t i = 0; i < g_ctx.nSats; i++)
            if (deg[i] > 0) {
                aliveIdx.push_back((int)i);
                alivePos.push_back(g_ctx.satPos[i]);
            }
        CoreInfo ci = DetectCores(alivePos, g_ctx.cores.n_cores, g_ctx.seed);
        std::vector<int> remap(g_ctx.nSats, -1);
        for (size_t i = 0; i < aliveIdx.size(); i++)
            remap[aliveIdx[i]] = ci.sat_to_core[i];
        ci.sat_to_core = remap;
        for (int c = 0; c < ci.n_cores; c++)
            ci.core_sat[c] = aliveIdx[ci.core_sat[c]];
        g_ctx.cores = ci;
    }
    ClearCbdpRoutes();
    InstallCbdpRoutes(degraded);
    std::cout << "RECONFIG done at " << Simulator::Now().GetSeconds()
              << " s, remaining edges " << degraded.size()
              << " isolated_sats=" << nIso
              << " portals_lost=" << nIsoPortal
              << " reelection=" << (nIsoPortal > 0 ? 1 : 0) << std::endl;
}

void PollGoodput() {
    uint64_t tot = 0;
    for (auto& s : g_ctx.sinks) tot += s->GetTotalRx();
    double mbps = (tot - g_ctx.lastRxBytes) * 8.0 / (g_ctx.pollInterval * 1e6);
    g_ctx.lastRxBytes = tot;
    std::cout << "TPUT t=" << std::fixed << std::setprecision(1)
              << Simulator::Now().GetSeconds() << " mbps=" << std::setprecision(3)
              << mbps << std::endl;
    Simulator::Schedule(Seconds(g_ctx.pollInterval), &PollGoodput);
}

// ============================================================================
// Main
// ============================================================================
int main(int argc, char* argv[]) {
    std::string mode = "compare";
    uint32_t nSats = 1000;
    uint32_t nGs = 20;
    std::string protocol = "CBDP";
    uint32_t seed = 42;
    double simTime = 120.0;
    double flowStart = 45.0;
    uint32_t nCores = 93;
    std::string linkRate = "100Gbps";
    double linkDelayMs = 2.0;
    double failFrac = 0.05;
    double failTime = 75.0;
    double detectDelay = 3.0;
    std::string failMode = "random";
    double flowRateMbps = 1.0;
    std::string xmlOut = "";

    CommandLine cmd;
    cmd.AddValue("mode", "compare|failure", mode);
    cmd.AddValue("nSats", "Number of satellites", nSats);
    cmd.AddValue("nGs", "Number of ground stations", nGs);
    cmd.AddValue("protocol", "OLSR|AODV|CBDP|Dijkstra|Nearest3|LPIH|PFNSAR", protocol);
    cmd.AddValue("seed", "RNG seed", seed);
    cmd.AddValue("simTime", "Simulation time (s)", simTime);
    cmd.AddValue("flowStart", "Traffic start time (s)", flowStart);
    cmd.AddValue("nCores", "CBDP core count (from PDE N-scan)", nCores);
    cmd.AddValue("linkRate", "ISL data rate", linkRate);
    cmd.AddValue("linkDelayMs", "Per-hop link delay (ms)", linkDelayMs);
    cmd.AddValue("failFrac", "Fraction of ISLs to fail (failure mode)", failFrac);
    cmd.AddValue("failTime", "Failure injection time (s)", failTime);
    cmd.AddValue("detectDelay", "Failure detection delay (s)", detectDelay);
    cmd.AddValue("failMode", "random|core|noncore (failure mode)", failMode);
    cmd.AddValue("flowRateMbps", "Per-flow offered rate (Mbps)", flowRateMbps);
    cmd.AddValue("xmlOut", "FlowMonitor XML output path", xmlOut);
    cmd.Parse(argc, argv);

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(1);

    // --- Topology ---
    auto sat_pos = GenerateSatellitePositions(nSats, 5, seed);
    auto gs_pos_all = GenerateGroundStationPositions();
    gs_pos_all.resize(nGs);
    auto isl_edges = BuildISLEdges(sat_pos);

    NodeContainer satNodes; satNodes.Create(nSats);
    NodeContainer gsNodes; gsNodes.Create(nGs);
    NodeContainer all = NodeContainer(satNodes, gsNodes);

    // --- Routing stack ---
    Ipv4ListRoutingHelper list;
    Ipv4StaticRoutingHelper staticRouting;
    if (protocol == "OLSR") { OlsrHelper olsr; list.Add(olsr, 10); }
    else if (protocol == "AODV") { AodvHelper aodv; list.Add(aodv, 10); }
    else if (protocol == "Dijkstra") { Ipv4GlobalRoutingHelper global; list.Add(global, 0); }
    else { list.Add(staticRouting, 0); }
    InternetStackHelper internet;
    internet.SetRoutingHelper(list);
    internet.Install(all);

    // --- Links ---
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue(linkRate));
    std::ostringstream dl; dl << linkDelayMs << "ms";
    p2p.SetChannelAttribute("Delay", StringValue(dl.str()));
    p2p.SetQueue("ns3::DropTailQueue", "MaxSize", StringValue("1000p"));

    std::vector<std::pair<uint32_t,uint32_t>> links;
    for (auto& e : isl_edges) links.push_back({(uint32_t)e.a, (uint32_t)e.b});
    std::vector<uint32_t> gs_sat(nGs);
    // Physical access links per GS: Nearest3 spreads its ingress load over its
    // 3 nearest satellites and needs 3 physical GS<->satellite links; the other
    // protocols use the single nearest satellite as the access/egress point.
    int nAccessLinks = (protocol == "Nearest3") ? std::min(3, (int)nSats) : 1;
    for (uint32_t g = 0; g < nGs; g++) {
        std::vector<std::pair<double,int>> cand;
        for (uint32_t i = 0; i < nSats; i++)
            cand.push_back({gs_pos_all[g].distance(sat_pos[i]), (int)i});
        std::sort(cand.begin(), cand.end());
        gs_sat[g] = cand[0].second; // nearest satellite (default egress)
        for (int t = 0; t < nAccessLinks; t++)
            links.push_back({nSats + g, (uint32_t)cand[t].second});
    }

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.0.0.0", "255.255.255.252");
    std::vector<Ipv4Address> gsAddr(nGs);
    std::vector<Ipv4Address> satAddr(nSats, Ipv4Address("0.0.0.0"));
    std::map<std::pair<uint32_t,uint32_t>, int> linkIface;
    std::map<std::pair<uint32_t,uint32_t>, Ipv4Address> linkIp;
    for (size_t li = 0; li < links.size(); li++) {
        auto a = links[li].first, b = links[li].second;
        NetDeviceContainer d = p2p.Install(NodeContainer(all.Get(a), all.Get(b)));
        Ipv4InterfaceContainer ic = ipv4.Assign(d);
        linkIface[{a,b}] = ic.Get(0).first->GetInterfaceForDevice(d.Get(0));
        linkIface[{b,a}] = ic.Get(1).first->GetInterfaceForDevice(d.Get(1));
        linkIp[{a,b}] = ic.GetAddress(0);
        linkIp[{b,a}] = ic.GetAddress(1);
        if (a >= nSats) gsAddr[a - nSats] = ic.GetAddress(0);
        if (b >= nSats) gsAddr[b - nSats] = ic.GetAddress(1);
        if (a < nSats && satAddr[a] == Ipv4Address("0.0.0.0")) satAddr[a] = ic.GetAddress(0);
        if (b < nSats && satAddr[b] == Ipv4Address("0.0.0.0")) satAddr[b] = ic.GetAddress(1);
        ipv4.NewNetwork();
    }

    if (protocol == "Dijkstra") Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // --- Fill context ---
    g_ctx.all = all; g_ctx.nSats = nSats; g_ctx.nGs = nGs;
    g_ctx.isl_edges = isl_edges;
    g_ctx.satPos = sat_pos; g_ctx.seed = seed;
    g_ctx.linkIface = linkIface; g_ctx.linkIp = linkIp;
    g_ctx.gsAddr = gsAddr; g_ctx.satAddr = satAddr; g_ctx.gs_sat = gs_sat;
    g_ctx.protocol = protocol;
    g_ctx.pollInterval = 0.5; g_ctx.lastRxBytes = 0;

    // --- CBDP core detection + routes + control traffic ---
    if (protocol == "CBDP") {
        g_ctx.cores = DetectCores(sat_pos, (int)nCores, seed);
        InstallCbdpRoutes(g_ctx.isl_edges);
        ScheduleControl(simTime);
        std::cout << "DIAG CBDP n_cores=" << g_ctx.cores.n_cores
                  << " edges=" << isl_edges.size() << std::endl;
    }

    // ============================================================================
    // Ported static baselines (algorithm-level path functions from
    // common_utils.py, installed as static routes over the real packet plane):
    //   Nearest3 : each GS -> 3 nearest access satellites + shortest-path transit
    //   LPIH     : logic-path-identified hierarchical routing (domain gateways)
    //   PFNSAR   : potential-field anycast (min-potential access + greedy descent)
    // All three use DijkstraSS on the shared weighted adjacency `wadj`.
    // ============================================================================
    if (protocol == "Nearest3" || protocol == "LPIH" || protocol == "PFNSAR") {
        // weighted adjacency list of the ISL graph, built once
        std::vector<std::vector<std::pair<int,double>>> wadj(nSats);
        for (auto& e : isl_edges) {
            wadj[e.a].push_back({e.b, e.dist});
            wadj[e.b].push_back({e.a, e.dist});
        }
        // demand per GS (uniform, matching the all-to-all 1 Mbps flow model)
        std::vector<double> demand(nGs, 1.0);

        if (protocol == "Nearest3") {
            // 3 nearest access satellites per GS
            std::vector<std::vector<int>> access3(nGs);
            for (uint32_t g = 0; g < nGs; g++) {
                std::vector<std::pair<double,int>> cand;
                for (uint32_t i = 0; i < nSats; i++)
                    cand.push_back({gs_pos_all[g].distance(sat_pos[i]), (int)i});
                std::sort(cand.begin(), cand.end());
                int k = std::min(3, (int)nSats);
                for (int t = 0; t < k; t++) access3[g].push_back(cand[t].second);
            }
            // next hop toward each destination egress satellite (single nearest)
            std::vector<std::vector<int>> next_hop(nGs, std::vector<int>(nSats, -1));
            for (uint32_t dst = 0; dst < nGs; dst++) {
                std::vector<double> dist; std::vector<int> par;
                DijkstraSS(wadj, (int)g_ctx.gs_sat[dst], dist, par);
                for (uint32_t i = 0; i < nSats; i++) next_hop[dst][i] = par[i];
            }
            for (uint32_t u = 0; u < all.GetN(); u++) {
                for (uint32_t dst = 0; dst < nGs; dst++) {
                    int next = -1;
                    if (u < nSats) {
                        int cur = (int)u;
                        next = (cur == (int)g_ctx.gs_sat[dst]) ? (int)(nSats + dst)
                                                               : next_hop[dst][cur];
                    } else {
                        uint32_t g = u - nSats;
                        next = access3[g][dst % access3[g].size()]; // round-robin ingress
                    }
                    if (next >= 0 && (int)u != next)
                        AddCbdpRoute(u, g_ctx.gsAddr[dst], (uint32_t)next, u);
                }
            }
            std::cout << "DIAG Nearest3 access_links=" << nAccessLinks << std::endl;
        }

        if (protocol == "LandmarkSR") {
            // Landmark-based skeleton-graph segment routing (Hu et al., 2024,
            // arXiv:2411.19679). Topology-agnostic: partitions the grid into
            // regions, elects one landmark per region, builds an inter-landmark
            // skeleton overlay, and forwards source-landmark -> skeleton ->
            // destination-landmark -> destination. Loop-free because each flow's
            // full end-to-end path is precomputed on the skeleton+SPT segments
            // and installed as per-node static routes along that exact path.
            int N = (int)nSats;
            int R = std::max(2, (int)std::round(std::sqrt((double)N))); // #regions
            std::mt19937 rng(seed);
            std::uniform_int_distribution<int> pick(0, N - 1);
            // k-means region partition (geographic centroids)
            std::vector<Vec3> cent;
            for (int c = 0; c < R; c++) cent.push_back(sat_pos[pick(rng)]);
            std::vector<int> assign(N, 0);
            for (int it = 0; it < 20; it++) {
                for (int i = 0; i < N; i++) {
                    double bd = 1e18; int bi = 0;
                    for (int c = 0; c < R; c++) {
                        double d = sat_pos[i].distance(cent[c]);
                        if (d < bd) { bd = d; bi = c; }
                    }
                    assign[i] = bi;
                }
                std::vector<Vec3> acc(R, Vec3(0,0,0)); std::vector<int> cnt(R, 0);
                for (int i = 0; i < N; i++) {
                    acc[assign[i]].x += sat_pos[i].x;
                    acc[assign[i]].y += sat_pos[i].y;
                    acc[assign[i]].z += sat_pos[i].z; cnt[assign[i]]++;
                }
                for (int c = 0; c < R; c++)
                    if (cnt[c]) { acc[c].x/=cnt[c]; acc[c].y/=cnt[c]; acc[c].z/=cnt[c]; }
                cent = acc;
            }
            // landmark = satellite nearest each region centroid
            std::vector<int> lm(R);
            for (int c = 0; c < R; c++) {
                double bd = 1e18; int bi = 0;
                for (int i = 0; i < N; i++) {
                    double d = sat_pos[i].distance(cent[c]);
                    if (d < bd) { bd = d; bi = i; }
                }
                lm[c] = bi;
            }
            // skeleton overlay: MST + 4-NN over landmarks (physical distance)
            std::vector<std::set<int>> skel(R);
            {
                std::vector<int> in(R, 0); in[0] = 1;
                for (int t = 0; t < R - 1; t++) {
                    double bd = 1e18; int bu = -1, bv = -1;
                    for (int u = 0; u < R; u++) if (in[u])
                        for (int v = 0; v < R; v++) if (!in[v]) {
                            double d = sat_pos[lm[u]].distance(sat_pos[lm[v]]);
                            if (d < bd) { bd = d; bu = u; bv = v; }
                        }
                    if (bu >= 0) { skel[bu].insert(bv); skel[bv].insert(bu); in[bv] = 1; }
                }
            }
            for (int u = 0; u < R; u++) {
                std::vector<std::pair<double,int>> cand;
                for (int v = 0; v < R; v++) if (v != u)
                    cand.push_back({sat_pos[lm[u]].distance(sat_pos[lm[v]]), v});
                std::sort(cand.begin(), cand.end());
                for (int k = 0; k < std::min(4, R - 1); k++) {
                    skel[u].insert(cand[k].second);
                    skel[cand[k].second].insert(u);
                }
            }
            // inter-region landmark sequence via BFS on the skeleton
            auto lmPath = [&](int s, int t) {
                std::vector<int> prev(R, -1); std::vector<int> q = {s};
                std::vector<char> vis(R, 0); vis[s] = 1;
                for (size_t h = 0; h < q.size(); h++)
                    for (int v : skel[q[h]]) if (!vis[v]) {
                        vis[v] = 1; prev[v] = q[h]; q.push_back(v);
                    }
                std::vector<int> path;
                if (!vis[t]) { path.push_back(s); if (s != t) path.push_back(t); return path; }
                for (int cur = t; cur != -1; cur = prev[cur]) path.push_back(cur);
                std::reverse(path.begin(), path.end());
                return path;
            };
            // Precompute SPT (DijkstraSS) toward every landmark and every egress,
            // so each physical segment is a loop-free shortest-path tree branch.
            std::vector<std::vector<int>> par_lm(R, std::vector<int>(N, -1));
            for (int c = 0; c < R; c++) {
                std::vector<double> dist; std::vector<int> par;
                DijkstraSS(wadj, lm[c], dist, par);
                par_lm[c] = par;
            }
            std::vector<std::vector<int>> par_dst(nGs, std::vector<int>(N, -1));
            for (uint32_t dst = 0; dst < nGs; dst++) {
                std::vector<double> dist; std::vector<int> par;
                DijkstraSS(wadj, (int)g_ctx.gs_sat[dst], dist, par);
                par_dst[dst] = par;
            }
            // Build the full per-flow satellite path and install per-node routes.
            // Flow key: source GS g -> destination dst. Satellites on the path get
            // a host route to gsAddr[dst] pointing to the next satellite on path.
            for (uint32_t g = 0; g < nGs; g++) {
                int src_sat = (int)g_ctx.gs_sat[g];
                for (uint32_t dst = 0; dst < nGs; dst++) {
                    if (g == dst) continue;
                    int dst_sat = (int)g_ctx.gs_sat[dst];
                    int dst_reg = assign[dst_sat];
                    // sequence of segment targets: intermediate landmarks then dst_sat
                    std::vector<int> seqRegs = lmPath(assign[src_sat], dst_reg);
                    // materialize the satellite path: src_sat -> lm(r1) -> ... -> dst_sat
                    std::vector<int> satPath;
                    int curNode = src_sat;
                    satPath.push_back(curNode);
                    // hop through each next region's landmark, then final dst_sat
                    std::vector<int> targets;
                    for (size_t k = 1; k < seqRegs.size(); k++) targets.push_back(lm[seqRegs[k]]);
                    targets.push_back(dst_sat);
                    bool ok = true;
                    for (int tgt : targets) {
                        if (tgt == curNode) continue;
                        // walk from curNode toward tgt along the SPT parent chain of tgt
                        std::vector<int> seg;
                        int x = curNode;
                        std::set<int> seen;
                        while (x != tgt) {
                            int p;
                            // choose the parent chain toward this target
                            if (tgt == dst_sat) p = par_dst[dst][x];
                            else {
                                // find region of this target landmark
                                int tr = assign[tgt];
                                p = par_lm[tr][x];
                            }
                            if (p < 0 || seen.count(x)) { ok = false; break; }
                            seen.insert(x);
                            seg.push_back(p);
                            x = p;
                        }
                        if (!ok) break;
                        for (int s : seg) satPath.push_back(s);
                        curNode = tgt;
                    }
                    if (!ok) continue; // skip unreachable flow (should not happen)
                    // install per-node next-hop along satPath
                    for (size_t k = 0; k + 1 < satPath.size(); k++) {
                        uint32_t u = (uint32_t)satPath[k];
                        uint32_t v = (uint32_t)satPath[k + 1];
                        AddCbdpRoute(u, g_ctx.gsAddr[dst], v, u);
                    }
                    // last satellite -> destination GS
                    AddCbdpRoute((uint32_t)dst_sat, g_ctx.gsAddr[dst], nSats + dst, (uint32_t)dst_sat);
                    // source GS -> its access satellite
                    AddCbdpRoute(nSats + g, g_ctx.gsAddr[dst], (uint32_t)src_sat, nSats + g);
                }
            }
            std::cout << "DIAG LandmarkSR regions=" << R << std::endl;
        }
    }

    // --- Control-packet counting hook (all protocols) ---
    Config::Connect("/NodeList/*/$ns3::Ipv4L3Protocol/Tx",
                    MakeCallback(&CtrlTxTrace));

    // --- Traffic: each GS sends one UDP flow to every other GS ---
    std::vector<Ptr<PacketSink>> sinkVec;
    for (uint32_t dst = 0; dst < nGs; dst++) {
        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), DATA_PORT));
        ApplicationContainer sinkApp = sink.Install(gsNodes.Get(dst));
        sinkApp.Start(Seconds(0.0));
        sinkApp.Stop(Seconds(simTime + 1.0));
        sinkVec.push_back(sinkApp.Get(0)->GetObject<PacketSink>());
    }
    g_ctx.sinks = sinkVec;
    std::ostringstream fr; fr << flowRateMbps << "Mbps";
    for (uint32_t src = 0; src < nGs; src++) {
        for (uint32_t dst = 0; dst < nGs; dst++) {
            if (src == dst) continue;
            OnOffHelper onoff("ns3::UdpSocketFactory",
                              InetSocketAddress(gsAddr[dst], DATA_PORT));
            onoff.SetConstantRate(DataRate(fr.str()), 1024); // 1024-byte packets
            ApplicationContainer app = onoff.Install(gsNodes.Get(src));
            app.Start(Seconds(flowStart));
            app.Stop(Seconds(simTime));
        }
    }

    // --- Failure mode scheduling ---
    if (mode == "failure") {
        Simulator::Schedule(Seconds(failTime), &InjectFailures, failFrac, seed, failMode);
        Simulator::Schedule(Seconds(failTime + detectDelay), &RecomputeCbdpRoutes);
        Simulator::Schedule(Seconds(flowStart), &PollGoodput);
    }

    // --- FlowMonitor ---
    FlowMonitorHelper flowmonHelper;
    Ptr<FlowMonitor> flowmon = flowmonHelper.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    // --- Metrics (rxBytes-based; correct packet accounting) ---
    flowmon->CheckForLostPackets();
    std::map<FlowId, FlowMonitor::FlowStats> stats = flowmon->GetFlowStats();
    double totDelay = 0.0, totJitter = 0.0;
    uint64_t totTx = 0, totRx = 0, totLost = 0;
    uint64_t totRxBytes = 0, totTxBytes = 0;
    int nFlows = 0;
    for (auto& kv : stats) {
        auto& s = kv.second;
        if (s.rxPackets > 0) {
            totDelay += s.delaySum.GetSeconds();
            totJitter += s.jitterSum.GetSeconds();
            nFlows++;
        }
        totTx += s.txPackets; totRx += s.rxPackets; totLost += s.lostPackets;
        totRxBytes += s.rxBytes; totTxBytes += s.txBytes;
    }
    double timeWindow = std::max(simTime - flowStart, 1.0);
    double avgDelay = (totRx > 0) ? totDelay / totRx : 0.0;
    double avgJitter = (totRx > 1) ? totJitter / (totRx - 1) : 0.0;
    double throughput = totRxBytes * 8.0 / (timeWindow * 1e6); // Mbps
    double offered = totTxBytes * 8.0 / (timeWindow * 1e6);
    double lossRate = (totTx > 0) ? 100.0 * (double)totLost / (double)totTx : 0.0;

    if (!xmlOut.empty())
        flowmonHelper.SerializeToXmlFile(xmlOut, true, false);

    std::cout << "RESULT "
              << "mode=" << mode
              << " N=" << nSats
              << " proto=" << protocol
              << " seed=" << seed
              << " nCores=" << (protocol == "CBDP" ? (int)nCores : 0)
              << " avgDelay_s=" << std::fixed << std::setprecision(6) << avgDelay
              << " avgJitter_s=" << std::setprecision(6) << avgJitter
              << " throughput_mbps=" << std::setprecision(3) << throughput
              << " offered_mbps=" << std::setprecision(3) << offered
              << " loss_pct=" << std::setprecision(3) << lossRate
              << " nFlows=" << nFlows
              << " txPkts=" << totTx << " rxPkts=" << totRx
              << " ctrlTxPkts=" << g_ctrlTxPkts
              << " ctrlTxBytes=" << g_ctrlTxBytes
              << std::endl;

    Simulator::Destroy();
    return 0;
}
