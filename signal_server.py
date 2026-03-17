# /Users/zakariaabdikarim/Desktop/nuunipay/PocketOptionAPI/signal_server.py
import os
import asyncio
from datetime import datetime
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from pocketoptionapi_async.client import AsyncPocketOptionClient
from pocketoptionapi_async.constants import TIMEFRAMES

PO_SSID = os.environ.get("PO_SSID")
if not PO_SSID:
    raise RuntimeError(
        "PO_SSID environment variable not set. "
        "Export your full auth string 42[\"auth\",{...}] into PO_SSID."
    )

app = FastAPI(title="PocketOption Bot Signals")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # adjust later if needed
    allow_methods=["*"],
    allow_headers=["*"],
)

client: AsyncPocketOptionClient | None = None
_client_lock = asyncio.Lock()


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


def decide_direction(df) -> tuple[Literal["buy", "sell", "neutral"], int, str]:
    if df is None or df.empty or len(df) < 3:
        return "neutral", 0, "Not enough data"

    # Use last 2 completed candles
    last_two = df.tail(3).iloc[:-1]
    c_prev = last_two.iloc[0]
    c_last = last_two.iloc[1]

    if c_last["close"] > c_prev["close"]:
        direction = "buy"
    elif c_last["close"] < c_prev["close"]:
        direction = "sell"
    else:
        direction = "neutral"

    # crude confidence based on body size
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


@app.get("/signal")
async def get_signal(
    asset: str = Query(..., description="PocketOption asset, e.g. EURUSD_otc"),
    timeframe: str = Query("1m", description="1m,5m,15m,1h,4h"),
    count: int = Query(60, ge=10, le=500),
):
    if timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail="Invalid timeframe")

    try:
        cli = await get_client()
        df = await cli.get_candles_dataframe(asset, timeframe, count=count, end_time=datetime.now())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PocketOption error: {e}")

    direction, confidence, message = decide_direction(df)

    return {
        "asset": asset,
        "timeframe": timeframe,
        "direction": direction,
        "confidence": confidence,
        "message": message,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)