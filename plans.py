# プランごとの機能制限を一元管理する。課金機能追加時はここだけ変える
LIMITS = {
    'free':    {'max_sites': 3, 'max_apps': 1},
    'premium': {'max_sites': None, 'max_apps': None},
}


def get_limits(plan: str) -> dict:
    return LIMITS.get(plan, LIMITS['free'])


def within_site_limit(plan: str, count: int) -> bool:
    limit = get_limits(plan)['max_sites']
    return limit is None or count <= limit


def within_app_limit(plan: str, count: int) -> bool:
    limit = get_limits(plan)['max_apps']
    return limit is None or count <= limit
