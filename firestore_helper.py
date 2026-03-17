"""
Nuunipay Signals Bot - Firestore integration.
Stores latest signals and optional history. Safe fallback - never crashes API.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("nuunipay.firestore")

# Lazy init - only when enabled and credentials available
_firestore_client = None
_initialized = False
_enabled = False


def _doc_id(asset: str, timeframe: str) -> str:
    return f"{asset}_{timeframe}"


def _to_firestore_safe(value: Any) -> Any:
    """Convert values for Firestore (no datetime objects directly)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)) or value is None:
        return value
    if isinstance(value, dict):
        return {k: _to_firestore_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_firestore_safe(v) for v in value]
    return str(value)


def init_firestore(enabled: bool, credentials_json: str | None = None) -> bool:
    """
    Initialize Firestore. Safe - returns False if init fails.
    credentials_json: Full service account JSON as string, or None to use GOOGLE_APPLICATION_CREDENTIALS.
    """
    global _firestore_client, _initialized, _enabled

    if not enabled:
        _enabled = False
        _initialized = True
        return True

    if _initialized:
        return _firestore_client is not None

    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError:
        logger.warning("firebase-admin not installed. Firestore disabled.")
        _enabled = False
        _initialized = True
        return False

    try:
        if credentials_json and credentials_json.strip():
            cred_dict = json.loads(credentials_json)
            cred = credentials.Certificate(cred_dict)
        else:
            cred = credentials.ApplicationDefault()

        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(cred)

        _firestore_client = firestore.client()
        _enabled = True
        _initialized = True
        logger.info("Firestore initialized successfully")
        return True
    except Exception as e:
        logger.warning("Firestore init failed: %s. Continuing without Firestore.", e)
        _firestore_client = None
        _enabled = False
        _initialized = True
        return False


def is_enabled() -> bool:
    return _enabled and _firestore_client is not None


def save_latest_signal(
    asset: str,
    timeframe: str,
    signal: dict,
    source: str = "live",
    is_demo: bool = True,
) -> bool:
    """
    Save/update latest signal in latest_signals collection.
    Returns True on success, False on failure (never raises).
    """
    if not is_enabled():
        return False

    try:
        doc_id = _doc_id(asset, timeframe)
        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        doc = {
            "asset": asset,
            "timeframe": timeframe,
            "direction": signal.get("direction", "neutral"),
            "confidence": signal.get("confidence", 0),
            "message": signal.get("message", ""),
            "generated_at": signal.get("generated_at", now),
            "cached": signal.get("cached", False),
            "source": source,
            "updated_at": now,
            "is_demo": is_demo,
            "status": "ok",
        }
        doc = _to_firestore_safe(doc)

        _firestore_client.collection("latest_signals").document(doc_id).set(doc)
        return True
    except Exception as e:
        logger.warning("Firestore save_latest_signal failed: %s", e)
        return False


def get_latest_signal(
    asset: str,
    timeframe: str,
    max_age_sec: int = 300,
) -> dict | None:
    """
    Get latest signal from Firestore if fresh enough.
    Returns dict or None. Never raises.
    """
    if not is_enabled():
        return None

    try:
        doc_id = _doc_id(asset, timeframe)
        doc_ref = _firestore_client.collection("latest_signals").document(doc_id)
        snap = doc_ref.get()

        if not snap.exists:
            return None

        data = snap.to_dict()
        if not data:
            return None

        updated_at = data.get("updated_at")
        if updated_at:
            try:
                s = str(updated_at).replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if age > max_age_sec:
                    return None
            except Exception:
                pass

        return {
            "asset": data.get("asset", asset),
            "timeframe": data.get("timeframe", timeframe),
            "direction": data.get("direction", "neutral"),
            "confidence": data.get("confidence", 0),
            "message": data.get("message", ""),
            "generated_at": data.get("generated_at", ""),
            "cached": True,
            "firestore_fallback": True,
            "source": "firestore",
        }
    except Exception as e:
        logger.warning("Firestore get_latest_signal failed: %s", e)
        return None


def append_signal_history(
    asset: str,
    timeframe: str,
    signal: dict,
    source: str = "live",
    last_direction: str | None = None,
    last_confidence: int | None = None,
    confidence_delta_threshold: int = 15,
) -> bool:
    """
    Append to signal_history if direction changed or confidence changed significantly.
    Throttled - does not write every second.
    Returns True on success.
    """
    if not is_enabled():
        return False

    try:
        direction = signal.get("direction", "neutral")
        confidence = signal.get("confidence", 0)

        # Only write meaningful changes
        dir_changed = last_direction is not None and direction != last_direction
        conf_changed = (
            last_confidence is not None
            and abs(confidence - last_confidence) >= confidence_delta_threshold
        )
        if not dir_changed and not conf_changed:
            return False  # Skip write - no meaningful change

        now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "asset": asset,
            "timeframe": timeframe,
            "direction": direction,
            "confidence": confidence,
            "message": signal.get("message", ""),
            "generated_at": signal.get("generated_at", now),
            "cached": signal.get("cached", False),
            "source": source,
            "created_at": now,
        }
        record = _to_firestore_safe(record)

        _firestore_client.collection("signal_history").add(record)
        return True
    except Exception as e:
        logger.warning("Firestore append_signal_history failed: %s", e)
        return False
