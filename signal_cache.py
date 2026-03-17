"""
Nuunipay Signals Bot - Signal Cache Manager
In-memory cache for precomputed signals. Keys: asset:timeframe
"""
import asyncio
from datetime import datetime
from typing import Any


class SignalCacheManager:
    """Thread-safe in-memory cache for signal results."""

    def __init__(self, ttl_sec: int = 5):
        self._cache: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._ttl_sec = ttl_sec

    @staticmethod
    def key(asset: str, timeframe: str) -> str:
        return f"{asset}:{timeframe}"

    async def get(self, asset: str, timeframe: str, allow_stale: bool = False) -> dict | None:
        """Get cached signal. If allow_stale, return even if TTL expired."""
        k = self.key(asset, timeframe)
        async with self._lock:
            entry = self._cache.get(k)
        if not entry:
            return None
        cached_at = entry.get("_cached_at")
        if not cached_at:
            out = self._to_response(entry)
            out["cached"] = True
            return out
        age_sec = (datetime.utcnow() - cached_at).total_seconds()
        if age_sec <= self._ttl_sec or allow_stale:
            out = self._to_response(entry)
            out["cached"] = True
            return out
        return None

    async def set(self, asset: str, timeframe: str, result: dict) -> None:
        """Store signal result."""
        k = self.key(asset, timeframe)
        entry = {**result, "_cached_at": datetime.utcnow()}
        async with self._lock:
            self._cache[k] = entry

    async def get_stale(self, asset: str, timeframe: str) -> dict | None:
        """Get last cached result even if expired (fallback when live fetch fails)."""
        return await self.get(asset, timeframe, allow_stale=True)

    def size(self) -> int:
        """Return number of cached entries (for health endpoint)."""
        return len(self._cache)

    async def get_debug_info(self) -> list[dict]:
        """
        Return cache keys and freshness for /debug/cache endpoint.
        No secrets exposed - only key, age_sec, is_fresh.
        """
        async with self._lock:
            now = datetime.utcnow()
            items = []
            for k, entry in self._cache.items():
                cached_at = entry.get("_cached_at")
                if cached_at:
                    age_sec = (now - cached_at).total_seconds()
                    is_fresh = age_sec <= self._ttl_sec
                    items.append({"key": k, "age_sec": round(age_sec, 1), "fresh": is_fresh})
                else:
                    items.append({"key": k, "age_sec": None, "fresh": True})
        return items

    def _to_response(self, entry: dict) -> dict:
        out = {k: v for k, v in entry.items() if not k.startswith("_")}
        # Ensure consistent API response fields
        if "source" not in out:
            out["source"] = "memory_cache"
        if "firestore_fallback" not in out:
            out["firestore_fallback"] = False
        if "fallback" not in out:
            out["fallback"] = False
        return out
