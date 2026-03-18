"""
Nuunipay Signals Bot - Signal generation. ALWAYS returns buy or sell, never neutral.
Uses: RSI, EMA, MACD, candle momentum. Fallback rules when data is weak.
"""
from typing import Literal, Tuple

# Type alias - direction is always buy or sell
SignalResult = Tuple[Literal["buy", "sell"], int, str]


def _safe_float(val, default: float = 0.0) -> float:
    """Safely convert to float."""
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _validate_dataframe(df) -> bool:
    """Validate dataframe has OHLC columns and at least 1 row."""
    if df is None:
        return False
    try:
        required = ["open", "high", "low", "close"]
        if not all(col in df.columns for col in required):
            return False
        if len(df) < 1:
            return False
        for col in required:
            if df[col].isna().all() or (df[col] == 0).all():
                return False
        return True
    except Exception:
        return False


def _decide_direction_fallback(df) -> Tuple[Literal["buy", "sell"], int, str]:
    """
    Force a direction using fallback rules. NEVER returns neutral.
    Rules: last 2 candles -> last 5 trend -> EMA -> last candle body.
    """
    close = df["close"].astype(float)
    recent = df.tail(min(50, len(df)))

    # Rule 1: Last 2 completed candles
    if len(recent) >= 2:
        c_prev = _safe_float(recent.iloc[-2]["close"])
        c_last = _safe_float(recent.iloc[-1]["close"])
        if c_last > c_prev:
            return "buy", 70, "Fallback bullish direction selected"
        if c_last < c_prev:
            return "sell", 70, "Fallback bearish direction selected"

    # Rule 2: Last 5 candles - majority bullish/bearish
    if len(recent) >= 5:
        last5 = recent.tail(5)
        bullish = sum(1 for _, r in last5.iterrows() if _safe_float(r["close"]) > _safe_float(r["open"]))
        bearish = sum(1 for _, r in last5.iterrows() if _safe_float(r["close"]) < _safe_float(r["open"]))
        if bullish > bearish:
            return "buy", 68, "Fallback bullish direction selected"
        if bearish > bullish:
            return "sell", 68, "Fallback bearish direction selected"

    # Rule 3: EMA direction
    if len(close) >= 21:
        ema_fast = close.ewm(span=9, adjust=False).mean()
        ema_slow = close.ewm(span=21, adjust=False).mean()
        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:
            return "buy", 65, "Fallback bullish direction selected"
        if ema_fast.iloc[-1] < ema_slow.iloc[-1]:
            return "sell", 65, "Fallback bearish direction selected"

    # Rule 4: Latest candle body
    if len(recent) >= 1:
        r = recent.iloc[-1]
        o = _safe_float(r["open"])
        c = _safe_float(r["close"])
        if c > o:
            return "buy", 62, "Fallback bullish direction selected"
        if c < o:
            return "sell", 62, "Fallback bearish direction selected"

    # Rule 5: Absolute last resort - use close vs first
    if len(close) >= 1:
        if close.iloc[-1] >= close.iloc[0]:
            return "buy", 60, "Fallback bullish direction selected"
        return "sell", 60, "Fallback bearish direction selected"

    return "buy", 60, "Fallback bullish direction selected"


def _ema(series, period: int):
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close_series, period: int = 14) -> float | None:
    """Compute RSI."""
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


def _macd_signal(close_series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[bool | None, bool | None]:
    """MACD: bullish if MACD > signal line."""
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
    """Last 3 candles: bullish if majority close > open."""
    if len(recent_df) < 3:
        return None
    last3 = recent_df.tail(3)
    bullish = sum(1 for _, r in last3.iterrows() if _safe_float(r["close"]) > _safe_float(r["open"]))
    bearish = sum(1 for _, r in last3.iterrows() if _safe_float(r["close"]) < _safe_float(r["open"]))
    if bullish > bearish:
        return True
    if bearish > bullish:
        return False
    return None


def compute_signal(df, min_candles: int = 10) -> SignalResult:
    """
    Compute trading signal. ALWAYS returns buy or sell, never neutral.
    Strong confirmation: 90-100. Medium: 75-89. Weak fallback: 60-74.
    """
    if not _validate_dataframe(df):
        if df is not None and len(df) >= 1:
            try:
                return _decide_direction_fallback(df)
            except Exception:
                pass
        return "buy", 60, "Fallback bullish direction selected"

    n = len(df)
    if n < 2:
        return "buy", 60, "Fallback bullish direction selected"

    try:
        close = df["close"].astype(float)
        recent = df.tail(min(50, n))

        # RSI
        rsi_val = _rsi(close, 14)
        rsi_buy = rsi_val is not None and rsi_val < 30
        rsi_sell = rsi_val is not None and rsi_val > 70

        # EMA
        ema_fast = _ema(close, 9)
        ema_slow = _ema(close, 21)
        ema_fast_val = ema_fast.iloc[-1] if len(ema_fast) > 0 else None
        ema_slow_val = ema_slow.iloc[-1] if len(ema_slow) > 0 else None
        ema_bullish = ema_fast_val is not None and ema_slow_val is not None and ema_fast_val > ema_slow_val
        ema_bearish = ema_fast_val is not None and ema_slow_val is not None and ema_fast_val < ema_slow_val

        # MACD
        macd_bullish, macd_bearish = _macd_signal(close, 12, 26, 9)

        # Candle momentum
        momentum_bullish = _candle_momentum_bullish(recent)
        momentum_bearish = momentum_bullish is False
        momentum_bullish = momentum_bullish is True

        # Score
        buy_score = 0
        sell_score = 0
        if rsi_buy:
            buy_score += 25
        elif rsi_sell:
            sell_score += 25
        if ema_bullish:
            buy_score += 25
        elif ema_bearish:
            sell_score += 25
        if macd_bullish:
            buy_score += 25
        elif macd_bearish:
            sell_score += 25
        if momentum_bullish:
            buy_score += 25
        elif momentum_bearish:
            sell_score += 25

        # Strong: 90-100 (4/4 or 3/4 indicators)
        if buy_score >= 75 and buy_score > sell_score:
            conf = min(100, 90 + (buy_score - 75) // 5)
            return "buy", conf, "Bullish momentum detected"
        if sell_score >= 75 and sell_score > buy_score:
            conf = min(100, 90 + (sell_score - 75) // 5)
            return "sell", conf, "Bearish trend confirmed"

        # Medium: 75-89 (2/4 indicators)
        if buy_score >= 50 and buy_score > sell_score:
            conf = min(89, 75 + (buy_score - 50) // 2)
            return "buy", conf, "Bullish momentum detected"
        if sell_score >= 50 and sell_score > buy_score:
            conf = min(89, 75 + (sell_score - 50) // 2)
            return "sell", conf, "Bearish trend confirmed"

        # Weak: use fallback rules (60-74)
        return _decide_direction_fallback(df)

    except Exception:
        return _decide_direction_fallback(df) if df is not None and len(df) >= 2 else ("buy", 60, "Fallback bullish direction selected")
