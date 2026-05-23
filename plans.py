# プランごとの機能制限を一元管理する。課金機能追加時はここだけ変える
LIMITS = {
    'free':    {'max_sites': 3, 'max_apps': 1, 'block_time_add': False},
    'premium': {'max_sites': None, 'max_apps': None, 'block_time_add': True},
}

# サービス数カウント時に除去する汎用サブドメインプレフィックス
_STRIP_PREFIXES = ('www.', 'm.', 'mobile.')


def get_limits(plan: str) -> dict:
    return LIMITS.get(plan, LIMITS['free'])


def normalize_domain(domain: str) -> str:
    """www.youtube.com → youtube.com など汎用プレフィックスを除去してサービス単位に正規化する"""
    d = domain.lower().strip()
    for prefix in _STRIP_PREFIXES:
        if d.startswith(prefix):
            return d[len(prefix):]
    return d


def count_unique_domains(sites: list) -> int:
    """ドメインリストをサービス単位に正規化したうえでユニーク数を返す。
    例: [youtube.com, www.youtube.com, youtu.be] → 2"""
    return len(set(normalize_domain(s) for s in sites))


def within_site_limit(plan: str, count: int) -> bool:
    limit = get_limits(plan)['max_sites']
    return limit is None or count <= limit


def within_app_limit(plan: str, count: int) -> bool:
    limit = get_limits(plan)['max_apps']
    return limit is None or count <= limit
