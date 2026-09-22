"""多类 guard-channel 最优阈值求解（Kaufman-Roberts 生灭过程精确解）。

★ 科学版生存优先调度核心（替代静态 PRIORITY_RESERVE_FRAC）★

问题建模：每个 (星, 10ms 时隙) RACH 池容量 c，三档到达率 A=(A_h, A_m, A_l)
（Erlang；单位时隙内平均尝试数，业务驻留=1 时隙 → 即损失系统 loss system）。
接纳策略 = 标准多类 guard-channel：
    high 准入 iff 占用 b < c
    med  准入 iff 占用 b < c - g_h        （g_h 为高危预留 guard）
    low  准入 iff 占用 b < c - g_h - g_m   （g_m 为中危在其之上的预留）
阻塞概率由 1D 生灭链精确给出（Kaufman 1981 / Roberts 1981 的 Kaufman-Roberts 递推）：
    π(b) = π(0) · Π_{k=0}^{b-1} birth(k)/death(k+1)，death(k)=k
    birth(k) = A_h·1[k<c] + A_m·1[k<c-g_h] + A_l·1[k<c-g_h-g_m]
    B_h = π(c)；B_m = Σ_{b≥c-g_h} π(b)；B_l = Σ_{b≥c-g_h-g_m} π(b)

最优阈值 (g_h*, g_m*) = argmin_{g} (w_m·B_m + w_l·B_l)  s.t. B_h ≤ ε
（无可行解时退化为 argmin B_h，即最大化高危保护）。

性质：
- 精确（闭式递推，非近似/非启发式）；
- 在线可解：O(c³) 每窗口（c≤64），星上即「每窗口重算」亦仅万级运算；
- 齐次性：最优阈值分数只依赖归一化负载，故结果可离线预存为查表（本实现用 lru_cache 等价）。
"""

from __future__ import annotations
import functools

__all__ = ["birth_death_pi", "blocking", "optimal_guards"]


def birth_death_pi(c: int, Ah: float, Am: float, Al: float, gh: int, gm: int):
    """返回稳态占用分布 π[0..c]（已归一化）。O(c)。

    birth/death 率见模块 docstring；状态转移 ±1 → 生灭链，递推即精确解。
    """
    pi = [0.0] * (c + 1)
    pi[0] = 1.0
    c_h = c - gh          # med 准入上限（占用 < c_h）
    c_l = c - gh - gm     # low  准入上限（占用 < c_l）
    for b in range(0, c):
        birth = 0.0
        if b < c:
            birth += Ah
        if b < c_h:
            birth += Am
        if b < c_l:
            birth += Al
        death = b + 1.0
        pi[b + 1] = pi[b] * (birth / death) if death > 0 else 0.0
    s = sum(pi)
    if s <= 0.0:
        pi[0] = 1.0
        return pi
    for i in range(c + 1):
        pi[i] /= s
    return pi


def blocking(c: int, Ah: float, Am: float, Al: float, gh: int, gm: int):
    """返回 (B_h, B_m, B_l, pi)。"""
    pi = birth_death_pi(c, Ah, Am, Al, gh, gm)
    bh = pi[c]
    c_h = c - gh
    c_l = c - gh - gm
    bm = sum(pi[b] for b in range(c_h, c + 1)) if c_h >= 0 else sum(pi)
    bl = sum(pi[b] for b in range(c_l, c + 1)) if c_l >= 0 else sum(pi)
    return bh, bm, bl, pi


@functools.lru_cache(maxsize=8192)
def optimal_guards(c: int, Ah: float, Am: float, Al: float,
                   wm: float = 1.0, wl: float = 1.0, eps: float = 0.10):
    """给定容量 c 与三档到达率（Erlang），返回最优 (g_h, g_m) 及其 (B_h,B_m,B_l)。

    参数取整后缓存（lru_cache），同一负载形状只解一次 → 仿真中近乎 O(1)。
    wm/wl：中/低危在目标函数中的权重（高危由 ε 约束保护，不进目标）。
    eps：高危阻塞率 QoS 上界（CAC 目标），默认 10%。

    ★修复（2026-09-22）：不可行区退化★
    原实现在无可行解时按 key=(1, round(Bh,8), round(obj,8)) 排序，即「只按高危阻塞
    最小」。在窄带重载（如风暴场景 c=4、A=(4,4,4)）下必然无可行解，该规则会选出
    g_h=c 的病态解——**以冻结全部中低危（B_m=B_l=1.0）换取 B_h 从 0.3168 到 0.3107
    的 0.6% 边际改善**，且 Bh 的 8 位小数精度使 obj 在第三位几乎失效，等价于完全
    放弃中低危。这与「生存优先≠其余全弃」的设计目标相悖。
    现改为不可行区使用**相对代价归一化排序**：
        cost = B_h + (wm·B_m + wl·B_l) / (wm + wl)
    即在高危阻塞与中低危加权阻塞之间做加权折中（两者同量纲、均在 [0,1]），
    既不放弃高危保护，也不允许以「全灭其余」换取微小改善。
    可行解区逻辑保持不变（B_h ≤ eps 硬约束优先，再最小化中低危加权阻塞）。
    """
    Ah, Am, Al = round(float(Ah), 4), round(float(Am), 4), round(float(Al), 4)
    eps = float(eps)
    wsum = (wm + wl) or 1.0
    best = None  # (sort_key, (gh,gm), (Bh,Bm,Bl))
    for gh in range(0, c + 1):
        for gm in range(0, c - gh + 1):
            bh, bm, bl, _ = blocking(c, Ah, Am, Al, gh, gm)
            feasible = bh <= eps
            obj = wm * bm + wl * bl
            if feasible:
                # 可行：硬约束已满足，最小化中低危加权阻塞（再以 Bh 作次级判据）
                key = (0, round(obj, 8), round(bh, 8))
            else:
                # 不可行：高危与中低危加权阻塞的折中（同量纲），避免「全灭中低危」病态解
                cost = bh + obj / wsum
                key = (1, round(cost, 8), round(bh, 8), round(obj, 8))
            if best is None or key < best[0]:
                best = (key, (gh, gm), (bh, bm, bl))
    return best[1], best[2]


if __name__ == "__main__":
    # 自检：低高危负载（A_h 小）→ 最优 g_h 应趋近 0（回收闲置）；高负载 → g_h 增大
    c = 64
    print("低高危负载 A=(2,20,40):", optimal_guards(c, 2, 20, 40))
    print("均衡负载   A=(20,20,20):", optimal_guards(c, 20, 20, 20))
    print("高危主导   A=(50,5,5):", optimal_guards(c, 50, 5, 5))
    c2 = 4
    print("窄带低高危 A=(0.1,1,2):", optimal_guards(c2, 0.1, 1.0, 2.0))
    print("窄带均衡   A=(1,1,1):", optimal_guards(c2, 1.0, 1.0, 1.0))
