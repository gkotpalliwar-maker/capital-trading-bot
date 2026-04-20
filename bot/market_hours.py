import logging
from datetime import datetime, timezone

logger = logging.getLogger("market_hours")

# Market categories by Capital.com epic
MARKET_CATEGORY = {
    "BTCUSD": "crypto", "ETHUSD": "crypto",
    "EURUSD": "forex", "GBPUSD": "forex", "USDJPY": "forex",
    "AUDUSD": "forex", "NZDUSD": "forex", "USDCAD": "forex", "USDCHF": "forex",
    "GOLD": "commodity", "SILVER": "commodity", "OIL_CRUDE": "commodity",
    "US100": "index", "US500": "index", "US30": "index",
}

def is_market_open(epic: str, utc_now: datetime = None) -> tuple:
    """Check if market is open for the given instrument.
    Returns (is_open: bool, reason: str)
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)
    cat = MARKET_CATEGORY.get(epic, "forex")
    weekday = utc_now.weekday()  # 0=Mon, 6=Sun
    hour = utc_now.hour

    # Crypto: 24/7 (always open, but flag low weekend volume)
    if cat == "crypto":
        if weekday >= 5:  # Sat/Sun
            return True, "open (low weekend volume)"
        return True, "open"

    # Forex/Commodity/Index: closed from Fri 22:00 UTC to Sun 22:00 UTC
    if weekday == 4 and hour >= 22:  # Friday after 22:00
        return False, f"{cat} market closed (weekend)"
    if weekday == 5:  # Saturday
        return False, f"{cat} market closed (weekend)"
    if weekday == 6 and hour < 22:  # Sunday before 22:00
        return False, f"{cat} market closed (weekend)"

    # Index-specific: additional daily breaks (US market hours ~14:30-21:00 UTC)
    # Keeping it simple - main close is weekends, intraday breaks are minor
    return True, "open"

def get_scannable_instruments(instrument_map: dict, utc_now: datetime = None) -> list:
    """Filter instruments to only those with open markets.
    Returns list of (instrument_name, epic) tuples that are open.
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)
    scannable = []
    skipped = []
    for name, epic in instrument_map.items():
        is_open, reason = is_market_open(epic, utc_now)
        if is_open:
            scannable.append((name, epic, reason))
        else:
            skipped.append((name, epic, reason))
    if skipped:
        logger.info("Market closed: %s", ", ".join(f"{n} ({r})" for n, _, r in skipped))
    return scannable
