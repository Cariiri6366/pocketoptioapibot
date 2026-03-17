"""
Nuunipay Signals Bot - Production-ready for Render
- Single global PocketOption client (no reconnect per request)
- Background updater refreshes market data every few seconds
- In-memory cache (asset:timeframe) returns instantly
- Fallback to stale cache if live fetch fails
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

from signal_cache import SignalCacheManager

load_dotenv()

PO_SSID = os.environ.get("PO_SSID")
if not PO_SSID:
    raise RuntimeError(
        "PO_SSID environment variable not set. "
        "Export your full auth string 42[\"auth\",{...}] into PO_SSID."
    )

# Tracked assets and timeframes - refreshed in background
TRACKED_ASSETS = [
    "EURUSD_otc",
    "USDCHF_otc",
    "AUDUSD_otc",
    "GBPUSD_otc",
    "USDJPY_otc",
    "USDCAD_otc",
    "AUDCAD_otc",
    "XAUUSD_otc",
    "XAGUSD_otc",
]
TRACKED_TIMEFRAMES = ["1m", "5m"]
REFRESH_INTERVAL_SEC = 5
CACHE_TTL_SEC = 5

client: AsyncPocketOptionClient | None = None
_client_lock = asyncio.Lock()
signal_cache = SignalCacheManager(ttl_sec=CACHE_TTL_SEC)
_background_task: asyncio.Task | None = None


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
    """Single global client - never reconnect on every request."""
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


async def _background_refresh_loop():
    """Continuously refresh market data and recalculate signals for tracked assets."""
    while True:
        try:
            for asset in TRACKED_ASSETS:
                for tf in TRACKED_TIMEFRAMES:
                    result = await _compute_signal(asset, tf)
                    if result:
                        await signal_cache.set(asset, tf, result)
        except Exception:
            pass
        await asyncio.sleep(REFRESH_INTERVAL_SEC)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect client and start background updater. Shutdown: cleanup."""
    global _background_task
    try:
        await get_client()
        _background_task = asyncio.create_task(_background_refresh_loop())
    except Exception:
        pass
    yield
    if _background_task:
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Nuunipay Signals Bot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Warm root endpoint - keeps Render instance alive."""
    return {"service": "Nuunipay Signals Bot", "status": "running"}


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
    cached = await signal_cache.get(asset, timeframe)
    if cached:
        return cached

    # 2. Try live fetch
    try:
        result = await _compute_signal(asset, timeframe, count)
        if result:
            result["cached"] = False
            await signal_cache.set(asset, timeframe, result)
            return result
    except Exception:
        pass

    # 3. Fallback: return last cached (even if stale)
    stale = await signal_cache.get_stale(asset, timeframe)
    if stale:
        return stale

    raise HTTPException(
        status_code=503,
        detail="Signal unavailable. Server may be warming up. Please retry.",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
