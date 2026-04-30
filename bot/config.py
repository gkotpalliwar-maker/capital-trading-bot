"""Capital.com Trading Bot v2.1 - Configuration"""
import os
from enum import Enum
from typing import List, Dict
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

CAPITAL_API_URL = os.getenv("CAPITAL_API_URL", "https://api-capital.backend-capital.com")
CAPITAL_API_KEY = os.getenv("CAPITAL_API_KEY", "")
CAPITAL_EMAIL = os.getenv("CAPITAL_EMAIL", "")
CAPITAL_PASSWORD = os.getenv("CAPITAL_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "900"))
MAX_SCAN_ROUNDS = int(os.getenv("MAX_SCAN_ROUNDS", "0"))  # 0 = infinite

# ---- Risk Management ----
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS", "10.0"))         # Max daily loss in account currency
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "5"))            # Max concurrent open trades
MAX_TRADES_PER_INSTRUMENT = int(os.getenv("MAX_TRADES_PER_INSTRUMENT", "1"))
SIGNAL_EXPIRY_SEC = int(os.getenv("SIGNAL_EXPIRY_SEC", "900"))      # Signal expires after 15 min
MAX_PRICE_DRIFT_PCT = float(os.getenv("MAX_PRICE_DRIFT_PCT", "0.3"))  # Max % drift from signal entry
MAX_SPREAD_MULTIPLIER = float(os.getenv("MAX_SPREAD_MULTIPLIER", "5.0"))  # Max spread vs normal
COOLDOWN_AFTER_LOSSES = int(os.getenv("COOLDOWN_AFTER_LOSSES", "3"))  # Pause after N consecutive losses
COOLDOWN_MINUTES = float(os.getenv("COOLDOWN_MINUTES", "60"))        # Minutes to wait after cooldown
DEDUP_HOURS = float(os.getenv("DEDUP_HOURS", "2.0"))                 # Suppress duplicate signals within N hours

# v2.7.2: Per-timeframe dedup TTL (~2 candles per TF)
# Override via env: DEDUP_HOURS_M15, DEDUP_HOURS_H1, DEDUP_HOURS_H4
DEDUP_HOURS_MAP = {
    "M15": float(os.getenv("DEDUP_HOURS_M15", "0.5")),   # 30 min (2 x M15 candles)
    "H1":  float(os.getenv("DEDUP_HOURS_H1",  "2.0")),   # 2 hours (2 x H1 candles)
    "H4":  float(os.getenv("DEDUP_HOURS_H4",  "8.0")),   # 8 hours (2 x H4 candles)
}
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "4"))       # Every N scans send heartbeat

# ---- Position Sizing ----
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.02"))   # 2% risk per trade
MIN_POSITION_SIZE = {
    "OIL_CRUDE": 1, "GOLD": 0.01, "SILVER": 0.01,
    "BTCUSD": 0.001, "ETHUSD": 0.01,
    "EURUSD": 100, "GBPUSD": 100, "USDJPY": 100,
    "AUDUSD": 100, "NZDUSD": 100, "USDCAD": 100, "USDCHF": 100,
    "AUDCAD": 100,
    "US100": 0.01, "US500": 0.01, "US30": 0.01,
}
MAX_POSITION_SIZE = {
    "OIL_CRUDE": 1, "GOLD": 1, "SILVER": 10,
    "BTCUSD": 1, "ETHUSD": 10,
    "EURUSD": 50000, "GBPUSD": 50000, "USDJPY": 50000,
    "AUDUSD": 50000, "NZDUSD": 50000, "USDCAD": 50000, "USDCHF": 50000,
    "AUDCAD": 50000,
    "US100": 5, "US500": 5, "US30": 5,
}

# ---- Regime Filter ----
ADX_TREND_THRESHOLD = float(os.getenv("ADX_TREND_THRESHOLD", "25"))
ADX_RANGE_THRESHOLD = float(os.getenv("ADX_RANGE_THRESHOLD", "18"))
VOL_HIGH_MULTIPLIER = float(os.getenv("VOL_HIGH_MULTIPLIER", "1.5"))
VOL_LOW_MULTIPLIER = float(os.getenv("VOL_LOW_MULTIPLIER", "0.6"))

