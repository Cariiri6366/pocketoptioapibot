"""
Nuunipay Signals Bot - Signal generation logic.
Stronger strategy: last completed candles, body strength, short trend, momentum.
Returns direction (buy/sell/neutral), confidence (0-100), and human-readable message.
"""
from typing import Literal, Tuple

# Type alias for signal result
SignalResult = Tuple[Literal["buy", "sell", "neutral"], int, str]


def _safe_float(val, default: float = 0.0) -> float:
    """Safely convert to float."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _validate_dataframe(df) -> bool:
    """Validate that dataframe has required OHLC columns and enough rows."""
    if df is None:
        return False
    try:
        required = ["open", "high", "low", "close"]
        if not all(col in df.columns for col in required):
            return False
        if len(df) < 10:  # Minimum for meaningful analysis
            return False
        # Check for NaN/inf
        for col in required:
            if df[col].isna().all() or (df[col] == 0).all():
                return False
        return True
    except Exception:
        return False


def compute_signal(df, min_candles: int = 10) -> SignalResult:
    """
    Compute trading signal from OHLC dataframe.
    Uses: last completed candles, body strength, short trend, recent momentum.
    Returns (direction, confidence, message).
    Only returns "Not enough data" when data is truly insufficient.
    """
    if not _validate_dataframe(df):
        return "neutral", 0, "Not enough data"

    n = len(df)
    if n < min_candles:
        return "neutral", 0, "Not enough data"

    try:
        # Use last completed candles (exclude current forming candle if needed)
        # Typically last row might be forming - use last 2-3 completed for direction
        recent = df.tail(min(30, n))
        last_completed = recent.iloc[:-1] if len(recent) > 1 else recent
        if len(last_completed) < 3:
            last_completed = recent

        # Last two completed candles for immediate direction
        c_prev = last_completed.iloc[-2]
        c_last = last_completed.iloc[-1]

        o_prev = _safe_float(c_prev["open"])
        c_prev_val = _safe_float(c_prev["close"])
        o_last = _safe_float(c_last["open"])
        c_last_val = _safe_float(c_last["close"])
        h_last = _safe_float(c_last["high"])
        l_last = _safe_float(c_last["low"])

        # 1. Candle-to-candle direction
        if c_last_val > c_prev_val:
            base_direction: Literal["buy", "sell", "neutral"] = "buy"
        elif c_last_val < c_prev_val:
            base_direction = "sell"
        else:
            base_direction = "neutral"

        # 2. Candle body strength (last candle)
        body = abs(c_last_val - o_last)
        candle_range = h_last - l_last if (h_last - l_last) > 1e-10 else 1e-10
        body_ratio = body / candle_range  # 0-1, higher = stronger body

        # 3. Short-term trend (last 5-10 candles)
        first_trend = _safe_float(recent.iloc[0]["close"])
        last_trend = _safe_float(recent.iloc[-1]["close"])
        trend_bullish = last_trend > first_trend

        # 4. Momentum: recent closes vs older
        mid = max(1, len(recent) // 2)
        older_avg = _safe_float(recent.iloc[:mid]["close"].mean())
        newer_avg = _safe_float(recent.iloc[mid:]["close"].mean())
        momentum_bullish = newer_avg > older_avg

        # 5. Average body size for relative strength
        bodies = (recent["close"] - recent["open"]).abs()
        avg_body = float(bodies.mean()) if len(bodies) > 0 else 1e-10
        if avg_body < 1e-10:
            avg_body = 1e-10
        last_body = abs(c_last_val - o_last)
        body_strength_ratio = last_body / avg_body

        # Combine signals
        conf = 50
        reasons = []

        if base_direction != "neutral":
            conf += 10
            reasons.append(f"Last candle {base_direction.upper()}")

        if body_ratio >= 0.6:
            conf += 12
            reasons.append("strong body")
        elif body_ratio >= 0.4:
            conf += 6
            reasons.append("moderate body")

        if body_strength_ratio >= 1.5:
            conf += 10
            reasons.append("above-average momentum")
        elif body_strength_ratio >= 1.0:
            conf += 5

        if (base_direction == "buy" and trend_bullish) or (
            base_direction == "sell" and not trend_bullish
        ):
            conf += 8
            reasons.append("trend aligned")
        elif (base_direction == "buy" and momentum_bullish) or (
            base_direction == "sell" and not momentum_bullish
        ):
            conf += 5
            reasons.append("momentum aligned")

        # Clamp confidence
        conf = min(95, max(35, conf))
        if base_direction == "neutral":
            conf = min(45, conf)
            msg = "Sideways movement, no clear direction"
        else:
            msg = f"{base_direction.capitalize()} signal: " + ", ".join(reasons[:3])

        return base_direction, conf, msg

    except Exception:
        return "neutral", 0, "Not enough data"
