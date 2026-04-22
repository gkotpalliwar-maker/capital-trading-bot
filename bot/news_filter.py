import logging
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

import requests

logger = logging.getLogger("news_filter")

# Config
NEWS_ENABLED = os.environ.get("NEWS_FILTER_ENABLED", "false").lower() == "true"
NEWS_BLOCK_BEFORE = int(os.environ.get("NEWS_BLOCK_MINUTES_BEFORE", "30"))
NEWS_BLOCK_AFTER = int(os.environ.get("NEWS_BLOCK_MINUTES_AFTER", "15"))
NEWS_REQUIRED = os.environ.get("NEWS_FILTER_REQUIRED", "false").lower() == "true"
NEWS_CONFLUENCE_PENALTY = int(os.environ.get("NEWS_CONFLUENCE_PENALTY", "3"))

FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_FILE = DATA_DIR / "news_cache.json"
CACHE_TTL = 6 * 3600

_cache_lock = Lock()
_events_cache = []
_cache_time = 0
_guard_active = False

INSTRUMENT_CURRENCIES = {
    "EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"],
    "USDJPY": ["USD", "JPY"], "AUDUSD": ["AUD", "USD"],
    "NZDUSD": ["NZD", "USD"], "USDCAD": ["USD", "CAD"],
    "USDCHF": ["USD", "CHF"],
    "GOLD": ["USD", "XAU"], "SILVER": ["USD", "XAG"],
    "OIL_CRUDE": ["USD", "OIL"],
    "US100": ["USD"], "US500": ["USD"], "US30": ["USD"],
    "BTCUSD": ["USD", "BTC"], "ETHUSD": ["USD", "ETH"],
}

INSTRUMENT_KEYWORDS = {
    "OIL_CRUDE": ["crude", "oil", "opec", "eia", "api", "petroleum", "energy", "barrel"],
    "GOLD": ["gold", "precious", "bullion", "xau"],
    "SILVER": ["silver", "xag"],
    "US100": ["nasdaq", "tech", "earnings"],
    "US500": ["s&p", "earnings", "gdp"],
    "BTCUSD": ["crypto", "bitcoin", "regulation", "sec"],
    "ETHUSD": ["crypto", "ethereum", "regulation", "sec"],
}

NEWS_SENSITIVITY = {
    "OIL_CRUDE": 1.5, "GOLD": 1.3, "USDJPY": 1.2,
    "US100": 1.2, "US500": 1.2, "EURUSD": 1.0,
    "GBPUSD": 1.0, "BTCUSD": 0.8, "ETHUSD": 0.7,
}


def _fetch_calendar():
    global _events_cache, _cache_time
    try:
        resp = requests.get(FF_URL, timeout=10)
        resp.raise_for_status()
        raw = resp.json()
        events = []
        for ev in raw:
            title = ev.get("title", "")
            country = ev.get("country", "")
            dt_str = ev.get("date", "")
            impact = ev.get("impact", "Low")
            try:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            currency = _country_to_currency(country)
            events.append({
                "title": title, "country": country, "currency": currency,
                "datetime": dt.isoformat(), "impact": impact,
                "forecast": ev.get("forecast", ""), "previous": ev.get("previous", ""),
            })
        _events_cache = events
        _cache_time = time.time()
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w") as f:
                json.dump({"events": events, "fetched": _cache_time}, f)
        except Exception:
            pass
        logger.info(f"Fetched {len(events)} news events from ForexFactory")
        return events
    except Exception as e:
        logger.warning(f"Failed to fetch calendar: {e}")
        return _load_disk_cache()


def _load_disk_cache():
    global _events_cache, _cache_time
    try:
        if CACHE_FILE.exists():
            with open(CACHE_FILE) as f:
                data = json.load(f)
            _events_cache = data.get("events", [])
            _cache_time = data.get("fetched", 0)
            return _events_cache
    except Exception:
        pass
    return []