REGIME_RULES = {
    "trending": {
        "high":   {"allow_bos": True,  "allow_mss": True},
        "normal": {"allow_bos": True,  "allow_mss": False},
        "low":    {"allow_bos": True,  "allow_mss": False},
    },
    "ranging": {
        "high":   {"allow_bos": False, "allow_mss": True},
        "normal": {"allow_bos": False, "allow_mss": True},
        "low":    {"allow_bos": False, "allow_mss": False},
    },
    "weak_trend": {
        "high":   {"allow_bos": True,  "allow_mss": True},
        "normal": {"allow_bos": True,  "allow_mss": True},
        "low":    {"allow_bos": False, "allow_mss": False},
    },
}

# ---- Dashboard ----
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")

INSTRUMENT_MAP = {
    "crude": "OIL_CRUDE", "wti": "OIL_CRUDE", "gold": "GOLD", "silver": "SILVER",
    "btcusd": "BTCUSD", "ethusd": "ETHUSD",
    "eurusd": "EURUSD", "gbpusd": "GBPUSD", "usdjpy": "USDJPY",
    "audusd": "AUDUSD", "nzdusd": "NZDUSD", "usdcad": "USDCAD", "usdchf": "USDCHF",
    "audcad": "AUDCAD",
    "nas100": "US100", "spx500": "US500", "us30": "US30",
}
INSTRUMENT_DISPLAY = {v: k.upper() for k, v in INSTRUMENT_MAP.items()}
PIP_SIZE = {
    "OIL_CRUDE": 1, "GOLD": 0.01, "SILVER": 0.001,
    "BTCUSD": 1.0, "ETHUSD": 0.01,
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01,
    "AUDUSD": 0.0001, "NZDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
    "AUDCAD": 0.0001,
    "US100": 1.0, "US500": 0.1, "US30": 1.0,
}
DEFAULT_SIZE = {
    "OIL_CRUDE": 1, "GOLD": 0.01, "SILVER": 0.1,
    "BTCUSD": 0.01, "ETHUSD": 0.1,
    "EURUSD": 1000, "GBPUSD": 1000, "USDJPY": 1000,
    "AUDUSD": 1000, "NZDUSD": 1000, "USDCAD": 1000, "USDCHF": 1000,
    "AUDCAD": 1500,
    "US100": 0.1, "US500": 0.1, "US30": 0.1,
}
TIMEFRAME_MAP = {
    "M1": "MINUTE", "M5": "MINUTE_5", "M15": "MINUTE_15", "M30": "MINUTE_30",
    "H1": "HOUR", "H4": "HOUR_4", "D": "DAY", "W": "WEEK",
}
DEFAULT_INSTRUMENTS = ["gold", "crude", "eurusd", "gbpusd", "usdjpy", "btcusd", "ethusd", "nas100", "spx500"]
DEFAULT_TIMEFRAMES = ["M15", "H1", "H4"]
WINNING_ZONE_COMBOS = {"retrace+buy", "retrace+sell", "bos+buy", "bearish+mss", "bos+sell", "bullish+mss", "bearish+mss+sell"}

def resolve_instrument(name):
    return INSTRUMENT_MAP.get(name.lower(), name.upper())

def resolve_timeframe(tf):
    return TIMEFRAME_MAP.get(tf.upper(), tf)

class TradingSession(Enum):
    ASIAN = "Asian"
    EURO = "European"
    US = "US"
    OVERLAP_EURO_US = "Euro-US Overlap"
    OFF_HOURS = "Off Hours"

SESSION_TIMES = {
    TradingSession.ASIAN: {"start": 0, "end": 9},
    TradingSession.EURO: {"start": 7, "end": 16},
    TradingSession.US: {"start": 13, "end": 22},
    TradingSession.OVERLAP_EURO_US: {"start": 13, "end": 16},
}

def get_current_session(utc_hour=None):
    if utc_hour is None:
        utc_hour = datetime.now(timezone.utc).hour
    active = []
    for session, times in SESSION_TIMES.items():
        if times["start"] <= utc_hour < times["end"]:
            active.append(session)
    return active if active else [TradingSession.OFF_HOURS]


def get_session_for_time(dt):
    """Convert datetime to trading session list."""
    hour = dt.hour if hasattr(dt, "hour") else datetime.now(timezone.utc).hour
    return get_current_session(hour)
