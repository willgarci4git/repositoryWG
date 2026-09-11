"""Persistência simples de alertas de preço em JSON (sem dependências externas)."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")

_lock = threading.Lock()


def _ensure_store() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)


def _read_all() -> list[dict]:
    _ensure_store()
    with open(ALERTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_all(alerts: list[dict]) -> None:
    _ensure_store()
    with open(ALERTS_FILE, "w", encoding="utf-8") as f:
        json.dump(alerts, f, indent=2, ensure_ascii=False)


def add_alert(ticker: str, target_price: float, direction: str, note: str = "") -> dict:
    """direction: 'above' (dispara quando preço >= target) ou 'below' (<= target)."""
    if direction not in ("above", "below"):
        raise ValueError("direction deve ser 'above' ou 'below'")

    alert = {
        "id": uuid.uuid4().hex[:8],
        "ticker": ticker.strip().upper(),
        "target_price": float(target_price),
        "direction": direction,
        "note": note,
        "status": "active",  # active | triggered | removed
        "created_at": datetime.now(timezone.utc).isoformat(),
        "triggered_at": None,
        "last_checked_price": None,
    }
    with _lock:
        alerts = _read_all()
        alerts.append(alert)
        _write_all(alerts)
    return alert


def list_alerts(status: Optional[str] = None) -> list[dict]:
    with _lock:
        alerts = _read_all()
    if status:
        return [a for a in alerts if a["status"] == status]
    return alerts


def remove_alert(alert_id: str) -> bool:
    with _lock:
        alerts = _read_all()
        found = False
        for a in alerts:
            if a["id"] == alert_id and a["status"] != "removed":
                a["status"] = "removed"
                found = True
        _write_all(alerts)
    return found


def mark_triggered(alert_id: str, price: float) -> None:
    with _lock:
        alerts = _read_all()
        for a in alerts:
            if a["id"] == alert_id:
                a["status"] = "triggered"
                a["triggered_at"] = datetime.now(timezone.utc).isoformat()
                a["last_checked_price"] = price
        _write_all(alerts)


def update_last_checked(alert_id: str, price: Optional[float]) -> None:
    if price is None:
        return
    with _lock:
        alerts = _read_all()
        for a in alerts:
            if a["id"] == alert_id:
                a["last_checked_price"] = price
        _write_all(alerts)
