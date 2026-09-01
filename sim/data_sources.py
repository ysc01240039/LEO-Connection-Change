"""真实数据源（REPLACEABLE：换 URL/解析即可换星座）。
所有抓取均记录 provenance（来源/URL/时间/数量）；TLE 落盘缓存（data/tle/<group>.txt），
离线复现时优先读缓存，避免依赖网络。绝不硬编码数据本身。源不可达且无缓存则抛错。"""
import datetime
from pathlib import Path
import requests
from .config import DATA_DIR

CELERTRAK = "https://celestrak.org/NORAD/elements/gp.php"
TLE_DIR = DATA_DIR / "tle"

# 可替换数据源清单：新增一项即可切换星座（如 Starlink、Galileo、北斗）
SOURCE_GROUPS = {
    "oneweb": {
        "url": f"{CELERTRAK}?GROUP=oneweb&FORMAT=TLE",
        "desc": "OneWeb 星座 TLE（Celestrak NORAD 公开两行根数）",
    },
    # "starlink": {
    #     "url": f"{CELERTRAK}?GROUP=starlink&FORMAT=TLE",
    #     "desc": "Starlink 星座 TLE（约 6000 颗，抓取/计算更重，按需开启）",
    # },
}


def _cache_path(group: str) -> Path:
    TLE_DIR.mkdir(parents=True, exist_ok=True)
    return TLE_DIR / f"{group}.txt"


def fetch_tle(group: str = "oneweb", use_cache: bool = True):
    """返回 (星历列表[(name,l1,l2)], provenance)。
    优先读本地缓存（可复现、离线可用）；无缓存才联网抓取并落盘。"""
    if group not in SOURCE_GROUPS:
        raise KeyError(f"未知数据源 {group}，可选: {list(SOURCE_GROUPS)}")
    meta = SOURCE_GROUPS[group]
    cache = _cache_path(group)
    fetched_utc = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    hit_cache = False
    if use_cache and cache.exists():
        text = cache.read_text(encoding="utf-8", errors="ignore")
        hit_cache = True
    else:
        r = requests.get(meta["url"], timeout=30)
        r.raise_for_status()
        text = r.text
        cache.write_text(text, encoding="utf-8")
        hit_cache = False
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    sats = []
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith("1 ") and l2.startswith("2 "):
            sats.append((name, l1, l2))
    provenance = {
        "source": meta["desc"],
        "url": meta["url"],
        "fetched_utc": fetched_utc,
        "satellite_count": len(sats),
        "cache": cache.as_posix(),          # TLE 已落盘，供审计/离线复现
        "cache_hit": hit_cache,
    }
    return sats, provenance