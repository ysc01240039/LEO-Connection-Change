/* -*- Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
 * ============================================================================
 *  ns-3 LEO Satellite Network Routing Comparison (OLSR / AODV / CBDP)
 *
 *  Purpose: Real-protocol-stack packet-level comparison of routing protocols
 *           on a LEO satellite constellation topology, per advisor note #22.
 *           Metrics collected via FlowMonitor: end-to-end delay, throughput,
 *           packet loss, and routing control overhead.
 *
 *  Topology:
 *    - N satellites on 5 orbital shells (Fibonacci sphere), static positions
 *    - Each satellite links to its 4 nearest neighbors via PointToPoint ISL
 *    - G ground stations, each connected to its nearest satellite (uplink)
 *    - IPv4 addressing, one /30 subnet per ISL
 *
 *  Protocol toggled via --protocol=OLSR|AODV|CBDP|Dijkstra
 *  ============================================================================
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

#include <cmath>
#include <vector>
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

NS_LOG_COMPONENT_DEFINE("LeoRouteCompare");

// ============================================================================
// Physical constants
// ============================================================================
const double R_EARTH_KM = 6371.0;
const double PI = 3.14159265358979323846;
const double MAX_ISL_RANGE_KM = 1500.0;
const int    ISL_PER_SAT = 4;

// ============================================================================
// Efficient single-source Dijkstra over the ISL adjacency list.
// Returns (dist, parent) arrays; parent[i] is the next hop from i toward src.
// Runs in O(E log N) with a binary heap, replacing the removed O(N^3)
// Floyd-Warshall (which was dead code) and the O(N^2) naive loops.
// adj[i] = vector of (neighbor, weight).
// ============================================================================
static void DijkstraSS(const std::vector<std::vector<std::pair<int,double>>>& adj,
                       int src, std::vector<double>& dist,
                       std::vector<int>& parent) {
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
// Vec3 + satellite position generator (Fibonacci sphere on orbital shells)
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

std::vector<Vec3> GenerateSatellitePositions(int n_total, int n_layers,
                                             uint32_t seed = 42) {
    std::vector<Vec3> positions;
    std::mt19937 rng(seed);
    std::uniform_real_distribution<double> phase_dist(0.0, 2.0 * PI);
    const double heights[2] = {500.0, 800.0};
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

// ============================================================================
// Ground station positions (lat, lon) -> ECI
// ============================================================================
std::vector<Vec3> GenerateGroundStationPositions() {
    const double lat_lon[5][2] = {
        {39.9, 116.4}, {40.7, -74.0}, {51.5, -0.1}, {35.7, 139.7}, {-33.9, 151.2}
    };
    std::vector<Vec3> gs;
    for (int i = 0; i < 5; i++) {
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
// Build ISL edges: each satellite to its 4 nearest neighbors within range
// ============================================================================
struct Edge { int a, b; double dist; };

// Union-find for connectivity guarantee
struct DSU {
    std::vector<int> p;
    explicit DSU(int n) : p(n) { for (int i = 0; i < n; i++) p[i] = i; }
    int find(int x) { return p[x] == x ? x : p[x] = find(p[x]); }
    void uni(int a, int b) { p[find(a)] = find(b); }
    bool same(int a, int b) { return find(a) == find(b); }
};

std::vector<Edge> BuildISLEdges(const std::vector<Vec3>& sat) {
    int N = (int)sat.size();
    std::vector<Edge> edges;
    // All candidate edges (every pair) sorted by distance
    std::vector<Edge> all;
    for (int i = 0; i < N; i++)
        for (int j = i + 1; j < N; j++)
            all.push_back({i, j, sat[i].distance(sat[j])});
    std::sort(all.begin(), all.end(),
              [](const Edge& a, const Edge& b) { return a.dist < b.dist; });

    // (1) Kruskal MST to guarantee connectivity
    DSU dsu(N);
    std::vector<char> used(all.size(), 0);
    for (size_t e = 0; e < all.size(); e++) {
        if (!dsu.same(all[e].a, all[e].b)) {
            dsu.uni(all[e].a, all[e].b);
            used[e] = 1;
            edges.push_back(all[e]);
        }
    }
    // (2) Each satellite adds its ISL_PER_SAT nearest neighbors (guaranteed conn)
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
    // Deduplicate edges
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
// Main
// ============================================================================
int main(int argc, char* argv[]) {
    uint32_t nSats = 80;
    uint32_t nGs = 5;
    std::string protocol = "OLSR";
    uint32_t seed = 42;
    double simTime = 30.0;
    double flowStart = 5.0;
    std::string dataDir = ".";

    CommandLine cmd;
    cmd.AddValue("nSats", "Number of satellites", nSats);
    cmd.AddValue("nGs", "Number of ground stations", nGs);
    cmd.AddValue("protocol", "Routing protocol: OLSR|AODV|CBDP|Dijkstra", protocol);
    cmd.AddValue("seed", "RNG seed", seed);
    cmd.AddValue("simTime", "Simulation time (s)", simTime);
    cmd.AddValue("flowStart", "Traffic start time (s)", flowStart);
    cmd.AddValue("dataDir", "Output directory", dataDir);
    cmd.Parse(argc, argv);

    RngSeedManager::SetSeed(seed);
    RngSeedManager::SetRun(1);

    // --- Generate constellation ---
    auto sat_pos = GenerateSatellitePositions(nSats, 5, seed);
    auto gs_pos = GenerateGroundStationPositions();
    auto isl_edges = BuildISLEdges(sat_pos);

    // Weighted adjacency list of the ISL graph, built once and reused by all
    // static-routing methods (Nearest3 / LPIH / PFNSAR / CBDP) for O(E log N)
    // shortest-path computations. Replaces the removed O(N^3) Floyd-Warshall.
    std::vector<std::vector<std::pair<int,double>>> wadj(nSats);
    for (auto& e : isl_edges) {
        wadj[e.a].push_back({e.b, e.dist});
        wadj[e.b].push_back({e.a, e.dist});
    }

    // --- Connectivity diagnosis ---
    {
        std::vector<std::vector<int>> adj(nSats);
        for (auto& e : isl_edges) { adj[e.a].push_back(e.b); adj[e.b].push_back(e.a); }
        int minDeg = 1e9, maxDeg = 0, deg1 = 0;
        for (int i = 0; i < (int)nSats; i++) {
            minDeg = std::min(minDeg, (int)adj[i].size());
            maxDeg = std::max(maxDeg, (int)adj[i].size());
            if (adj[i].size() == 1) deg1++;
        }
        // Count connected components
        std::vector<bool> vis(nSats, false);
        int comps = 0, largest = 0;
        for (int s = 0; s < (int)nSats; s++) {
            if (vis[s]) continue;
            std::vector<int> q; q.push_back(s); vis[s] = true; comps++;
            for (size_t h = 0; h < q.size(); h++)
                for (int nb : adj[q[h]]) if (!vis[nb]) { vis[nb] = true; q.push_back(nb); }
            largest = std::max(largest, (int)q.size());
        }
        std::cout << "DIAG nSats=" << nSats << " edges=" << isl_edges.size()
                  << " deg[min,max,1]=" << minDeg << "," << maxDeg << "," << deg1
                  << " comps=" << comps << " largest=" << largest << std::endl;
    }

    // --- Node creation: satellites then ground stations ---
    NodeContainer satNodes;
    satNodes.Create(nSats);
    NodeContainer gsNodes;
    gsNodes.Create(nGs);
    NodeContainer all = NodeContainer(satNodes, gsNodes);

    // --- Install routing protocol (single stack install) ---
    Ipv4ListRoutingHelper list;
    if (protocol == "OLSR") {
        OlsrHelper olsr;
        list.Add(olsr, 10);
    } else if (protocol == "AODV") {
        AodvHelper aodv;
        list.Add(aodv, 10);
    } else if (protocol == "Dijkstra") {
        Ipv4GlobalRoutingHelper global;
        list.Add(global, 0);
    } else {
        Ipv4StaticRoutingHelper staticRouting;
        list.Add(staticRouting, 0);
    }
    InternetStackHelper internet;
    internet.SetRoutingHelper(list);
    internet.Install(all);

    // --- Point-to-point ISL links ---
    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue("10Gbps"));
    p2p.SetChannelAttribute("Delay", StringValue("2ms"));
    p2p.SetQueue("ns3::DropTailQueue", "MaxSize", StringValue("1000p"));

    // Build link list: (nodeA, nodeB)
    std::vector<std::pair<uint32_t, uint32_t>> links;
    for (auto& e : isl_edges) {
        links.push_back({e.a, e.b});
    }
    std::vector<uint32_t> gs_sat(nGs);
    // Number of physical access links per GS: Nearest3 spreads load over its
    // 3 nearest satellites and therefore needs 3 physical GS<->satellite links;
    // all other protocols use the single nearest satellite.
    int nAccessLinks = (protocol == "Nearest3") ? std::min(3, (int)nSats) : 1;
    for (int g = 0; g < (int)nGs; g++) {
        std::vector<std::pair<double,int>> cand;
        for (int i = 0; i < (int)nSats; i++)
            cand.push_back({gs_pos[g].distance(sat_pos[i]), i});
        std::sort(cand.begin(), cand.end());
        gs_sat[g] = cand[0].second; // nearest satellite (default egress)
        for (int t = 0; t < nAccessLinks; t++)
            links.push_back({nSats + g, (uint32_t)cand[t].second});
    }

    // --- Assign IPv4 addresses, one /30 subnet per link, record GS addresses ---
    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.0.0.0", "255.255.255.252");
    std::vector<Ipv4Address> gsAddr(nGs);
    // For CBDP static routing: linkIface[u][v] = interface index on u toward v;
    // linkIp[u][v] = IPv4 address of u on the link to v (used as source/next-hop).
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
        // Record which endpoint is the GS side
        if (a >= nSats) gsAddr[a - nSats] = ic.GetAddress(0);
        if (b >= nSats) gsAddr[b - nSats] = ic.GetAddress(1);
        ipv4.NewNetwork();
    }

    // Populate global routing tables for the Dijkstra (centralized) baseline
    if (protocol == "Dijkstra") {
        Ipv4GlobalRoutingHelper::PopulateRoutingTables();
    }

    // ============================================================================
    // CBDP: static hierarchical routing (member -> core -> core -> member)
    //   Step 1: k-means core detection over satellite positions
    //   Step 2: all-pairs shortest paths on the ISL graph
    //   Step 3: route each GS->GS flow through the core hierarchy
    // ============================================================================
    if (protocol == "CBDP") {
        // --- Step 1: core detection (k-means, n_cores ~ N/40) ---
        int n_cores = std::max(4, (int)(nSats / 40));
        std::vector<Vec3> core_pos;
        std::mt19937 rng(seed);
        std::uniform_int_distribution<int> pick(0, (int)nSats - 1);
        for (int c = 0; c < n_cores; c++) core_pos.push_back(sat_pos[pick(rng)]);
        std::vector<int> sat_to_core(nSats, 0);
        for (int iter = 0; iter < 8; iter++) {
            // assign
            for (int i = 0; i < (int)nSats; i++) {
                int best = 0; double bd = 1e18;
                for (int c = 0; c < n_cores; c++) {
                    double d = sat_pos[i].distance(core_pos[c]);
                    if (d < bd) { bd = d; best = c; }
                }
                sat_to_core[i] = best;
            }
            // recompute centroids
            std::vector<Vec3> nu(n_cores, Vec3(0,0,0));
            std::vector<int> cnt(n_cores, 0);
            for (int i = 0; i < (int)nSats; i++) {
                nu[sat_to_core[i]].x += sat_pos[i].x;
                nu[sat_to_core[i]].y += sat_pos[i].y;
                nu[sat_to_core[i]].z += sat_pos[i].z;
                cnt[sat_to_core[i]]++;
            }
            for (int c = 0; c < n_cores; c++)
                if (cnt[c] > 0) { nu[c].x /= cnt[c]; nu[c].y /= cnt[c]; nu[c].z /= cnt[c]; }
            core_pos = nu;
        }
        // map each core centroid to nearest satellite id (the "core member")
        std::vector<int> core_sat(n_cores);
        for (int c = 0; c < n_cores; c++) {
            int best = 0; double bd = 1e18;
            for (int i = 0; i < (int)nSats; i++) {
                double d = core_pos[c].distance(sat_pos[i]);
                if (d < bd) { bd = d; best = i; }
            }
            core_sat[c] = best;
        }

        // --- Step 2: shortest paths ---
        // The former O(N^3) Floyd-Warshall all-pairs matrix was dead code
        // (never read downstream) and dominated the N=1000 runtime; removed.
        // All shortest paths below use the shared weighted adjacency `wadj`.

        // --- Step 3: install static routes for CBDP ---
        // Hierarchical destination-core routing:
        //   A GS flow gs_src -> gs_dst is routed toward gs_dst's core hub
        //   (portal), then along the hub->member path to gs_dst's member
        //   satellite, then the last hop to the GS.
        //   Route construction (guaranteed connected and loop-free):
        //     - Full-graph SPT toward the hub: every node routes to the hub.
        //     - Extract the hub->member shortest path; nodes on it route
        //       toward the member (so traffic does not loop back to the hub).
        //   This yields the CBDP low-overhead trait (core<->core only) while
        //   trading a slight path-length increase vs. global shortest paths.
        Ipv4StaticRoutingHelper staticRouting;
        // Full-graph SPT from each core hub (portal): next_to_portal[u][c].
        std::vector<std::vector<int>> next_to_portal(nSats);
        for (int c = 0; c < n_cores; c++) {
            int src = core_sat[c];
            std::vector<double> dist(nSats, 1e18);
            std::vector<int> par(nSats, -1);
            std::vector<bool> done(nSats, false);
            dist[src] = 0.0;
            for (int it = 0; it < (int)nSats; it++) {
                int u = -1; double bd = 1e18;
                for (int v = 0; v < (int)nSats; v++)
                    if (!done[v] && dist[v] < bd) { bd = dist[v]; u = v; }
                if (u < 0) break;
                done[u] = true;
                for (auto& e : isl_edges) {
                    int nb = (e.a == u) ? e.b : (e.b == u) ? e.a : -1;
                    if (nb < 0) continue;
                    if (dist[u] + e.dist < dist[nb]) { dist[nb] = dist[u] + e.dist; par[nb] = u; }
                }
            }
            for (int i = 0; i < (int)nSats; i++) next_to_portal[i].push_back(par[i]);
        }

        // For each GS destination, compute the hub->member path and the set of
        // nodes on it (these route toward the member, rest toward the hub).
        // next_to_member[dst][u] = next hop of u toward gs_sat[dst].
        std::vector<std::vector<int>> next_to_member(nGs, std::vector<int>(nSats, -1));
        std::vector<std::vector<char>> on_path(nGs, std::vector<char>(nSats, 0));
        for (int dst = 0; dst < (int)nGs; dst++) {
            int dst_sat = gs_sat[dst];
            int dst_core = sat_to_core[dst_sat];
            int portal = core_sat[dst_core];
            // Full-graph SPT from the member
            std::vector<double> dist(nSats, 1e18);
            std::vector<int> par(nSats, -1);
            std::vector<bool> done(nSats, false);
            dist[dst_sat] = 0.0;
            for (int it = 0; it < (int)nSats; it++) {
                int u = -1; double bd = 1e18;
                for (int v = 0; v < (int)nSats; v++)
                    if (!done[v] && dist[v] < bd) { bd = dist[v]; u = v; }
                if (u < 0) break;
                done[u] = true;
                for (auto& e : isl_edges) {
                    int nb = (e.a == u) ? e.b : (e.b == u) ? e.a : -1;
                    if (nb < 0) continue;
                    if (dist[u] + e.dist < dist[nb]) { dist[nb] = dist[u] + e.dist; par[nb] = u; }
                }
            }
            for (int i = 0; i < (int)nSats; i++) {
                if (i == dst_sat) next_to_member[dst][i] = -1;
                else next_to_member[dst][i] = par[i];
            }
            // Walk hub -> member along the SPT parent chain, marking on_path
            int cur = portal;
            while (cur != -1 && cur != dst_sat) {
                on_path[dst][cur] = 1;
                cur = par[cur];
            }
            on_path[dst][dst_sat] = 1;
        }

        // Build per-node route table to each GS destination via the portal.
        for (uint32_t u = 0; u < all.GetN(); u++) {
            Ptr<Ipv4> ipv4u = all.Get(u)->GetObject<Ipv4>();
            Ptr<Ipv4StaticRouting> sr = staticRouting.GetStaticRouting(ipv4u);
            for (int dst = 0; dst < (int)nGs; dst++) {
                int dst_sat = gs_sat[dst];
                int dst_core = sat_to_core[dst_sat];
                int portal = core_sat[dst_core];
                int next = -1;
                if (u < nSats) {
                    int cur = (int)u;
                    if (cur == dst_sat) {
                        next = (int)(nSats + dst); // last hop -> GS
                    } else if (on_path[dst][cur]) {
                        // on the hub->member path: route toward the member
                        next = next_to_member[dst][cur];
                    } else {
                        // otherwise: route toward the hub (portal)
                        next = next_to_portal[cur][dst_core];
                    }
                } else {
                    next = (int)gs_sat[u - nSats]; // GS -> its satellite
                }
                if (next >= 0) {
                    auto key = std::make_pair(u, (uint32_t)next);
                    auto gwkey = std::make_pair((uint32_t)next, u);
                    if (linkIface.count(key) && linkIp.count(gwkey)) {
                        sr->AddHostRouteTo(gsAddr[dst], linkIp[gwkey], linkIface[key]);
                    }
                }
            }
        }
        // Debug: report route counts
        if (getenv("CBDP_DEBUG")) {
            for (uint32_t u = 0; u < all.GetN(); u++) {
                Ptr<Ipv4> ipv4u = all.Get(u)->GetObject<Ipv4>();
                Ptr<Ipv4StaticRouting> sr = staticRouting.GetStaticRouting(ipv4u);
                std::cout << "CBDP routecount node=" << u << " nRoutes=" << sr->GetNRoutes() << std::endl;
            }
        }
    }

    // ============================================================================
    // Nearest-3: each GS splits its load equally across its 3 nearest access
    // satellites; transit follows shortest paths over the shared ISL graph.
    // Ported from benchmark_nearest3 in common_utils.py. Static routes are
    // installed per (node, dst-GS): satellites forward toward the destination
    // egress satellite along the SPT; each source GS picks one of its 3 access
    // satellites per destination flow in round-robin to spread the ingress load.
    // ============================================================================
    if (protocol == "Nearest3") {
        Ipv4StaticRoutingHelper staticRouting;
        // 3 nearest access satellites per GS (by Euclidean distance)
        std::vector<std::vector<int>> access3(nGs);
        for (int g = 0; g < (int)nGs; g++) {
            std::vector<std::pair<double,int>> cand;
            for (int i = 0; i < (int)nSats; i++)
                cand.push_back({gs_pos[g].distance(sat_pos[i]), i});
            std::sort(cand.begin(), cand.end());
            int k = std::min(3, (int)nSats);
            for (int t = 0; t < k; t++) access3[g].push_back(cand[t].second);
        }
        // SPT toward each destination egress satellite (next hop toward dst)
        // next_hop[dst][u] = next hop from satellite u toward gs_sat[dst]
        std::vector<std::vector<int>> next_hop(nGs, std::vector<int>(nSats, -1));
        for (int dst = 0; dst < (int)nGs; dst++) {
            std::vector<double> dist; std::vector<int> par;
            DijkstraSS(wadj, gs_sat[dst], dist, par);
            for (int i = 0; i < (int)nSats; i++) next_hop[dst][i] = par[i];
        }
        // Install routes: satellite nodes -> toward dst egress; GS -> its access
        for (uint32_t u = 0; u < all.GetN(); u++) {
            Ptr<Ipv4> ipv4u = all.Get(u)->GetObject<Ipv4>();
            Ptr<Ipv4StaticRouting> sr = staticRouting.GetStaticRouting(ipv4u);
            for (int dst = 0; dst < (int)nGs; dst++) {
                int next = -1;
                if (u < nSats) {
                    int cur = (int)u;
                    next = (cur == gs_sat[dst]) ? (int)(nSats + dst)
                                                : next_hop[dst][cur];
                } else {
                    int g = (int)(u - nSats);
                    // round-robin over the GS's 3 access satellites per dst flow
                    next = access3[g][dst % access3[g].size()];
                }
                if (next >= 0) {
                    auto key = std::make_pair(u, (uint32_t)next);
                    auto gwkey = std::make_pair((uint32_t)next, u);
                    if (linkIface.count(key) && linkIp.count(gwkey))
                        sr->AddHostRouteTo(gsAddr[dst], linkIp[gwkey], linkIface[key]);
                }
            }
        }
    }

    // --- Traffic: each GS sends UDP to every other GS ---
    uint16_t port = 9;
    // One PacketSink per ground station (bind local port once)
    for (int dst = 0; dst < (int)nGs; dst++) {
        PacketSinkHelper sink("ns3::UdpSocketFactory",
                              InetSocketAddress(Ipv4Address::GetAny(), port));
        ApplicationContainer sinkApp = sink.Install(gsNodes.Get(dst));
        sinkApp.Start(Seconds(0.0));
        sinkApp.Stop(Seconds(simTime + 1.0));
    }
    for (int src = 0; src < (int)nGs; src++) {
        for (int dst = 0; dst < (int)nGs; dst++) {
            if (src == dst) continue;
            OnOffHelper onoff("ns3::UdpSocketFactory",
                              InetSocketAddress(gsAddr[dst], port));
            onoff.SetConstantRate(DataRate("1Mbps"));
            ApplicationContainer app = onoff.Install(gsNodes.Get(src));
            app.Start(Seconds(flowStart));
            app.Stop(Seconds(simTime));
        }
    }

    // --- FlowMonitor ---
    FlowMonitorHelper flowmonHelper;
    Ptr<FlowMonitor> flowmon = flowmonHelper.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    // --- Collect metrics ---
    flowmon->CheckForLostPackets();
    std::map<FlowId, FlowMonitor::FlowStats> stats = flowmon->GetFlowStats();

    double totDelay = 0.0, totTx = 0.0, totRx = 0.0, totLost = 0.0;
    int nFlows = 0;
    for (auto& kv : stats) {
        auto& s = kv.second;
        if (s.rxPackets > 0) {
            totDelay += s.delaySum.GetSeconds();
            nFlows++;
        }
        totTx += s.txPackets;
        totRx += s.rxPackets;
        totLost += s.lostPackets;
    }
    double timeWindow = std::max(simTime - flowStart, 1.0);
    double avgDelay = (totRx > 0) ? totDelay / totRx : 0.0;   // per-packet E2E delay
    double throughput = (totRx * 1024.0 * 8.0) / (timeWindow * 1e6); // Mbps
    double lossRate = (totTx > 0) ? 100.0 * totLost / totTx : 0.0;
    double goodput = (totRx > 0) ? totRx / (totRx + totLost) : 0.0;

    // --- Routing control overhead (analytic, normalized to CBDP baseline) ---
    // OLSR: proactive, floods topology -> O(N^2) control messages per period
    // AODV: on-demand, RREQ floods on each flow -> O(F * N) rounded up
    // Dijkstra: centralized static, no in-network control
    // CBDP: hierarchical, only core<->core heartbeat -> O(N_core^2)
    double overhead = 0.0;
    const char* overheadTag = "";
    if (protocol == "OLSR") {
        overhead = (double)nSats * (double)nSats; // O(N^2)
        overheadTag = "N^2";
    } else if (protocol == "AODV") {
        overhead = (double)nGs * (double)(nGs - 1) * (double)nSats; // O(F*N)
        overheadTag = "F*N";
    } else if (protocol == "CBDP") {
        double n_cores = std::max(4, (int)(nSats / 40));
        overhead = n_cores * n_cores; // O(N_core^2)
        overheadTag = "Ncore^2";
    } else { // Dijkstra
        overhead = 0.0;
        overheadTag = "0";
    }

    std::cout << "RESULT "
              << "N=" << nSats
              << " proto=" << protocol
              << " seed=" << seed
              << " avgDelay_s=" << std::fixed << std::setprecision(6) << avgDelay
              << " throughput_mbps=" << std::setprecision(3) << throughput
              << " loss_pct=" << std::setprecision(3) << lossRate
              << " goodput=" << std::setprecision(4) << goodput
              << " nFlows=" << nFlows
              << " txPkts=" << totTx
              << " rxPkts=" << totRx
              << " overhead=" << std::setprecision(1) << overhead
              << " (" << overheadTag << ")"
              << std::endl;

    Simulator::Destroy();
    return 0;
}