# bot/market_intelligence.py — v2.8.0
# External intelligence: COT, TradingView, Fear & Greed, Volatility
from __future__ import annotations

import os
import json
import time
import sqlite3
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger("market_intelligence")

# ============================================================
# INSTRUMENT MAPPINGS
# ============================================================

# COT: CFTC contract codes mapped to our Capital.com instruments
COT_INSTRUMENT_MAP = {
    "gold":    {"cftc_code": "088691", "name": "GOLD"},
    "crude":   {"cftc_code": "067651", "name": "CRUDE OIL, LIGHT SWEET"},
    "eurusd":  {"cftc_code": "099741", "name": "EURO FX"},
    "gbpusd":  {"cftc_code": "096742", "name": "BRITISH POUND"},
    "usdjpy":  {"cftc_code": "097741", "name": "JAPANESE YEN"},
    "us500":   {"cftc_code": "13874A", "name": "E-MINI S&P 500"},
    "spx500":  {"cftc_code": "13874A", "name": "E-MINI S&P 500"},  # alias for us500
    "nas100":  {"cftc_code": "209742", "name": "E-MINI NASDAQ-100"},
    "btcusd":  {"cftc_code": "133741", "name": "BITCOIN"},
    "ethusd":  {"cftc_code": "244601", "name": "ETHER"},
}

# TradingView: exchange + symbol per instrument
TV_INSTRUMENT_MAP = {
    "gold":    {"exchange": "OANDA",  "symbol": "XAUUSD"},
    "crude":   {"exchange": "TVC",    "symbol": "USOIL"},
    "eurusd":  {"exchange": "OANDA",  "symbol": "EURUSD"},
    "gbpusd":  {"exchange": "OANDA",  "symbol": "GBPUSD"},
    "usdjpy":  {"exchange": "OANDA",  "symbol": "USDJPY"},
    "us500":   {"exchange": "OANDA",  "symbol": "SPX500USD"},
    "spx500":  {"exchange": "OANDA",  "symbol": "SPX500USD"},  # alias for us500
    "nas100":  {"exchange": "OANDA",  "symbol": "NAS100USD"},
    "btcusd":  {"exchange": "BITSTAMP", "symbol": "BTCUSD"},
    "ethusd":  {"exchange": "BITSTAMP", "symbol": "ETHUSD"},
}

# TradingView intervals mapped to our timeframes
TV_INTERVAL_MAP = {
    "M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
    "H1": "1h", "H4": "4h", "D1": "1d", "W1": "1W",
}


