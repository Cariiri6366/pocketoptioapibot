"""
PocketOption Bot Signals API - Production-optimized
- Keeps connection alive globally
- Precomputes signals in background
- Returns cached results instantly (1-3s target)
"""
import os
import asyncio
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from dotenv import load_dotenv

from pocketoptionapi_async.client import AsyncPocketOptionClient
from pocketoptionapi_async.constants import TIMEFRAMES

load_dotenv()

PO_SSID = os.environ.get("PO_SSID")
if not PO_SSID:
    raise RuntimeError(
        "PO_SSID environment variable not set. "
        "Export your full auth string 42[\"auth\",{...}] into PO_SSID."
    )

# Precompute config - popular assets refreshed every 5s
PREFETCH_ASSETS = [
    "EURUSD_otc",
    "GBPUSD_otc",
    "USDJPY_otc",
    "USDCHF_otc",
    "USDCAD_otc",
    "AUDUSD_otc",
    "AUDCAD_otc",
    "XAUUSD_otc",
    "XAGUSD_otc",
]
PREFETCH_TIMEFRAMES = ["1m", "5m"]
PREFETCH_INTERVAL_SEC = 5
CACHE_TTL_SEC = 5

client: AsyncPocketOptionClient | None = None
_client_lock = asyncio.Lock()
_signal_cache: dict[str, dict] = {}
_cache_lock = asyncio.Lock()
_precompute_task: asyncio.Task | None = None


def _cache_key(asset: str, timeframe: str) -> str:
    return f"{asset}|{timeframe}"


def decide_direction(df) -> tuple[Literal["buy", "sell", "neutral"], int, str]:
    if df is None or df.empty or len(df) < 3:
        return "neutral", 0, "Not enough data"

    last_two = df.tail(3).iloc[:-1]
    c_prev = last_two.iloc[0]
    c_last = last_two.iloc[1]

    if c_last["close"] > c_prev["close"]:
        direction = "buy"
    elif c_last["close"] < c_prev["close"]:
        direction = "sell"
    else:
        direction = "neutral"

    recent = df.tail(20)
    bodies = (recent["close"] - recent["open"]).abs()
    avg_body = bodies.mean() or 0.00001
    last_body = abs(c_last["close"] - c_last["open"])
    ratio = float(last_body / avg_body)

    if ratio >= 1.8:
        conf = 85
    elif ratio >= 1.2:
        conf = 70
    else:
        conf = 55 if direction != "neutral" else 40

    msg = f"Second candle suggests {direction.upper()} (body ratio {ratio:.2f})"
    return direction, conf, msg


async def get_client() -> AsyncPocketOptionClient:
    global client
    async with _client_lock:
        if client is None or not client.is_connected:
            client = AsyncPocketOptionClient(
                ssid=PO_SSID,
                is_demo=True,
                persistent_connection=True,
                auto_reconnect=True,
            )
            ok = await client.connect()
            if not ok:
                raise RuntimeError("Failed to connect to PocketOption")
        return client


async def _compute_signal(asset: str, timeframe: str, count: int = 100) -> dict | None:
    try:
        cli = await get_client()
        df = await cli.get_candles_dataframe(
            asset, timeframe, count=count, end_time=datetime.now()
        )
        direction, confidence, message = decide_direction(df)
        return {
            "asset": asset,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": confidence,
            "message": message,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "cached": False,
        }
    except Exception:
        return None


async def _precompute_loop():
    """Background task: precompute signals for popular assets every few seconds."""
    while True:
        try:
            for asset in PREFETCH_ASSETS:
                for tf in PREFETCH_TIMEFRAMES:
                    result = await _compute_signal(asset, tf)
                    if result:
                        result["cached"] = True
                        key = _cache_key(asset, tf)
                        async with _cache_lock:
                            _signal_cache[key] = {
                                **result,
                                "_cached_at": datetime.utcnow(),
                            }
        except Exception:
            pass
        await asyncio.sleep(PREFETCH_INTERVAL_SEC)


async def _get_cached(asset: str, timeframe: str) -> dict | None:
    key = _cache_key(asset, timeframe)
    async with _cache_lock:
        entry = _signal_cache.get(key)
    if not entry:
        return None
    cached_at = entry.get("_cached_at")
    if cached_at and (datetime.utcnow() - cached_at).total_seconds() > CACHE_TTL_SEC:
        return None
    out = {k: v for k, v in entry.items() if not k.startswith("_")}
    out["cached"] = True
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect and start precompute. Shutdown: cleanup."""
    global _precompute_task
    try:
        await get_client()
        _precompute_task = asyncio.create_task(_precompute_loop())
    except Exception:
        pass
    yield
    if _precompute_task:
        _precompute_task.cancel()
        try:
            await _precompute_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="PocketOption Bot Signals", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Lightweight health check."""
    try:
        cli = await get_client()
        return {"status": "ok", "connected": cli.is_connected}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


@app.get("/signal")
async def get_signal(
    asset: str = Query(..., description="PocketOption asset, e.g. EURUSD_otc"),
    timeframe: str = Query("1m", description="1m,5m,15m,1h,4h"),
    count: int = Query(100, ge=10, le=500),
):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Invalid timeframe")

    # 1. Return cached immediately if fresh
    cached = await _get_cached(asset, timeframe)
    if cached:
        return cached

    # 2. Try live fetch
    try:
        result = await _compute_signal(asset, timeframe, count)
        if result:
            result["cached"] = False
            key = _cache_key(asset, timeframe)
            async with _cache_lock:
                _signal_cache[key] = {
                    **result,
                    "_cached_at": datetime.utcnow(),
                }
            return result
    except Exception:
        pass

    # 3. Fallback: return stale cache if available
    key = _cache_key(asset, timeframe)
    async with _cache_lock:
        entry = _signal_cache.get(key)
    if entry:
        out = {k: v for k, v in entry.items() if not k.startswith("_")}
        out["cached"] = True
        return out

    raise HTTPException(
        status_code=503,
        detail="Signal unavailable. Server may be warming up. Please retry.",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
