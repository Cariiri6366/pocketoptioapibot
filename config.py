"""
Nuunipay Signals Bot - Centralized configuration.
All timing and asset settings are configurable via environment variables.
"""
import os
from typing import List

# Required: PocketOption authentication
PO_SSID = os.environ.get("PO_SSID", "")

# Demo mode: 1 = demo, 0 = live
IS_DEMO = os.environ.get("PO_IS_DEMO", "1").strip().lower() in ("1", "true", "yes")

# Cache: TTL in seconds (5-10 for instant responses)
CACHE_TTL_SEC = int(os.environ.get("SIGNAL_CACHE_TTL_SEC", "5"))

# Background refresh: interval in seconds (3-5 for professional bot feel)
REFRESH_INTERVAL_SEC = int(os.environ.get("SIGNAL_REFRESH_INTERVAL_SEC", "3"))

# Default number of candles to fetch for signal calculation
DEFAULT_CANDLE_COUNT = int(os.environ.get("SIGNAL_DEFAULT_CANDLE_COUNT", "150"))

# Minimum candles required to produce a signal
MIN_CANDLES_FOR_SIGNAL = 10

# Tracked assets: comma-separated list, or default (focused set for fast cycles)
_TRACKED_STR = os.environ.get("SIGNAL_TRACKED_ASSETS", "").strip()
if _TRACKED_STR:
    TRACKED_ASSETS: List[str] = [a.strip() for a in _TRACKED_STR.split(",") if a.strip()]
else:
    TRACKED_ASSETS = [
        "EURUSD_otc",
        "USDJPY_otc",
        "GBPUSD_otc",
    ]

# Tracked timeframes
_TRACKED_TF_STR = os.environ.get("SIGNAL_TRACKED_TIMEFRAMES", "1m,5m").strip()
TRACKED_TIMEFRAMES: List[str] = [
    tf.strip() for tf in _TRACKED_TF_STR.split(",") if tf.strip()
] or ["1m", "5m"]

# Candle request timeout (seconds) - PocketOption candle API can be slow after cold start
CANDLE_TIMEOUT_SEC = float(os.environ.get("SIGNAL_CANDLE_TIMEOUT_SEC", "30"))

# Live calculation timeout in request path - never block API for long
LIVE_REQUEST_TIMEOUT_SEC = float(os.environ.get("SIGNAL_LIVE_REQUEST_TIMEOUT_SEC", "3"))

# Reconnection: max attempts when connection is lost
RECONNECT_MAX_ATTEMPTS = int(os.environ.get("SIGNAL_RECONNECT_ATTEMPTS", "3"))

# ---------------------------------------------------------------------------
# Firestore (optional)
# ---------------------------------------------------------------------------
ENABLE_FIRESTORE = os.environ.get("ENABLE_FIRESTORE", "false").strip().lower() in ("true", "1", "yes")
FIRESTORE_WRITE_HISTORY = os.environ.get("FIRESTORE_WRITE_HISTORY", "true").strip().lower() in ("true", "1", "yes")
HISTORY_WRITE_INTERVAL_SEC = int(os.environ.get("HISTORY_WRITE_INTERVAL_SEC", "60"))
FIRESTORE_LATEST_TTL_SEC = int(os.environ.get("FIRESTORE_LATEST_TTL_SEC", "300"))
# Service account JSON as string (from env) or path via GOOGLE_APPLICATION_CREDENTIALS
FIREBASE_SERVICE_ACCOUNT_JSON = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