class MarketIntelligence:
    """Fetches and caches external market intelligence data."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "bot.db"
        )
        self._init_tables()
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "TradingBot/2.8.0"})
        # Cache TTLs (seconds)
        self.COT_CACHE_TTL = 86400 * 2      # 2 days (COT updates weekly)
        self.TV_CACHE_TTL = 900              # 15 min (per scan cycle)
        self.FG_CACHE_TTL = 3600 * 4         # 4 hours
        self.VOLATILITY_CACHE_TTL = 300      # 5 min (recomputed per scan)
        # TradingView availability flag
        self._tv_available = None

    def _init_tables(self):
        """Create intelligence cache tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intel_cache (
                    cache_key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cot_positions (
                    instrument TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    commercial_long INTEGER,
                    commercial_short INTEGER,
                    commercial_net INTEGER,
                    large_spec_long INTEGER,
                    large_spec_short INTEGER,
                    large_spec_net INTEGER,
                    small_spec_long INTEGER,
                    small_spec_short INTEGER,
                    small_spec_net INTEGER,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (instrument, report_date)
                )
            """)
            conn.commit()

    # ============================================================
    # CACHE HELPERS
    # ============================================================
    def _get_cached(self, key: str) -> Optional[Dict]:
        """Return cached data if not expired, else None."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT data_json, expires_at FROM intel_cache WHERE cache_key = ?",
                (key,)
            ).fetchone()
        if not row:
            return None
        expires = datetime.fromisoformat(row[1])
        if datetime.now(timezone.utc) > expires:
            return None
        return json.loads(row[0])

    def _set_cached(self, key: str, data: Dict, ttl_seconds: int):
        """Store data in cache with TTL."""
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO intel_cache (cache_key, data_json, fetched_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (key, json.dumps(data), now.isoformat(), expires.isoformat())
            )
            conn.commit()

    # ============================================================
    # A) COT DATA — Commitment of Traders
    # ============================================================
    def fetch_cot_data(self, instrument: str) -> Optional[Dict]:
        """
        Fetch latest COT positioning for an instrument.
        Returns: {bias, commercial_net, large_spec_net, report_date, ...}
        """
        cache_key = f"cot_{instrument}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        mapping = COT_INSTRUMENT_MAP.get(instrument)
        if not mapping:
            logger.warning(f"COT: No mapping for {instrument}")
            return None

        try:
            # CFTC Disaggregated Futures API (Socrata open data)
            url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json"
            params = {
                "$where": f"cftc_contract_market_code='{mapping['cftc_code']}'",
                "$order": "report_date_as_yyyy_mm_dd DESC",
                "$limit": 5,
            }
            resp = self._session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            records = resp.json()

            if not records:
                # Fallback: try legacy futures-only report
                url_legacy = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
                params_legacy = {
                    "$where": f"cftc_contract_market_code='{mapping['cftc_code']}'",
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$limit": 5,
                }
                resp = self._session.get(url_legacy, params=params_legacy, timeout=15)
                resp.raise_for_status()
                records = resp.json()

            if not records:
                logger.warning(f"COT: No data for {instrument} ({mapping['cftc_code']})")
                return None

            latest = records[0]
            report_date = latest.get("report_date_as_yyyy_mm_dd", "unknown")

            # Parse positions — field names vary between datasets
            # Disaggregated report fields
            comm_long = int(latest.get("prod_merc_positions_long",
                           latest.get("comm_positions_long_all", 0)))
            comm_short = int(latest.get("prod_merc_positions_short",
                            latest.get("comm_positions_short_all", 0)))
            comm_net = comm_long - comm_short

            # Large speculators (managed money or non-commercial)
            spec_long = int(latest.get("m_money_positions_long",
                           latest.get("noncomm_positions_long_all", 0)))
            spec_short = int(latest.get("m_money_positions_short",
                            latest.get("noncomm_positions_short_all", 0)))
            spec_net = spec_long - spec_short

            # Small speculators (non-reportable)
            small_long = int(latest.get("nonrept_positions_long_all", 0))
            small_short = int(latest.get("nonrept_positions_short_all", 0))
            small_net = small_long - small_short

            # Derive bias: commercials are the "smart money" — they HEDGE
            # When commercials are heavily short → they're hedging long exposure → BULLISH underlying
            # When commercials are heavily long → they're hedging short exposure → BEARISH underlying
            # For currencies: COT tracks futures, so interpretation may invert for quote currencies
            #
            # Simpler: use large spec (managed money) direction as momentum indicator
            # Large specs net long → BULLISH momentum
            # Large specs net short → BEARISH momentum

            # Check previous week for trend direction
            prev_spec_net = None
            if len(records) >= 2:
                prev = records[1]
                prev_spec_net = (int(prev.get("m_money_positions_long",
                                     prev.get("noncomm_positions_long_all", 0))) -
                                 int(prev.get("m_money_positions_short",
                                     prev.get("noncomm_positions_short_all", 0))))

            # Bias determination
            if spec_net > 0 and (prev_spec_net is None or spec_net > prev_spec_net):
                bias = "BULLISH"
            elif spec_net < 0 and (prev_spec_net is None or spec_net < prev_spec_net):
                bias = "BEARISH"
            elif spec_net > 0:
                bias = "WEAK_BULLISH"  # positive but decreasing
            elif spec_net < 0:
                bias = "WEAK_BEARISH"  # negative but recovering
            else:
                bias = "NEUTRAL"

            # For JPY: invert (COT tracks JPY futures, we trade USD/JPY)
            if instrument == "usdjpy":
                invert = {"BULLISH": "BEARISH", "BEARISH": "BULLISH",
                          "WEAK_BULLISH": "WEAK_BEARISH", "WEAK_BEARISH": "WEAK_BULLISH",
                          "NEUTRAL": "NEUTRAL"}
                bias = invert[bias]

            result = {
                "instrument": instrument,
                "bias": bias,
                "report_date": report_date,
                "commercial_net": comm_net,
                "large_spec_net": spec_net,
                "large_spec_net_prev": prev_spec_net,
                "small_spec_net": small_net,
                "spec_direction": "long" if spec_net > 0 else "short",
                "spec_momentum": "increasing" if (prev_spec_net is not None and spec_net > prev_spec_net) else "decreasing",
            }

            # Store in dedicated table too
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO cot_positions
                       (instrument, report_date, commercial_long, commercial_short, commercial_net,
                        large_spec_long, large_spec_short, large_spec_net,
                        small_spec_long, small_spec_short, small_spec_net, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (instrument, report_date, comm_long, comm_short, comm_net,
                     spec_long, spec_short, spec_net, small_long, small_short, small_net,
                     datetime.now(timezone.utc).isoformat())
                )
                conn.commit()

            self._set_cached(cache_key, result, self.COT_CACHE_TTL)
            logger.info(f"COT {instrument}: {bias} (spec_net={spec_net:+,}, prev={prev_spec_net})")
            return result

        except Exception as e:
            logger.error(f"COT fetch error for {instrument}: {e}")
            # Return stale cache if available
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT data_json FROM intel_cache WHERE cache_key = ?",
                    (cache_key,)
                ).fetchone()
            if row:
                logger.info(f"COT: Using stale cache for {instrument}")
                return json.loads(row[0])
            return None

    # ============================================================
    # B) TRADINGVIEW TECHNICAL ANALYSIS
    # ============================================================
    def _check_tv_available(self) -> bool:
        """Check if tradingview-ta is installed."""
        if self._tv_available is not None:
            return self._tv_available
        try:
            import tradingview_ta
            self._tv_available = True
        except ImportError:
            self._tv_available = False
            logger.warning("tradingview-ta not installed. Run: pip install tradingview-ta")
        return self._tv_available

    def fetch_tv_rating(self, instrument: str, timeframe: str = "H4") -> Optional[Dict]:
        """
        Get TradingView technical analysis consensus.
        Returns: {summary, recommendation, buy_count, sell_count, neutral_count}
        """
        if not self._check_tv_available():
            return None

        cache_key = f"tv_{instrument}_{timeframe}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        mapping = TV_INSTRUMENT_MAP.get(instrument)
        interval = TV_INTERVAL_MAP.get(timeframe)
        if not mapping or not interval:
            logger.warning(f"TV: No mapping for {instrument}/{timeframe}")
            return None

        try:
            from tradingview_ta import TA_Handler, Interval

            # Map our interval string to tradingview_ta Interval constant
            tv_intervals = {
                "1m": Interval.INTERVAL_1_MINUTE,
                "5m": Interval.INTERVAL_5_MINUTES,
                "15m": Interval.INTERVAL_15_MINUTES,
                "30m": Interval.INTERVAL_30_MINUTES,
                "1h": Interval.INTERVAL_1_HOUR,
                "4h": Interval.INTERVAL_4_HOURS,
                "1d": Interval.INTERVAL_1_DAY,
                "1W": Interval.INTERVAL_1_WEEK,
            }
            tv_interval = tv_intervals.get(interval)
            if not tv_interval:
                return None

            handler = TA_Handler(
                symbol=mapping["symbol"],
                exchange=mapping["exchange"],
                screener="forex" if instrument not in ("btcusd", "ethusd", "us500", "nas100") else "crypto" if instrument in ("btcusd", "ethusd") else "america",
                interval=tv_interval,
            )
            analysis = handler.get_analysis()

            result = {
                "instrument": instrument,
                "timeframe": timeframe,
                "recommendation": analysis.summary["RECOMMENDATION"],  # STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
                "buy_signals": analysis.summary["BUY"],
                "sell_signals": analysis.summary["SELL"],
                "neutral_signals": analysis.summary["NEUTRAL"],
                # Key oscillator/MA values
                "rsi": analysis.indicators.get("RSI"),
                "macd_signal": analysis.indicators.get("MACD.signal"),
                "ema_20": analysis.indicators.get("EMA20"),
                "sma_50": analysis.indicators.get("SMA50"),
                "bb_upper": analysis.indicators.get("BB.upper"),
                "bb_lower": analysis.indicators.get("BB.lower"),
                "atr": analysis.indicators.get("ATR"),
            }

            self._set_cached(cache_key, result, self.TV_CACHE_TTL)
            logger.info(f"TV {instrument} {timeframe}: {result['recommendation']} "
                       f"(B:{result['buy_signals']} S:{result['sell_signals']} N:{result['neutral_signals']})")
            return result

        except Exception as e:
            logger.error(f"TV fetch error for {instrument}/{timeframe}: {e}")
            return None

    # ============================================================
    # C) FEAR & GREED INDEX
    # ============================================================
    def fetch_fear_greed(self) -> Optional[Dict]:
        """
        Fetch CNN-style Fear & Greed Index.
        Returns: {value: 0-100, classification: str, timestamp}
        """
        cache_key = "fear_greed"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        # Try multiple sources
        result = None

        # Source 1: alternative.me crypto Fear & Greed (most reliable free API)
        try:
            resp = self._session.get(
                "https://api.alternative.me/fng/?limit=2&format=json",
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("data"):
                current = data["data"][0]
                prev = data["data"][1] if len(data["data"]) > 1 else None
                result = {
                    "source": "alternative.me_crypto",
                    "value": int(current["value"]),
                    "classification": current["value_classification"],
                    "prev_value": int(prev["value"]) if prev else None,
                    "prev_classification": prev["value_classification"] if prev else None,
                    "timestamp": current.get("timestamp"),
                    # Interpretation for trading
                    "is_extreme_greed": int(current["value"]) >= 75,
                    "is_extreme_fear": int(current["value"]) <= 25,
                }
        except Exception as e:
            logger.warning(f"Fear&Greed (alternative.me) failed: {e}")

        # Source 2: VIX as fear proxy (via Yahoo Finance)
        if result is None:
            try:
                resp = self._session.get(
                    "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX?interval=1d&range=2d",
                    timeout=10
                )
                resp.raise_for_status()
                vix_data = resp.json()
                closes = vix_data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                vix_current = closes[-1] if closes else None
                if vix_current:
                    # VIX > 30 = extreme fear, VIX < 15 = extreme greed
                    # Normalize to 0-100 scale (inverted: high VIX = low greed)
                    fg_value = max(0, min(100, int(100 - (vix_current - 10) * (100 / 40))))
                    result = {
                        "source": "vix_proxy",
                        "value": fg_value,
                        "vix": round(vix_current, 2),
                        "classification": (
                            "Extreme Fear" if fg_value <= 25 else
                            "Fear" if fg_value <= 40 else
                            "Neutral" if fg_value <= 60 else
                            "Greed" if fg_value <= 75 else
                            "Extreme Greed"
                        ),
                        "is_extreme_greed": fg_value >= 75,
                        "is_extreme_fear": fg_value <= 25,
                    }
            except Exception as e:
                logger.warning(f"Fear&Greed (VIX proxy) failed: {e}")

        if result:
            self._set_cached(cache_key, result, self.FG_CACHE_TTL)
            logger.info(f"Fear&Greed: {result['value']} ({result['classification']})")
        return result

    # ============================================================
    # D) VOLATILITY ASSESSMENT
    # ============================================================
    def assess_volatility(self, df, instrument: str, timeframe: str) -> Dict:
        """
        Assess if current volatility is abnormally high.
        Uses ATR ratio, wick percentage, and candle body analysis.

        Args:
            df: DataFrame with OHLC + atr column (from add_technical_indicators)
            instrument: instrument key
            timeframe: e.g. "M15", "H1"

        Returns: {regime, atr_ratio, avg_wick_pct, is_chaotic, should_skip, reason}
        """
        result = {
            "instrument": instrument,
            "timeframe": timeframe,
            "regime": "NORMAL",
            "atr_ratio": 1.0,
            "avg_wick_pct": 0.0,
            "is_chaotic": False,
            "should_skip": False,
            "reason": "",
        }

        if len(df) < 30:
            return result

        try:
            import numpy as np

            # 1) ATR Ratio: current ATR vs 50-period average ATR
            if "atr" in df.columns:
                current_atr = df["atr"].iloc[-1]
                avg_atr = df["atr"].iloc[-50:].mean() if len(df) >= 50 else df["atr"].mean()
                if avg_atr > 0:
                    atr_ratio = current_atr / avg_atr
                    result["atr_ratio"] = round(atr_ratio, 2)
                else:
                    atr_ratio = 1.0
            else:
                atr_ratio = 1.0

            # 2) Recent candle wick analysis (last 5 candles)
            recent = df.iloc[-5:]
            wick_pcts = []
            body_pcts = []
            for _, candle in recent.iterrows():
                full_range = candle["high"] - candle["low"]
                if full_range <= 0:
                    continue
                body = abs(candle["close"] - candle["open"])
                wick = full_range - body
                wick_pcts.append(wick / full_range * 100)
                body_pcts.append(body / full_range * 100)

            avg_wick = np.mean(wick_pcts) if wick_pcts else 0
            avg_body = np.mean(body_pcts) if body_pcts else 100
            result["avg_wick_pct"] = round(avg_wick, 1)

            # 3) Directional chaos: how many direction changes in last 10 candles
            last_10 = df.iloc[-10:]
            direction_changes = 0
            for i in range(1, len(last_10)):
                curr_dir = 1 if last_10["close"].iloc[i] >= last_10["open"].iloc[i] else -1
                prev_dir = 1 if last_10["close"].iloc[i-1] >= last_10["open"].iloc[i-1] else -1
                if curr_dir != prev_dir:
                    direction_changes += 1
            chaos_ratio = direction_changes / max(len(last_10) - 1, 1)

            # 4) Large gap detection (price jump between candles)
            gap_count = 0
            if "atr" in df.columns and len(df) >= 6:
                atr_val = df["atr"].iloc[-6]
                for i in range(-5, 0):
                    gap = abs(df["open"].iloc[i] - df["close"].iloc[i - 1])
                    if atr_val > 0 and gap > atr_val * 0.5:
                        gap_count += 1

            # ── Regime classification ──
            reasons = []

            # HIGH VOLATILITY: ATR 2x+ above average
            if atr_ratio >= 2.5:
                result["regime"] = "EXTREME_VOLATILITY"
                result["should_skip"] = True
                reasons.append(f"ATR {atr_ratio:.1f}x above average")
            elif atr_ratio >= 1.8:
                result["regime"] = "HIGH_VOLATILITY"
                result["should_skip"] = True
                reasons.append(f"ATR {atr_ratio:.1f}x above average")
            elif atr_ratio <= 0.4:
                result["regime"] = "LOW_VOLATILITY"
                reasons.append(f"ATR only {atr_ratio:.1f}x of average (dead market)")

            # CHAOTIC: >70% direction changes + high wicks
            if chaos_ratio >= 0.7 and avg_wick >= 55:
                result["is_chaotic"] = True
                result["should_skip"] = True
                reasons.append(f"Chaotic: {direction_changes}/{len(last_10)-1} direction changes, {avg_wick:.0f}% avg wick")

            # WHIPSAW: avg wick > 65% on recent candles (no conviction)
            if avg_wick >= 65:
                result["should_skip"] = True
                reasons.append(f"Whipsaw candles: {avg_wick:.0f}% avg wick (no conviction)")

            # GAP RISK: multiple gaps in recent candles
            if gap_count >= 3:
                result["should_skip"] = True
                reasons.append(f"{gap_count} gap candles in last 5 (news/event driven)")

            # Lower timeframe specific: M1/M5 are naturally noisier
            if timeframe in ("M1", "M5") and atr_ratio >= 1.5:
                result["should_skip"] = True
                reasons.append(f"Low TF ({timeframe}) + elevated volatility")

            if not reasons:
                result["regime"] = "NORMAL"
            result["reason"] = "; ".join(reasons)

        except Exception as e:
            logger.error(f"Volatility assessment error: {e}")

        return result

    # ============================================================
    # E) RETAIL SENTIMENT (IG Client Sentiment)
    # ============================================================
    def fetch_retail_sentiment(self, instrument: str) -> Optional[Dict]:
        """
        Fetch retail trader sentiment as contrarian indicator.
        If 80%+ of retail is long → bearish signal (crowd is usually wrong).
        """
        cache_key = f"sentiment_{instrument}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        # IG Client Sentiment (public data, no API key)
        ig_symbols = {
            "gold": "CS.D.CFEGOLD.CFE.IP",
            "crude": "CS.D.CFEOIL.CFE.IP",
            "eurusd": "CS.D.EURUSD.CFD.IP",
            "gbpusd": "CS.D.GBPUSD.CFD.IP",
            "usdjpy": "CS.D.USDJPY.CFD.IP",
        }

        symbol = ig_symbols.get(instrument)
        if not symbol:
            return None

        try:
            # IG public sentiment widget API
            resp = self._session.get(
                f"https://www.ig.com/en/ig-client-sentiment/{symbol}",
                timeout=10,
                headers={"Accept": "application/json"}
            )
            # If IG blocks, try Myfxbook as fallback
            if resp.status_code != 200:
                return self._fetch_myfxbook_sentiment(instrument)

            data = resp.json()
            long_pct = data.get("longPositionPercentage", 50)
            short_pct = data.get("shortPositionPercentage", 50)

            result = {
                "instrument": instrument,
                "source": "ig_sentiment",
                "long_pct": round(long_pct, 1),
                "short_pct": round(short_pct, 1),
                # Contrarian: if 70%+ retail is long, bias is BEARISH
                "contrarian_bias": (
                    "BEARISH" if long_pct >= 70 else
                    "BULLISH" if short_pct >= 70 else
                    "NEUTRAL"
                ),
                "crowd_extreme": long_pct >= 75 or short_pct >= 75,
            }
            self._set_cached(cache_key, result, self.FG_CACHE_TTL)
            return result

        except Exception as e:
            logger.warning(f"IG sentiment failed for {instrument}: {e}")
            return self._fetch_myfxbook_sentiment(instrument)

    def _fetch_myfxbook_sentiment(self, instrument: str) -> Optional[Dict]:
        """Fallback: Myfxbook community outlook."""
        try:
            resp = self._session.get(
                "https://www.myfxbook.com/community/outlook",
                timeout=10
            )
            if resp.status_code != 200:
                return None
            # Myfxbook returns HTML — would need parsing
            # For now return None; will implement if IG works
            return None
        except Exception:
            return None

    # ============================================================
    # F) COMPOSITE INTELLIGENCE REPORT
    # ============================================================
    def get_full_report(self, instrument: str, timeframe: str = "H4",
                        df=None) -> Dict:
        """
        Get complete intelligence report for an instrument.
        Combines COT + TV + Fear/Greed + Volatility + Sentiment.
        """
        report = {
            "instrument": instrument,
            "timeframe": timeframe,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Fetch all intelligence (each handles its own caching)
        report["cot"] = self.fetch_cot_data(instrument)
        report["tradingview"] = self.fetch_tv_rating(instrument, timeframe)
        report["fear_greed"] = self.fetch_fear_greed()
        report["sentiment"] = self.fetch_retail_sentiment(instrument)
        report["volatility"] = self.assess_volatility(df, instrument, timeframe) if df is not None else None

        # Composite signal alignment score
        score = 0
        alignment_details = []

        # COT alignment: does institutional positioning match signal direction?
        # (Used by guardrails — score added there based on signal direction)

        # TV rating strength
        if report["tradingview"]:
            rec = report["tradingview"]["recommendation"]
            if rec in ("STRONG_BUY", "STRONG_SELL"):
                score += 2
                alignment_details.append(f"TV: {rec} (+2)")
            elif rec in ("BUY", "SELL"):
                score += 1
                alignment_details.append(f"TV: {rec} (+1)")
            else:
                alignment_details.append(f"TV: {rec} (0)")

        # Fear & Greed: extreme values are warnings, not scores
        if report["fear_greed"]:
            fg = report["fear_greed"]
            if fg["is_extreme_greed"] or fg["is_extreme_fear"]:
                alignment_details.append(f"F&G: {fg['value']} {fg['classification']} ⚠️")

        # Volatility: skip flag
        if report["volatility"] and report["volatility"]["should_skip"]:
            score -= 5
            alignment_details.append(f"VOL: {report['volatility']['regime']} (-5)")

        report["alignment_score"] = score
        report["alignment_details"] = alignment_details

        return report

    # ============================================================
    # G) TELEGRAM FORMATTING
    # ============================================================
    def format_telegram(self, report: Dict, direction: str = None) -> str:
        """Format intelligence report for Telegram notification."""
        lines = ["📊 <b>Market Intelligence</b>"]

        # COT
        cot = report.get("cot")
        if cot:
            emoji = "🟢" if "BULLISH" in cot["bias"] else "🔴" if "BEARISH" in cot["bias"] else "⚪"
            conflict = ""
            if direction:
                if (direction == "BUY" and "BEARISH" in cot["bias"]) or \
                   (direction == "SELL" and "BULLISH" in cot["bias"]):
                    conflict = " ⚠️ CONFLICTS"
            lines.append(f"{emoji} COT: {cot['bias']}{conflict}")
            lines.append(f"   Specs {cot['spec_direction']} {cot['large_spec_net']:+,} ({cot['spec_momentum']})")
            lines.append(f"   Report: {cot['report_date']}")
        else:
            lines.append("⚪ COT: unavailable")

        # TradingView
        tv = report.get("tradingview")
        if tv:
            emoji = "🟢" if "BUY" in tv["recommendation"] else "🔴" if "SELL" in tv["recommendation"] else "⚪"
            conflict = ""
            if direction:
                if (direction == "BUY" and "SELL" in tv["recommendation"]) or \
                   (direction == "SELL" and "BUY" in tv["recommendation"]):
                    conflict = " ⚠️ CONFLICTS"
            lines.append(f"{emoji} TV {tv['timeframe']}: {tv['recommendation']}{conflict}")
            lines.append(f"   B:{tv['buy_signals']} S:{tv['sell_signals']} N:{tv['neutral_signals']}")
            if tv.get("rsi"):
                lines.append(f"   RSI: {tv['rsi']:.1f}")
        else:
            lines.append("⚪ TV: unavailable")

        # Fear & Greed
        fg = report.get("fear_greed")
        if fg:
            emoji = "🔴" if fg["is_extreme_greed"] else "🟢" if fg["is_extreme_fear"] else "⚪"
            lines.append(f"{emoji} F&G: {fg['value']} ({fg['classification']})")
        else:
            lines.append("⚪ F&G: unavailable")

        # Volatility
        vol = report.get("volatility")
        if vol:
            emoji = "🔴" if vol["should_skip"] else "🟢"
            lines.append(f"{emoji} Vol: {vol['regime']} (ATR {vol['atr_ratio']:.1f}x)")
            if vol["reason"]:
                lines.append(f"   {vol['reason']}")

        # Sentiment
        sent = report.get("sentiment")
        if sent:
            emoji = "📊"
            lines.append(f"{emoji} Retail: {sent['long_pct']:.0f}% long / {sent['short_pct']:.0f}% short")
            if sent["crowd_extreme"]:
                lines.append(f"   ⚠️ Crowd extreme → {sent['contrarian_bias']} contrarian")

        return "\n".join(lines)