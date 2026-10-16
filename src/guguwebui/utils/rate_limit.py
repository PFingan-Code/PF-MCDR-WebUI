"""登录类接口的失败次数限制（进程内滑动窗口）。

两级限制：
- 账号级：同一 IP + 同一账号，窗口内最多 5 次失败（防单账号暴力破解）；
- IP 级：同一 IP 所有账号合计，窗口内最多 20 次失败（防撞库/换号尝试）。
只统计失败；成功登录清除该账号的失败记录（IP 级记录不清除，避免被
一个已知账号反复“洗白”）。

另有不区分 IP 的全局限制，应对公网（frp 等）下的分布式尝试：
- 临时登录码：所有来源合计，窗口内最多 100 次失败（临时码不对应账号，只能全局计数）；
- 两步验证码：同一账号所有来源合计，15 分钟内最多 10 次失败（能走到这一步的人已持有密码）。

以及按 IP 的请求频率限制（hit_limited），用于会触发外部请求或写库的匿名接口。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Optional


class FailureLimiter:
    """窗口内失败次数达到上限后拒绝，直到最早一次失败滑出窗口。"""

    def __init__(self, max_failures: int, window_seconds: float, max_keys: int = 10000):
        self.max_failures = max_failures
        self.window = window_seconds
        self.max_keys = max_keys
        self._failures: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Optional[Deque[float]]:
        q = self._failures.get(key)
        if q is None:
            return None
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            self._failures.pop(key, None)
            return None
        return q

    def retry_after(self, key: str, now: Optional[float] = None) -> float:
        """被限制时返回需等待的秒数，否则返回 0。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._prune(key, now)
            if q is None or len(q) < self.max_failures:
                return 0.0
            return max(0.0, self.window - (now - q[0]))

    def record_failure(self, key: str, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            if key not in self._failures and len(self._failures) >= self.max_keys:
                for k in list(self._failures):
                    self._prune(k, now)
                if len(self._failures) >= self.max_keys:
                    # 仍然过多：丢弃最早插入的键，保证内存有上限
                    self._failures.pop(next(iter(self._failures)), None)
            self._failures.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


LOGIN_WINDOW_SECONDS = 300
account_limiter = FailureLimiter(max_failures=5, window_seconds=LOGIN_WINDOW_SECONDS)
ip_limiter = FailureLimiter(max_failures=20, window_seconds=LOGIN_WINDOW_SECONDS)


# 全局（不区分 IP）
temp_code_global_limiter = FailureLimiter(max_failures=100, window_seconds=LOGIN_WINDOW_SECONDS)
TOTP_WINDOW_SECONDS = 900
totp_account_limiter = FailureLimiter(max_failures=10, window_seconds=TOTP_WINDOW_SECONDS)

# 匿名接口的请求频率（每次请求都计数）
_hit_limiters: Dict[str, FailureLimiter] = {}
_hit_lock = threading.Lock()


def _account_key(scope: str, ip: str, account: Optional[str]) -> str:
    return f"{scope}|{ip}|{(account or '').strip().lower()}"


def _ip_key(ip: str) -> str:
    return f"ip|{ip}"


def login_retry_after(scope: str, ip: str, account: Optional[str] = None) -> int:
    """返回需等待的秒数（向上取整）；0 表示允许尝试。account 为 None 时只检查 IP 级。"""
    wait = ip_limiter.retry_after(_ip_key(ip))
    if account is not None:
        wait = max(wait, account_limiter.retry_after(_account_key(scope, ip, account)))
    return int(wait) + (1 if wait % 1 else 0)


def record_login_failure(scope: str, ip: str, account: Optional[str] = None) -> None:
    ip_limiter.record_failure(_ip_key(ip))
    if account is not None:
        account_limiter.record_failure(_account_key(scope, ip, account))


def record_login_success(scope: str, ip: str, account: Optional[str] = None) -> None:
    if account is not None:
        account_limiter.reset(_account_key(scope, ip, account))


def _wait_seconds(wait: float) -> int:
    return int(wait) + (1 if wait % 1 else 0)


def temp_code_retry_after() -> int:
    return _wait_seconds(temp_code_global_limiter.retry_after("temp"))


def record_temp_code_failure() -> None:
    temp_code_global_limiter.record_failure("temp")


def totp_retry_after(account: str) -> int:
    return _wait_seconds(totp_account_limiter.retry_after((account or "").strip().lower()))


def record_totp_failure(account: str) -> None:
    totp_account_limiter.record_failure((account or "").strip().lower())


def hit_limited(bucket: str, ip: str, max_hits: int, window_seconds: float) -> int:
    """记录一次请求；超出频率时返回需等待的秒数（本次不计入），否则返回 0。"""
    with _hit_lock:
        limiter = _hit_limiters.get(bucket)
        if limiter is None:
            limiter = FailureLimiter(max_failures=max_hits, window_seconds=window_seconds)
            _hit_limiters[bucket] = limiter
    wait = limiter.retry_after(ip)
    if wait > 0:
        return _wait_seconds(wait)
    limiter.record_failure(ip)
    return 0


def reset_all() -> None:
    """测试用：清空全部计数。"""
    account_limiter.clear()
    ip_limiter.clear()
    temp_code_global_limiter.clear()
    totp_account_limiter.clear()
    with _hit_lock:
        for limiter in _hit_limiters.values():
            limiter.clear()
