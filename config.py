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

# Cache: TTL in seconds (10-20 to keep successful results longer)
CACHE_TTL_SEC = int(os.environ.get("SIGNAL_CACHE_TTL_SEC", "15"))

# Background refresh: interval in seconds
REFRESH_INTERVAL_SEC = int(os.environ.get("SIGNAL_REFRESH_INTERVAL_SEC", "5"))

# Delay between background fetches (avoid hammering PocketOption)
BACKGROUND_FETCH_DELAY_SEC = float(os.environ.get("SIGNAL_BACKGROUND_FETCH_DELAY_SEC", "1.5"))

# Default number of candles to fetch for signal calculation
DEFAULT_CANDLE_COUNT = int(os.environ.get("SIGNAL_DEFAULT_CANDLE_COUNT", "150"))

# Minimum candles required (26 for MACD, RSI)
MIN_CANDLES_FOR_SIGNAL = int(os.environ.get("SIGNAL_MIN_CANDLES", "26"))

# Signal quality: minimum score (90 = strict 4/4 indicators, 75 = 3/4)
SIGNAL_MIN_SCORE = int(os.environ.get("SIGNAL_MIN_SCORE", "90"))

# Tracked assets: start minimal (EURUSD_otc) to avoid timeouts
_TRACKED_STR = os.environ.get("SIGNAL_TRACKED_ASSETS", "").strip()
if _TRACKED_STR:
    TRACKED_ASSETS: List[str] = [a.strip() for a in _TRACKED_STR.split(",") if a.strip()]
else:
    TRACKED_ASSETS = ["EURUSD_otc"]

# Tracked timeframes: start with 1m only
_TRACKED_TF_STR = os.environ.get("SIGNAL_TRACKED_TIMEFRAMES", "").strip()
if _TRACKED_TF_STR:
    TRACKED_TIMEFRAMES: List[str] = [
        tf.strip() for tf in _TRACKED_TF_STR.split(",") if tf.strip()
    ]
else:
    TRACKED_TIMEFRAMES = ["1m"]

# Candle fetch timeout (seconds) - PocketOption can be slow, especially after cold start
CANDLE_TIMEOUT_SEC = float(os.environ.get("SIGNAL_CANDLE_TIMEOUT_SEC", "45"))

# Live calculation timeout in /signal request path
LIVE_REQUEST_TIMEOUT_SEC = float(os.environ.get("SIGNAL_LIVE_REQUEST_TIMEOUT_SEC", "10"))

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
