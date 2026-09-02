"""T4 星上轻量凭证认证（真实 HMAC-SHA256，非占位实现）。

审计修复说明（原 `protocol.py:179-184` / `leo_access.cc:456` 为读终端自报比特的 Mock，
拦截率恒为 1.0，属同义反复。本模块替换为真实密码学校验，使指标可证伪。）

认证模型
--------
- 派生密钥：dev_key = HMAC(root_key, "devkey" || term_id)，根密钥由场景种子派生。
- 凭证：(pseudo_id, counter, mac)，mac = HMAC(dev_key, pseudo || counter)[:MAC_BYTES]。
- 星上校验：重算 MAC 比对 + counter 单调递增（防重放）。

攻击者建模（决定漏检率，是本模块的关键设计）
--------------------------------------------
伪造终端按 `compromised_share` 分为两类：
- **盲伪造(blind)**：无有效密钥，MAC 为随机字节 → 校验必失败 → 被拦截。
  漏检仅来自盲猜撞中，概率 2^(-8*MAC_BYTES)（4 字节时为 2^-32 ≈ 2.3e-10）。
- **密钥泄露(compromised)**：持有效 dev_key，能生成合法 MAC 且 counter 合法递增 →
  **密码层无法检出**，必然漏检。
  这类攻击者刻画了纯密码认证的**检出率上限**，也是 AGENTS.md §0.3 论证
  "需与物理层特征融合" 的量化依据。

故：拦截率 ≈ 1 - compromised_share（非 1.0），漏检率 ≈ compromised_share。
两者均为**计算结果**，且 compromised_share 可扫描。

虚警率由信道误码决定（见 channel.mac_fail_prob）：BER 翻转 MAC 比特 → 合法终端被误拒。
这使"仰角代价"通过误码率兑现为真实后果。

时延：`auth_extra_ms` 由 `measure_verify_ms()` 对本机真实 HMAC 校验计时得到，
不再是无源常量。
"""
import hashlib
import hmac
import timeit

from .config import AUTH_MAC_BYTES, AUTH_PSEUDO_BYTES, AUTH_MEASURE_SAMPLES

MAC_BITS = AUTH_MAC_BYTES * 8


def derive_root_key(seed: int) -> bytes:
    """由场景种子派生根密钥（确定性，保证实验可复现）。"""
    return hashlib.sha256(b"LEO-EMERG-ROOT" + str(int(seed)).encode()).digest()


def derive_dev_key(root_key: bytes, term_id: int) -> bytes:
    return hmac.new(root_key, b"devkey" + str(int(term_id)).encode(),
                    hashlib.sha256).digest()


def make_pseudo(root_key: bytes, term_id: int) -> bytes:
    """假名标识（对外不暴露 dev_id，支持切换时轮换）。"""
    return hmac.new(root_key, b"pseudo" + str(int(term_id)).encode(),
                    hashlib.sha256).digest()[:AUTH_PSEUDO_BYTES]


def sign(dev_key: bytes, pseudo: bytes, counter: int) -> bytes:
    msg = pseudo + counter.to_bytes(8, "big")
    return hmac.new(dev_key, msg, hashlib.sha256).digest()[:AUTH_MAC_BYTES]


class OnboardAuth:
    """星上校验器：验签 + 重放检测。"""

    def __init__(self, root_key: bytes):
        self.root_key = root_key
        self._last = {}          # pseudo -> 已见最大 counter
        self.n_ok = 0
        self.n_bad_mac = 0
        self.n_replay = 0

    def verify(self, dev_key: bytes, pseudo: bytes, counter: int, mac: bytes) -> str:
        """返回 'ok' | 'bad_mac' | 'replay'。"""
        last = self._last.get(pseudo)
        if last is not None and counter <= last:
            self.n_replay += 1
            return "replay"
        if not hmac.compare_digest(sign(dev_key, pseudo, counter), mac):
            self.n_bad_mac += 1
            return "bad_mac"
        self._last[pseudo] = counter
        self.n_ok += 1
        return "ok"


def forge_mac(rand_bytes_fn) -> bytes:
    """盲伪造：随机字节 MAC（撞中概率 2^-MAC_BITS）。"""
    return rand_bytes_fn(AUTH_MAC_BYTES)


def measure_verify_ms(samples: int = AUTH_MEASURE_SAMPLES) -> float:
    """实测本机 HMAC-SHA256 校验耗时（ms/次），替代硬编码 auth_extra_ms。

    注意：这是**仿真宿主 CPU** 的实测值，用作星上处理时延的一阶估计；
    星上真实算力不同，故该值作为可扫描参数暴露（见 config.AUTH_MEASURE_SAMPLES）。
    """
    key = b"\x01" * 32
    pseudo = b"\x02" * AUTH_PSEUDO_BYTES
    ctr = 7
    mac = sign(key, pseudo, ctr)
    auth = OnboardAuth(b"\x00" * 32)
    t = timeit.timeit(lambda: auth.verify(key, pseudo, ctr, mac), number=samples)
    return round(t / samples * 1000.0, 6)
