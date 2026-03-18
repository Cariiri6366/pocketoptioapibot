"""
Nuunipay Signals Bot - High-confidence signal generation.
Returns ONLY strong BUY or SELL signals (90-100% confidence).
Uses: RSI (<30 buy, >70 sell), EMA trend, MACD, candle momentum.
"""
from typing import Literal, Tuple

# Type alias for signal result
SignalResult = Tuple[Literal["buy", "sell", "neutral"], int, str]

# Minimum score to return a signal (from config, default 90 = strict)
def _min_score() -> int:
    try:
        from config import SIGNAL_MIN_SCORE
        return SIGNAL_MIN_SCORE
    except ImportError:
        return 90

# Output confidence range (always 90-100 when we return BUY/SELL)
MIN_OUTPUT_CONFIDENCE = 90
MAX_OUTPUT_CONFIDENCE = 100

# Indicator weights (each contributes up to 25%)
WEIGHT_RSI = 25
WEIGHT_EMA = 25
WEIGHT_MACD = 25
WEIGHT_MOMENTUM = 25


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
        if len(df) < 26:  # Need enough for MACD (26-period)
            return False
        for col in required:
            if df[col].isna().all() or (df[col] == 0).all():
                return False
        return True
    except Exception:
        return False


def _rsi(close_series, period: int = 14) -> float | None:
    """Compute RSI. Returns None if insufficient data."""
    if len(close_series) < period + 1:
        return None
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1]) if not rsi.empty and not rsi.isna().iloc[-1] else None


def _ema(series, period: int):
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def _macd_signal(close_series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[bool | None, bool | None]:
    """
    MACD: (fast_ema - slow_ema), signal line.
    Returns (is_bullish, is_bearish). None if insufficient data.
    Bullish: MACD line > signal line.
    """
    if len(close_series) < slow + signal:
        return None, None
    ema_fast = _ema(close_series, fast)
    ema_slow = _ema(close_series, slow)
    macd_line = ema_fast - ema_slow
    macd_signal_line = _ema(macd_line, signal)
    macd_val = macd_line.iloc[-1]
    sig_val = macd_signal_line.iloc[-1]
    if macd_val is None or sig_val is None or (macd_val != macd_val) or (sig_val != sig_val):
        return None, None
    return macd_val > sig_val, macd_val < sig_val


def _candle_momentum_bullish(recent_df) -> bool | None:
    """Last 3 candles: bullish if majority close higher than open."""
    if len(recent_df) < 3:
        return None
    last3 = recent_df.tail(3)
    bullish_count = sum(1 for _, r in last3.iterrows() if _safe_float(r["close"]) > _safe_float(r["open"]))
    bearish_count = sum(1 for _, r in last3.iterrows() if _safe_float(r["close"]) < _safe_float(r["open"]))
    if bullish_count > bearish_count:
        return True
    if bearish_count > bullish_count:
        return False
    return None  # Tie


def _ignore_small_movement(close_series, threshold_pct: float = 0.05) -> bool:
    """True if recent price movement is significant (not noise)."""
    if len(close_series) < 5:
        return False
    recent = close_series.tail(5)
    low = recent.min()
    high = recent.max()
    if low <= 0:
        return False
    range_pct = (high - low) / low * 100
    return range_pct >= threshold_pct


def compute_signal(df, min_candles: int = 26) -> SignalResult:
    """
    Compute high-confidence trading signal.
    Only returns BUY/SELL when score >= 90.
    Uses: RSI (<30 buy, >70 sell), EMA trend, MACD, candle momentum.
    """
    if not _validate_dataframe(df):
        return "neutral", 0, "Not enough data"

    n = len(df)
    if n < min_candles:
        return "neutral", 0, "Not enough data"

    try:
        close = df["close"].astype(float)
        recent = df.tail(min(50, n))

        # Ignore small movements (noise)
        if not _ignore_small_movement(close):
            return "neutral", 0, "No strong signal"

        # 1. RSI (25%)
        rsi_val = _rsi(close, 14)
        rsi_buy = rsi_val is not None and rsi_val < 30
        rsi_sell = rsi_val is not None and rsi_val > 70

        # 2. EMA trend (25%): fast 9, slow 21
        ema_fast = _ema(close, 9)
        ema_slow = _ema(close, 21)
        ema_fast_val = ema_fast.iloc[-1] if len(ema_fast) > 0 else None
        ema_slow_val = ema_slow.iloc[-1] if len(ema_slow) > 0 else None
        ema_bullish = ema_fast_val is not None and ema_slow_val is not None and ema_fast_val > ema_slow_val
        ema_bearish = ema_fast_val is not None and ema_slow_val is not None and ema_fast_val < ema_slow_val

        # 3. MACD (25%)
        macd_bullish, macd_bearish = _macd_signal(close, 12, 26, 9)

        # 4. Candle momentum - last 3 candles (25%)
        momentum_bullish = _candle_momentum_bullish(recent)
        momentum_bearish = momentum_bullish is False
        momentum_bullish = momentum_bullish is True

        # Score BUY and SELL separately
        buy_score = 0
        sell_score = 0

        if rsi_buy:
            buy_score += WEIGHT_RSI
        elif rsi_sell:
            sell_score += WEIGHT_RSI

        if ema_bullish:
            buy_score += WEIGHT_EMA
        elif ema_bearish:
            sell_score += WEIGHT_EMA

        if macd_bullish:
            buy_score += WEIGHT_MACD
        elif macd_bearish:
            sell_score += WEIGHT_MACD

        if momentum_bullish:
            buy_score += WEIGHT_MOMENTUM
        elif momentum_bearish:
            sell_score += WEIGHT_MOMENTUM

        min_score = _min_score()
        # Only return signal if score >= min_score
        # Scale output confidence to 90-100
        if buy_score >= min_score and buy_score > sell_score:
            # 75->90, 100->100
            denom = max(1, 100 - min_score)
            conf = int(MIN_OUTPUT_CONFIDENCE + (buy_score - min_score) / denom * (MAX_OUTPUT_CONFIDENCE - MIN_OUTPUT_CONFIDENCE))
            conf = min(MAX_OUTPUT_CONFIDENCE, max(MIN_OUTPUT_CONFIDENCE, conf))
            msg = "Strong bullish trend confirmed"
            if rsi_buy:
                msg += " (RSI oversold)"
            if ema_bullish:
                msg += " (EMA aligned)"
            if macd_bullish:
                msg += " (MACD bullish)"
            return "buy", conf, msg

        if sell_score >= min_score and sell_score > buy_score:
            denom = max(1, 100 - min_score)
            conf = int(MIN_OUTPUT_CONFIDENCE + (sell_score - min_score) / denom * (MAX_OUTPUT_CONFIDENCE - MIN_OUTPUT_CONFIDENCE))
            conf = min(MAX_OUTPUT_CONFIDENCE, max(MIN_OUTPUT_CONFIDENCE, conf))
            msg = "Strong bearish trend confirmed"
            if rsi_sell:
                msg += " (RSI overbought)"
            if ema_bearish:
                msg += " (EMA aligned)"
            if macd_bearish:
                msg += " (MACD bearish)"
            return "sell", conf, msg

        # No strong signal
        return "neutral", 0, "No strong signal"

    except Exception:
        return "neutral", 0, "Not enough data"
