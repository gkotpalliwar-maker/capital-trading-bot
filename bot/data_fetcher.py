"""Market Data Fetcher"""
import numpy as np
import pandas as pd
import logging
from config import resolve_instrument, resolve_timeframe
logger = logging.getLogger(__name__)

def fetch_candles(client, instrument, granularity="M5", count=500, from_time=None, to_time=None):
    epic = resolve_instrument(instrument)
    resolution = resolve_timeframe(granularity)
    params = {"resolution": resolution, "max": min(count, 1000)}
    if from_time: params["from"] = from_time
    if to_time: params["to"] = to_time
    try: resp = client.get(f"/api/v1/prices/{epic}", params=params)
    except Exception as e: logger.error(f"Error fetching {epic}: {e}"); return pd.DataFrame()
    prices = resp.get("prices", [])
    if not prices: return pd.DataFrame()
    records = []
    for p in prices:
        bo,bh,bl,bc = float(p["openPrice"]["bid"]),float(p["highPrice"]["bid"]),float(p["lowPrice"]["bid"]),float(p["closePrice"]["bid"])
        ao,ah,al,ac = float(p["openPrice"]["ask"]),float(p["highPrice"]["ask"]),float(p["lowPrice"]["ask"]),float(p["closePrice"]["ask"])
        records.append({"time":pd.to_datetime(p["snapshotTime"]),"open":(bo+ao)/2,"high":(bh+ah)/2,"low":(bl+al)/2,"close":(bc+ac)/2,"volume":int(p.get("lastTradedVolume",0)),"complete":True,"bid_close":bc,"ask_close":ac,"spread":ac-bc})
    df = pd.DataFrame(records)
    if not df.empty: df.set_index("time",inplace=True); df.index = df.index.tz_localize(None)
    return df

def get_current_price(client, instrument):
    epic = resolve_instrument(instrument)
    resp = client.get(f"/api/v1/prices/{epic}", params={"resolution":"MINUTE","max":1})
    prices = resp.get("prices",[])
    if not prices: raise ValueError(f"No price data for {epic}")
    p = prices[-1]; bid=float(p["closePrice"]["bid"]); ask=float(p["closePrice"]["ask"])
    return {"instrument":epic,"bid":bid,"ask":ask,"mid":(bid+ask)/2,"spread":ask-bid,"time":p["snapshotTime"]}

def add_technical_indicators(df, short_ma=9, long_ma=21):
    df = df.copy()
    df["sma_short"]=df["close"].rolling(window=short_ma).mean()
    df["sma_long"]=df["close"].rolling(window=long_ma).mean()
    df["ema_short"]=df["close"].ewm(span=short_ma,adjust=False).mean()
    df["ema_long"]=df["close"].ewm(span=long_ma,adjust=False).mean()
    hl=df["high"]-df["low"];hc=(df["high"]-df["close"].shift()).abs();lc=(df["low"]-df["close"].shift()).abs()
    df["atr"]=pd.concat([hl,hc,lc],axis=1).max(axis=1).rolling(14).mean()
    delta=df["close"].diff();gain=delta.where(delta>0,0).rolling(14).mean();loss=(-delta.where(delta<0,0)).rolling(14).mean()
    df["rsi"]=100-(100/(1+gain/loss.replace(0,np.nan)))
    e12=df["close"].ewm(span=12,adjust=False).mean();e26=df["close"].ewm(span=26,adjust=False).mean()
    df["macd"]=e12-e26;df["macd_signal"]=df["macd"].ewm(span=9,adjust=False).mean();df["macd_hist"]=df["macd"]-df["macd_signal"]
    df["bb_mid"]=df["close"].rolling(20).mean();bb_std=df["close"].rolling(20).std()
    df["bb_upper"]=df["bb_mid"]+2*bb_std;df["bb_lower"]=df["bb_mid"]-2*bb_std
    df["body"]=abs(df["close"]-df["open"]);df["upper_wick"]=df["high"]-df[["open","close"]].max(axis=1)
    df["lower_wick"]=df[["open","close"]].min(axis=1)-df["low"];df["is_bullish"]=df["close"]>df["open"]
    return df
