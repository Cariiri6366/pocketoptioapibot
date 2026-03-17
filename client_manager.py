"""
Nuunipay Signals Bot - Single global PocketOption client manager.
- Keeps one client alive, no reconnect per request
- Reconnection handling if client disconnects
- Prevents overlapping connection attempts
"""
import asyncio
import logging
from typing import Optional

from pocketoptionapi_async.client import AsyncPocketOptionClient

from config import PO_SSID, IS_DEMO, RECONNECT_MAX_ATTEMPTS

logger = logging.getLogger("nuunipay.signals")

_client: Optional[AsyncPocketOptionClient] = None
_connection_lock = asyncio.Lock()


async def get_client() -> AsyncPocketOptionClient:
    """
    Return the single global PocketOption client.
    Creates and connects if needed. Reconnects if disconnected.
    Prevents overlapping connection attempts via lock.
    """
    global _client

    async with _connection_lock:
        if _client is not None and _client.is_connected:
            return _client

        try:
            if _client is not None:
                try:
                    await _client.disconnect()
                except Exception as e:
                    logger.warning("Disconnect during reconnect: %s", e)
                _client = None

            for attempt in range(1, RECONNECT_MAX_ATTEMPTS + 1):
                try:
                    _client = AsyncPocketOptionClient(
                        ssid=PO_SSID,
                        is_demo=IS_DEMO,
                        persistent_connection=True,
                        auto_reconnect=True,
                        enable_logging=False,
                    )
                    ok = await _client.connect()
                    if ok:
                        logger.info("PocketOption client connected (attempt %d)", attempt)
                        return _client
                except Exception as e:
                    logger.warning(
                        "Connection attempt %d/%d failed: %s",
                        attempt,
                        RECONNECT_MAX_ATTEMPTS,
                        e,
                    )
                    if attempt < RECONNECT_MAX_ATTEMPTS:
                        await asyncio.sleep(2 + attempt)

            raise RuntimeError(
                "Failed to connect to PocketOption after %d attempts" % RECONNECT_MAX_ATTEMPTS
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("get_client failed: %s", e)
            raise


def is_connected() -> bool:
    """Check if client exists and is connected."""
    return _client is not None and _client.is_connected


async def ensure_connected() -> bool:
    """Ensure client is connected; attempt reconnect if not. Returns True if connected."""
    try:
        await get_client()
        return True
    except Exception as e:
        logger.error("ensure_connected failed: %s", e)
        return False
