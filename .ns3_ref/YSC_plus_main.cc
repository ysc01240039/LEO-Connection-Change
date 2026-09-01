#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/mobility-module.h"
#include "ns3/applications-module.h"
#include "ns3/config-store-module.h"
#include "ns3/flow-monitor-module.h"

#include "model/constellation/constellation-helper.h"
#include "model/task/task-generator.h"
#include "algorithm/scheduler-base.h"
#include "algorithm/centralized/centralized-scheduler.h"
#include "algorithm/random/random-scheduler.h"
#include "algorithm/cnp/cnp-scheduler.h"
#include "algorithm/drl/drl-scheduler.h"
#include "algorithm/gnn/gnn-scheduler.h"
#include "algorithm/ysc/ysc-scheduler.h"
#include "scenario/scenario-base.h"
#include "scenario/steady-state-scenario.h"
#include "scenario/burst-scenario.h"
#include "scenario/high-dynamic-scenario.h"
#include "scenario/fault-recovery-scenario.h"
#include "scenario/cross-region-scenario.h"
#include "scenario/load-balance-scenario.h"
#include "scenario/scalability-scenario.h"
#include "metrics/metrics-collector.h"

#include <fstream>
#include <iostream>
#include <filesystem>
#include <unistd.h>

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("YSC_plus");

enum SchedulerType {
    CENTRALIZED = 0,
    RANDOM = 1,
    CNP = 2,
    DRL = 3,
    GNN = 4,
    YSC = 5
};

enum ScenarioType {
    STEADY_STATE = 0,
    BURST = 1,
    HIGH_DYNAMIC = 2,
    FAULT_RECOVERY = 3,
    CROSS_REGION = 4,
    LOAD_BALANCE = 5,
    SCALABILITY = 6
};

// Overload operator>> for SchedulerType
std::istream& operator>>(std::istream& is, SchedulerType& type) {
    int value;
    is >> value;
    type = static_cast<SchedulerType>(value);
    return is;
}

// Overload operator>> for ScenarioType
std::istream& operator>>(std::istream& is, ScenarioType& type) {
    int value;
    is >> value;
    type = static_cast<ScenarioType>(value);
    return is;
}

std::string SchedulerTypeToString(SchedulerType type) {
    switch (type) {
        case CENTRALIZED: return "Centralized";
        case RANDOM: return "Random";
        case CNP: return "CNP";
        case DRL: return "DRL";
        case GNN: return "GNN";
        case YSC: return "YSC";
        default: return "Unknown";
    }
}

std::string ScenarioTypeToString(ScenarioType type) {
    switch (type) {
        case STEADY_STATE: return "SteadyState";
        case BURST: return "Burst";
        case HIGH_DYNAMIC: return "HighDynamic";
        case FAULT_RECOVERY: return "FaultRecovery";
        case CROSS_REGION: return "CrossRegion";
        case LOAD_BALANCE: return "LoadBalance";
        case SCALABILITY: return "Scalability";
        default: return "Unknown";
    }
}

struct SimulationConfig {
    SchedulerType scheduler;
    ScenarioType scenario;
    uint32_t leoCount;
    uint32_t meoCount;
    uint32_t geoCount;
    double leoAltitude;
    double meoAltitude;
    double geoAltitude;
    uint32_t leoPlanes;
    uint32_t leoSatsPerPlane;
    uint32_t meoPlanes;
    uint32_t meoSatsPerPlane;
    double leoInclination;
    double meoInclination;
    double taskArrivalRate;
    double burstTaskArrivalRate;
    Time simulationTime;
    uint32_t randomSeed;
    bool enableYSCCache;
    bool enableYSCDynamicProb;
    bool enableYSCPrefetch;
    bool enableYSCAsyncLearning;
    bool enableYSCCrossRegion;
    uint32_t candidateCountK;
    Time cacheTTL;
    Time windowSizeW;
    double lambda1;
    std::string outputDir;
};

