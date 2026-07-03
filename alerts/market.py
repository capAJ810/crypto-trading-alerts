"""Exchange access: cached ccxt instances + closed-candle OHLCV fetch."""

import time

import ccxt
import pandas as pd

# Binance's main API geo-blocks US IPs (where GitHub Actions runs);
# data-api.binance.vision serves the same public market data without the block.
BINANCE_PUBLIC_DATA = "https://data-api.binance.vision/api/v3"

_exchanges = {}


def get_exchange(name: str) -> ccxt.Exchange:
    if name not in _exchanges:
        ex = getattr(ccxt, name)({"enableRateLimit": True})
        if name == "binance":
            api = ex.urls.get("api")
            if isinstance(api, dict):
                api["public"] = BINANCE_PUBLIC_DATA
            # Restrict market loading to spot only. ccxt's binance defaults
            # to also loading linear/inverse futures markets via
            # fapi.binance.com, which is geo-blocked for US IPs (unlike the
            # spot data-api.binance.vision host above) and we don't trade
            # futures here anyway.
            ex.options["fetchMarkets"] = {"types": ["spot"]}
        _exchanges[name] = ex
    return _exchanges[name]


def timeframe_ms(tf: str) -> int:
    return ccxt.Exchange.parse_timeframe(tf) * 1000


def fetch_closed_candles(exchange: ccxt.Exchange, pair: str, timeframe: str,
                         limit: int, drop_forming: bool = True) -> pd.DataFrame:
    raw = exchange.fetch_ohlcv(pair, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if drop_forming:
        # Drop the still-forming candle: keep rows whose close time has passed.
        now_ms = int(time.time() * 1000)
        df = df[df["timestamp"] + timeframe_ms(timeframe) <= now_ms]
    return df.reset_index(drop=True)
