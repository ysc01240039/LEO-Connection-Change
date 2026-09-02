"""审计脚本1：独立复算两轨指标，分解时延，验证结构性疑点。"""
import csv, math, statistics as st
from collections import Counter


def load(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


for tag, path in [("Python轨", "data/sim/access_trace.csv"),
                  ("ns-3轨", "data/sim/ns3_out/access_trace.csv")]:
    tr = load(path)
    acc = [e for e in tr if e["event_type"] == "ACCESS"]
    ho = [e for e in tr if e["event_type"] == "HANDOVER"]
    leg = [e for e in acc if e["forged"] == "0"]
    forg = [e for e in acc if e["forged"] == "1"]
    print(f"\n{'='*64}\n{tag}  {path}   总事件={len(tr)}")
    print(f"  ACCESS={len(acc)} (合法{len(leg)} 伪造{len(forg)})   HANDOVER={len(ho)}")
    fblock = sum(1 for e in forg if e["result"] == "fail")
    print(f"  伪造: {len(forg)} 拦截{fblock} -> 拦截率={fblock/max(1,len(forg)):.4f}  放行={len(forg)-fblock}")
    print(f"  合法 result分布: {dict(Counter(e['result'] for e in leg))}")

    inter = [float(e["value_ms"]) for e in ho]
    pp = [int(e["pingpong"]) for e in ho]
    mm = [int(e["predict_mismatch"]) for e in ho]
    elc = [float(e["ho_el_cost_deg"]) for e in ho]
    print(f"  切换中断: 非零 {sum(1 for x in inter if x>0)}/{len(inter)}  max={max(inter) if inter else 0}")
    print(f"  乒乓标记: {sum(pp)}/{len(pp)} = {sum(pp)/max(1,len(pp)):.4f}")
    print(f"  失配标记: {sum(mm)}/{len(mm)} = {sum(mm)/max(1,len(mm)):.4f}")
    print(f"  仰角代价: 均值{st.mean(elc):.2f}° 中位{st.median(elc):.2f}° max{max(elc):.2f}° "
          f"零占比{sum(1 for x in elc if x==0)/max(1,len(elc)):.4f}")

    d = [float(e["value_ms"]) for e in leg if float(e["value_ms"]) > 0]
    sla = [float(e["slant_km"]) for e in leg if float(e["slant_km"]) > 0]
    if d and sla:
        rt = 2 * st.median(sla) / 299792.458 * 1000
        print(f"  接入时延: 均值{st.mean(d):.2f} min{min(d):.2f} max{max(d):.2f}")
        print(f"  斜距中位{st.median(sla):.0f}km -> 2x传播={rt:.2f}ms (+处理3+认证1) 纯握手≈{rt+4:.2f}ms")
        print(f"  => 等待/退避贡献 ≈ {st.mean(d)-(rt+4):.2f}ms")

    # 选中卫星的实际仰角（由斜距反解，LEO 1200km 假设）
    r_e, h = 6371.0, 1200.0
    r_s = r_e + h
    els = []
    for e in ho:
        dd = float(e["slant_km"])
        if dd <= 0:
            continue
        s = (r_s**2 - r_e**2 - dd**2) / (2 * r_e * dd)
        if -1 <= s <= 1:
            els.append(math.degrees(math.asin(s)))
    if els:
        print(f"  切换目标星仰角(由斜距反解): 均值{st.mean(els):.1f}° 中位{st.median(els):.1f}° "
              f"min{min(els):.1f}°  <30°占比{sum(1 for x in els if x<30)/len(els):.2%}")
        print(f"  => 目标星仰角 + 仰角代价均值 = 仰角最优星 ≈ {st.mean(els)+st.mean(elc):.1f}°")