SimulationConfig ParseCommandLine(int argc, char* argv[]) {
    SimulationConfig config;

    config.scheduler = YSC;
    config.scenario = STEADY_STATE;
    config.leoCount = 1008;
    config.meoCount = 12;
    config.geoCount = 2;
    config.leoAltitude = 550.0;
    config.meoAltitude = 20000.0;
    config.geoAltitude = 35786.0;
    config.leoPlanes = 24;
    config.leoSatsPerPlane = 42;
    config.meoPlanes = 3;
    config.meoSatsPerPlane = 4;
    config.leoInclination = 53.0;
    config.meoInclination = 55.0;
    config.taskArrivalRate = 10.0;
    config.burstTaskArrivalRate = 100.0;
    config.simulationTime = Hours(24);
    config.randomSeed = 1;
    config.enableYSCCache = true;
    config.enableYSCDynamicProb = true;
    config.enableYSCPrefetch = true;
    config.enableYSCAsyncLearning = true;
    config.enableYSCCrossRegion = true;
    config.candidateCountK = 5;
    config.cacheTTL = Seconds(300);
    config.windowSizeW = Seconds(60);
    config.lambda1 = 0.7;
    config.outputDir = "results";

    CommandLine cmd;
    cmd.AddValue("scheduler", "Scheduler type (0=Centralized, 1=Random, 2=CNP, 3=DRL, 4=GNN, 5=YSC)", config.scheduler);
    cmd.AddValue("scenario", "Scenario type (0=SteadyState, 1=Burst, 2=HighDynamic, 3=FaultRecovery, 4=CrossRegion, 5=LoadBalance, 6=Scalability)", config.scenario);
    cmd.AddValue("leoCount", "Number of LEO satellites", config.leoCount);
    cmd.AddValue("meoCount", "Number of MEO satellites", config.meoCount);
    cmd.AddValue("geoCount", "Number of GEO satellites", config.geoCount);
    cmd.AddValue("simulationTime", "Simulation time in seconds", config.simulationTime);
    cmd.AddValue("randomSeed", "Random seed", config.randomSeed);
    cmd.AddValue("enableYSCCache", "Enable YSC cache", config.enableYSCCache);
    cmd.AddValue("enableYSCDynamicProb", "Enable YSC dynamic probability", config.enableYSCDynamicProb);
    cmd.AddValue("enableYSCPrefetch", "Enable YSC predictive prefetch", config.enableYSCPrefetch);
    cmd.AddValue("enableYSCAsyncLearning", "Enable YSC async learning", config.enableYSCAsyncLearning);
    cmd.AddValue("enableYSCCrossRegion", "Enable YSC cross-region coordination", config.enableYSCCrossRegion);
    cmd.AddValue("candidateCountK", "Candidate count K for YSC", config.candidateCountK);
    cmd.AddValue("outputDir", "Output directory", config.outputDir);
    cmd.Parse(argc, argv);

    return config;
}

Ptr<SchedulerBase> CreateScheduler(const SimulationConfig& config) {
    switch (config.scheduler) {
        case CENTRALIZED:
            return CreateObject<CentralizedScheduler>();
        case RANDOM:
            return CreateObject<RandomScheduler>();
        case CNP:
            return CreateObject<CNPScheduler>();
        case DRL:
            return CreateObject<DRLScheduler>();
        case GNN:
            return CreateObject<GNNScheduler>();
        case YSC:
            return CreateObject<YSCScheduler>(config.enableYSCCache,
                                              config.enableYSCDynamicProb,
                                              config.enableYSCPrefetch,
                                              config.enableYSCAsyncLearning,
                                              config.enableYSCCrossRegion,
                                              config.candidateCountK,
                                              config.cacheTTL,
                                              config.windowSizeW,
                                              config.lambda1);
        default:
            return CreateObject<YSCScheduler>(config.enableYSCCache,
                                              config.enableYSCDynamicProb,
                                              config.enableYSCPrefetch,
                                              config.enableYSCAsyncLearning,
                                              config.enableYSCCrossRegion,
                                              config.candidateCountK,
                                              config.cacheTTL,
                                              config.windowSizeW,
                                              config.lambda1);
    }
}

