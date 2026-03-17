"""
Nuunipay Signals Bot - Production-ready FastAPI backend for Render.
- Single global PocketOption client (no reconnect per request)
- In-memory cache by asset:timeframe with configurable TTL
- Background task precomputes signals for tracked assets
- Fallback to stale cache when live fetch fails
- Consistent JSON responses, structured error handling
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from pocketoptionapi_async.constants import TIMEFRAMES
from pocketoptionapi_async.exceptions import (
    ConnectionError as POConnectionError,
    InvalidParameterError,
    PocketOptionError,
)

from client_manager import ensure_connected, get_client, is_connected
from config import (
    CACHE_TTL_SEC,
    CANDLE_TIMEOUT_SEC,
    DEFAULT_CANDLE_COUNT,
    ENABLE_FIRESTORE,
    FIRESTORE_LATEST_TTL_SEC,
    FIRESTORE_WRITE_HISTORY,
    FIREBASE_SERVICE_ACCOUNT_JSON,
    HISTORY_WRITE_INTERVAL_SEC,
    IS_DEMO,
    MIN_CANDLES_FOR_SIGNAL,
    PO_SSID,
    REFRESH_INTERVAL_SEC,
    TRACKED_ASSETS,
    TRACKED_TIMEFRAMES,
)
from firestore_helper import (
    append_signal_history,
    get_latest_signal,
    init_firestore,
    save_latest_signal,
)
from signal_cache import SignalCacheManager
from signal_logic import compute_signal

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
if not PO_SSID:
    raise RuntimeError(
        "PO_SSID environment variable not set. "
        "Export your full auth string 42[\"auth\",{...}] into PO_SSID."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("nuunipay.signals")

signal_cache = SignalCacheManager(ttl_sec=CACHE_TTL_SEC)
_background_task: asyncio.Task | None = None
# Track last signal per asset:timeframe for history write throttling
_last_signal_for_history: dict[str, tuple[str, int]] = {}
_last_history_write_time: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Signal computation
# ---------------------------------------------------------------------------
async def _compute_signal(
    asset: str, timeframe: str, count: int = DEFAULT_CANDLE_COUNT
) -> dict | None:
    """Fetch candles, compute signal, return standardized dict or None on failure."""
    try:
        cli = await asyncio.wait_for(get_client(), timeout=45.0)
        df = await asyncio.wait_for(
            cli.get_candles_dataframe(
                asset, timeframe, count=count, end_time=datetime.now()
            ),
            timeout=CANDLE_TIMEOUT_SEC,
        )
        direction, confidence, message = compute_signal(
            df, min_candles=MIN_CANDLES_FOR_SIGNAL
        )
        return {
            "asset": asset,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": confidence,
            "message": message,
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cached": False,
            "firestore_fallback": False,
            "source": "live",
        }
    except asyncio.TimeoutError:
        logger.warning("Candle fetch or connection timeout for %s %s", asset, timeframe)
        return None
    except POConnectionError as e:
        logger.warning("Connection error for %s %s: %s", asset, timeframe, e)
        return None
    except InvalidParameterError as e:
        logger.warning("Invalid parameter for %s %s: %s", asset, timeframe, e)
        raise
    except PocketOptionError as e:
        logger.warning("PocketOption error for %s %s: %s", asset, timeframe, e)
        return None
    except Exception as e:
        logger.exception("Unexpected error computing signal for %s %s", asset, timeframe)
        return None


def _maybe_append_history(asset: str, timeframe: str, result: dict) -> None:
    """Append to signal_history only when direction/confidence changes and interval elapsed."""
    if not FIRESTORE_WRITE_HISTORY:
        return
    key = SignalCacheManager.key(asset, timeframe)
    now = time.monotonic()
    last_write = _last_history_write_time.get(key, 0)
    if now - last_write < HISTORY_WRITE_INTERVAL_SEC:
        return  # Throttle: don't write more often than interval
    last = _last_signal_for_history.get(key)
    last_dir, last_conf = last if last else (None, None)
    if append_signal_history(
        asset,
        timeframe,
        result,
        source="live",
        last_direction=last_dir,
        last_confidence=last_conf,
        confidence_delta_threshold=15,
    ):
        _last_history_write_time[key] = now
    _last_signal_for_history[key] = (result.get("direction", "neutral"), result.get("confidence", 0))


async def _background_refresh_loop():
    """Periodically refresh signals for tracked asset/timeframe pairs."""
    logger.info(
        "Background refresh started: %d assets, %d timeframes, interval=%ds",
        len(TRACKED_ASSETS),
        len(TRACKED_TIMEFRAMES),
        REFRESH_INTERVAL_SEC,
    )
    while True:
        try:
            if not await ensure_connected():
                logger.warning("Background refresh: not connected, skipping cycle")
                await asyncio.sleep(REFRESH_INTERVAL_SEC)
                continue

            for asset in TRACKED_ASSETS:
                for tf in TRACKED_TIMEFRAMES:
                    try:
                        result = await _compute_signal(asset, tf)
                        if result:
                            await signal_cache.set(asset, tf, result)
                            save_latest_signal(asset, tf, result, source="live", is_demo=IS_DEMO)
                            _maybe_append_history(asset, tf, result)
                    except InvalidParameterError:
                        pass  # Skip invalid asset/tf
                    except Exception as e:
                        logger.debug("Background refresh %s %s: %s", asset, tf, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Background refresh error: %s", e)

        await asyncio.sleep(REFRESH_INTERVAL_SEC)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect client, init Firestore, start background refresh. Shutdown: cleanup."""
    global _background_task
    try:
        init_firestore(ENABLE_FIRESTORE, FIREBASE_SERVICE_ACCOUNT_JSON or None)
        await ensure_connected()
        _background_task = asyncio.create_task(_background_refresh_loop())
        logger.info("Nuunipay Signals Bot started")
    except Exception as e:
        logger.error("Startup failed: %s", e)
    yield
    if _background_task:
        _background_task.cancel()
        try:
            await _background_task
        except asyncio.CancelledError:
            pass
    logger.info("Nuunipay Signals Bot stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="Nuunipay Signals Bot", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Never expose tracebacks to frontend - return clean error."""
    from fastapi import HTTPException as FastAPIHTTPException
    from fastapi.responses import JSONResponse

    if isinstance(exc, FastAPIHTTPException):
        raise exc
    logger.exception("Unhandled error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please try again later."},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/")
async def root():
    """Root endpoint - basic status."""
    return {"status": "running"}


@app.get("/health")
async def health():
    """Health check - connection status."""
    try:
        connected = is_connected()
        if not connected:
            connected = await ensure_connected()
        return {"status": "ok" if connected else "degraded", "connected": connected}
    except Exception as e:
        return {"status": "error", "connected": False, "error": str(e)}


@app.get("/debug/cache")
async def debug_cache():
    """Show cache keys and freshness (no secrets)."""
    items = await signal_cache.get_debug_info()
    return {"keys": items, "ttl_sec": CACHE_TTL_SEC}


@app.get("/tracked-assets")
async def tracked_assets():
    """Return currently tracked assets and timeframes."""
    return {
        "assets": TRACKED_ASSETS,
        "timeframes": TRACKED_TIMEFRAMES,
        "refresh_interval_sec": REFRESH_INTERVAL_SEC,
    }


@app.get("/signal")
async def get_signal(
    asset: str = Query(..., description="PocketOption asset, e.g. EURUSD_otc"),
    timeframe: str = Query("1m", description="1m, 5m, 15m, 1h, 4h"),
    count: int = Query(DEFAULT_CANDLE_COUNT, ge=10, le=500),
):
    """
    Get trading signal for asset/timeframe.
    Flow: memory cache -> Firestore fallback -> live compute.
    Returns consistent response with source, firestore_fallback, cached.
    """
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Invalid timeframe")

    # 1. Return memory cache immediately if fresh
    cached = await signal_cache.get(asset, timeframe)
    if cached:
        cached["source"] = "memory_cache"
        cached["firestore_fallback"] = False
        return cached

    # 2. Try Firestore fallback if enabled and memory cache miss
    fs_signal = get_latest_signal(asset, timeframe, max_age_sec=FIRESTORE_LATEST_TTL_SEC)
    if fs_signal:
        return fs_signal

    # 3. Try live fetch
    try:
        result = await _compute_signal(asset, timeframe, count)
        if result:
            result["cached"] = False
            result["firestore_fallback"] = False
            result["source"] = "live"
            await signal_cache.set(asset, timeframe, result)
            save_latest_signal(asset, timeframe, result, source="live", is_demo=IS_DEMO)
            _maybe_append_history(asset, timeframe, result)
            return result
    except InvalidParameterError as e:
        raise HTTPException(status_code=400, detail="Invalid asset or timeframe")
    except Exception as e:
        logger.warning("Live signal fetch failed for %s %s: %s", asset, timeframe, e)

    # 4. Fallback: Firestore again (ignore TTL - last resort)
    fs_stale = get_latest_signal(asset, timeframe, max_age_sec=86400)
    if fs_stale:
        return fs_stale

    # 5. Fallback: return last memory cached (even if stale)
    stale = await signal_cache.get_stale(asset, timeframe)
    if stale:
        stale["source"] = "memory_cache"
        stale["firestore_fallback"] = False
        return stale

    raise HTTPException(
        status_code=503,
        detail="Signal unavailable. Server may be warming up. Please retry.",
    )


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