def _country_to_currency(country):
    mapping = {
        "USD": "USD", "EUR": "EUR", "GBP": "GBP", "JPY": "JPY",
        "AUD": "AUD", "NZD": "NZD", "CAD": "CAD", "CHF": "CHF",
        "CNY": "CNY", "All": "ALL",
    }
    return mapping.get(country, country)


def get_events(refresh=False):
    global _events_cache, _cache_time
    with _cache_lock:
        if refresh or (time.time() - _cache_time > CACHE_TTL) or not _events_cache:
            _fetch_calendar()
    return _events_cache


def get_upcoming_events(hours=24, impact_filter=None):
    events = get_events()
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    upcoming = []
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev["datetime"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if now - timedelta(minutes=NEWS_BLOCK_AFTER) <= dt <= cutoff:
            if impact_filter == "High" and ev["impact"] != "High":
                continue
            if impact_filter == "Medium" and ev["impact"] not in ("High", "Medium"):
                continue
            ev_copy = dict(ev)
            ev_copy["minutes_away"] = int((dt - now).total_seconds() / 60)
            upcoming.append(ev_copy)
    upcoming.sort(key=lambda e: e["minutes_away"])
    return upcoming


def check_news_risk(epic):
    if not NEWS_ENABLED and not _guard_active:
        return "clear", [], "News filter disabled"
    currencies = INSTRUMENT_CURRENCIES.get(epic, ["USD"])
    keywords = INSTRUMENT_KEYWORDS.get(epic, [])
    sensitivity = NEWS_SENSITIVITY.get(epic, 1.0)
    events = get_events()
    now = datetime.now(timezone.utc)
    before_window = timedelta(minutes=int(NEWS_BLOCK_BEFORE * sensitivity))
    after_window = timedelta(minutes=NEWS_BLOCK_AFTER)
    relevant = []
    highest_impact = "Low"
    for ev in events:
        try:
            dt = datetime.fromisoformat(ev["datetime"])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if not (now - after_window <= dt <= now + before_window):
            continue
        is_relevant = False
        if ev["currency"] in currencies or ev["currency"] == "ALL":
            is_relevant = True
        title_lower = ev["title"].lower()
        if any(kw in title_lower for kw in keywords):
            is_relevant = True
        if is_relevant:
            mins = int((dt - now).total_seconds() / 60)
            ev_copy = dict(ev)
            ev_copy["minutes_away"] = mins
            relevant.append(ev_copy)
            if ev["impact"] == "High":
                highest_impact = "High"
            elif ev["impact"] == "Medium" and highest_impact != "High":
                highest_impact = "Medium"
    if not relevant:
        return "clear", [], "No upcoming news events"
    if highest_impact == "High":
        titles = ", ".join(e["title"] for e in relevant[:3])
        return "blocked", relevant, f"High-impact: {titles}"
    elif highest_impact == "Medium":
        titles = ", ".join(e["title"] for e in relevant[:3])
        return "caution", relevant, f"Medium-impact: {titles}"
    return "clear", relevant, "Low-impact events only"


def check_volatility_guard(epic, current_atr, avg_atr, threshold=2.0):
    if avg_atr == 0:
        return False, 0, "No ATR data"
    ratio = current_atr / avg_atr
    if ratio >= threshold:
        return True, round(ratio, 2), f"ATR spike: {ratio:.1f}x normal"
    return False, round(ratio, 2), f"ATR normal: {ratio:.1f}x"


def activate_guard():
    global _guard_active
    _guard_active = True
    get_events(refresh=True)
    logger.info("News guard ACTIVATED")

def deactivate_guard():
    global _guard_active
    _guard_active = False
    logger.info("News guard DEACTIVATED")

def is_guard_active():
    return _guard_active

def get_guard_status():
    return {
        "active": _guard_active,
        "news_enabled": NEWS_ENABLED,
        "events_cached": len(_events_cache),
        "cache_age_min": int((time.time() - _cache_time) / 60) if _cache_time > 0 else -1,
        "block_before": NEWS_BLOCK_BEFORE,
        "block_after": NEWS_BLOCK_AFTER,
        "required": NEWS_REQUIRED,
        "penalty": NEWS_CONFLUENCE_PENALTY,
    }