Ptr<ScenarioBase> CreateScenario(const SimulationConfig& config) {
    switch (config.scenario) {
        case STEADY_STATE:
            return CreateObject<SteadyStateScenario>(config.taskArrivalRate);
        case BURST:
            return CreateObject<BurstScenario>(config.taskArrivalRate, config.burstTaskArrivalRate);
        case HIGH_DYNAMIC:
            return CreateObject<HighDynamicScenario>(config.taskArrivalRate);
        case FAULT_RECOVERY:
            return CreateObject<FaultRecoveryScenario>(config.taskArrivalRate);
        case CROSS_REGION:
            return CreateObject<CrossRegionScenario>(config.taskArrivalRate);
        case LOAD_BALANCE:
            return CreateObject<LoadBalanceScenario>(config.taskArrivalRate);
        case SCALABILITY:
            return CreateObject<ScalabilityScenario>(config.taskArrivalRate);
        default:
            return CreateObject<SteadyStateScenario>(config.taskArrivalRate);
    }
}

int main(int argc, char* argv[]) {
    // 1. 获取当前工作目录
    char cwd[1024];
    if (getcwd(cwd, sizeof(cwd)) != nullptr) {
        NS_LOG_UNCOND("Current working directory: " << cwd);
    } else {
        NS_LOG_UNCOND("Failed to get current working directory");
    }

    // 2. 解析命令行参数
    SimulationConfig config = ParseCommandLine(argc, argv);

    // 3. 创建输出目录
    try {
        std::filesystem::create_directories(config.outputDir);
        NS_LOG_UNCOND("Output directory '" << config.outputDir << "' created/verified");
    } catch (const std::exception& e) {
        NS_LOG_UNCOND("Error creating output directory: " << e.what());
    }

    // 4. 打开调试日志文件
    std::string logPath = config.outputDir + "/debug.log";
    std::ofstream debugFile(logPath);
    if (!debugFile.is_open()) {
        NS_LOG_UNCOND("ERROR: Failed to open debug.log at " << logPath);
        logPath = "/tmp/debug.log";
        debugFile.open(logPath);
        if (!debugFile.is_open()) {
            NS_LOG_UNCOND("ERROR: Also failed to open /tmp/debug.log");
        } else {
            NS_LOG_UNCOND("Using fallback debug log: " << logPath);
        }
    } else {
        NS_LOG_UNCOND("Debug log will be written to: " << logPath);
    }

    // 5. 重定向 stderr 到文件
    if (debugFile.is_open()) {
        std::streambuf* old_stderr = std::cerr.rdbuf(debugFile.rdbuf());
        debugFile << std::unitbuf;
    }

    // 6. 启用日志组件
    LogComponentEnable("YSC_plus", LOG_LEVEL_INFO);
    LogComponentEnable("ConstellationHelper", LOG_LEVEL_INFO);
    LogComponentEnable("TaskGenerator", LOG_LEVEL_INFO);
    LogComponentEnable("MetricsCollector", LOG_LEVEL_INFO);
    LogComponentEnable("YSCScheduler", LOG_LEVEL_INFO);
    LogComponentEnable("CacheManager", LOG_LEVEL_INFO);

    RngSeedManager::SetSeed(config.randomSeed);

    NS_LOG_UNCOND("=== YSC_plus Satellite Network Task Scheduling Simulation ===");
    NS_LOG_UNCOND("Scheduler: " << SchedulerTypeToString(config.scheduler));
    NS_LOG_UNCOND("Scenario: " << ScenarioTypeToString(config.scenario));
    NS_LOG_UNCOND("LEO Satellites: " << config.leoCount);
    NS_LOG_UNCOND("MEO Satellites: " << config.meoCount);
    NS_LOG_UNCOND("GEO Satellites: " << config.geoCount);
    NS_LOG_UNCOND("Simulation Time: " << config.simulationTime.GetSeconds() << " seconds");
    NS_LOG_UNCOND("Random Seed: " << config.randomSeed);

    NodeContainer leoNodes;
    NodeContainer meoNodes;
    NodeContainer geoNodes;

    Ptr<ConstellationHelper> constellationHelper = CreateObject<ConstellationHelper>();
    constellationHelper->ConfigureLEO(config.leoCount, config.leoAltitude * 1000,
                                       config.leoPlanes, config.leoSatsPerPlane,
                                       config.leoInclination);
    constellationHelper->ConfigureMEO(config.meoCount, config.meoAltitude * 1000,
                                       config.meoPlanes, config.meoSatsPerPlane,
                                       config.meoInclination);
    constellationHelper->ConfigureGEO(config.geoCount, config.geoAltitude * 1000);

    leoNodes = constellationHelper->CreateLEONodes();
    meoNodes = constellationHelper->CreateMEONodes();
    geoNodes = constellationHelper->CreateGEONodes();

    NS_LOG_UNCOND("Created " << leoNodes.GetN() << " LEO nodes");
    NS_LOG_UNCOND("Created " << meoNodes.GetN() << " MEO nodes");
    NS_LOG_UNCOND("Created " << geoNodes.GetN() << " GEO nodes");

    Ptr<SchedulerBase> scheduler = CreateScheduler(config);
    scheduler->SetConstellation(constellationHelper->GetConstellation());

    // 诊断输出
    NS_LOG_UNCOND("Actual scheduler: " << scheduler->GetSchedulerName());
    if (config.scheduler == YSC) {
        Ptr<YSCScheduler> yscScheduler = DynamicCast<YSCScheduler>(scheduler);
        if (yscScheduler) {
            NS_LOG_UNCOND("YSC components: Cache=" << yscScheduler->GetEnableCache()
                          << " DynamicProb=" << yscScheduler->GetEnableDynamicProb()
                          << " Prefetch=" << yscScheduler->GetEnablePrefetch()
                          << " AsyncLearning=" << yscScheduler->GetEnableAsyncLearning()
                          << " CrossRegion=" << yscScheduler->GetEnableCrossRegion());
        } else {
            NS_LOG_UNCOND("WARNING: YSC scheduler requested but dynamic cast failed!");
        }
    }

    Ptr<ScenarioBase> scenario = CreateScenario(config);

    if (config.scheduler == YSC && config.scenario == BURST) {
        Ptr<YSCScheduler> yscScheduler = DynamicCast<YSCScheduler>(scheduler);
        Ptr<BurstScenario> burstScenario = DynamicCast<BurstScenario>(scenario);
        if (yscScheduler && burstScenario) {
            yscScheduler->SetBurstScenario(burstScenario);
        }
    }

    Ptr<TaskGenerator> taskGenerator = CreateObject<TaskGenerator>();
    taskGenerator->SetScheduler(scheduler);
    taskGenerator->SetConstellation(constellationHelper->GetConstellation());
    taskGenerator->SetScenario(scenario);

    Ptr<MetricsCollector> metricsCollector = CreateObject<MetricsCollector>();
    metricsCollector->SetOutputDirectory(config.outputDir);
    scheduler->SetMetricsCollector(metricsCollector);
    taskGenerator->SetMetricsCollector(metricsCollector);

    NS_LOG_UNCOND("MetricsCollector set to scheduler and taskGenerator");

    if (leoNodes.GetN() > 0) {
        leoNodes.Get(0)->AddApplication(taskGenerator);
    }

    taskGenerator->SetStartTime(Seconds(0));
    taskGenerator->SetStopTime(config.simulationTime);

    Simulator::Stop(config.simulationTime);

    NS_LOG_UNCOND("Starting simulation...");
    Simulator::Run();

    NS_LOG_UNCOND("Simulation completed.");
    NS_LOG_UNCOND("Collecting metrics...");

    metricsCollector->CollectFinalMetrics();
    metricsCollector->PrintSummary();
    metricsCollector->SaveResults();

    NS_LOG_UNCOND("Before Simulator::Destroy()");
    Simulator::Destroy();
    NS_LOG_UNCOND("After Simulator::Destroy()");

    NS_LOG_UNCOND("=== Simulation Finished ===");

    // 强制退出以避免析构顺序问题导致的段错误
    exit(0);
}