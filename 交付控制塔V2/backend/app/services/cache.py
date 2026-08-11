"""内存 TTL 缓存（cachetools 实现），预留 Redis 切换接口"""
from __future__ import annotations

import time
from typing import Any, Callable, Protocol

from cachetools import TTLCache


class CacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int) -> None: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...
    def health(self) -> bool: ...


class MemoryCache:
    """线程安全的内存 TTL 缓存"""

    def __init__(self, maxsize: int = 512, default_ttl: int = 60):
        self._cache: TTLCache[str, tuple[Any, float]] = TTLCache(
            maxsize=maxsize, ttl=default_ttl
        )

    def get(self, key: str) -> Any | None:
        item = self._cache.get(key)
        if item is None:
            return None
        value, expires = item
        if time.time() > expires:
            self._cache.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if ttl is None:
            self._cache[key] = (value, time.time() + self._cache.ttl)
        else:
            self._cache[key] = (value, time.time() + ttl)

    def delete(self, key: str) -> None:
        self._cache.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()

    def health(self) -> bool:
        return True

    @property
    def size(self) -> int:
        return len(self._cache)


class RedisCache:
    """Redis 缓存（预留，未启用时抛出明确错误）"""

    def __init__(self, url: str):
        raise NotImplementedError("Redis 缓存本期未启用，请使用 MemoryCache 或设置 CACHE_TTL_DEFAULT")


_cache: MemoryCache | None = None


def get_cache() -> MemoryCache:
    """全局缓存单例"""
    global _cache
    if _cache is None:
        from app.config import get_settings

        settings = get_settings()
        _cache = MemoryCache(default_ttl=settings.CACHE_TTL_DEFAULT)
    return _cache


def cached(ttl: int = 60, key_fn: Callable[..., str] | None = None):
    """装饰器：缓存函数结果（同步/异步均可）"""

    def decorator(fn: Callable):
        import functools
        import inspect

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                key = key_fn(*args, **kwargs) if key_fn else f"{fn.__name__}:{args}:{kwargs}"
                cache = get_cache()
                hit = cache.get(key)
                if hit is not None:
                    return hit
                value = await fn(*args, **kwargs)
                cache.set(key, value, ttl)
                return value

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if key_fn else f"{fn.__name__}:{args}:{kwargs}"
            cache = get_cache()
            hit = cache.get(key)
            if hit is not None:
                return hit
            value = fn(*args, **kwargs)
            cache.set(key, value, ttl)
            return value

        return sync_wrapper

    return decorator
