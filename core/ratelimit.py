"""Fixed-window rate limiting backed by Django's cache.

Use a shared cache (CACHE_BACKEND=db or Redis) in production so limits apply across workers.
"""
import hashlib

from django.core.cache import cache


def _key(scope, identifier):
    digest = hashlib.sha256(str(identifier).lower().encode()).hexdigest()[:32]
    return f"rl:{scope}:{digest}"


def is_limited(scope, identifier, limit):
    return (cache.get(_key(scope, identifier)) or 0) >= limit


def hit(scope, identifier, window):
    key = _key(scope, identifier)
    if cache.add(key, 1, timeout=window):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, timeout=window)
        return 1


def reset(scope, identifier):
    cache.delete(_key(scope, identifier))
