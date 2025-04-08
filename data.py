
import aiohttp
import pandas as pd

async def fetch_klines(symbol: str, interval: str = "15m", limit: int = 100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return None
            data = await response.json()
            df = pd.DataFrame(data, columns=[
                "time", "open", "high", "low", "close", "volume",
                "close_time", "qav", "num_trades", "tbbav", "tbqav", "ignore"
            ])
            df["close"] = pd.to_numeric(df["close"])
            df["time"] = pd.to_datetime(df["time"], unit="ms")
            return df

async def fetch_top_symbols_by_volume(limit=20):
    url = "https://api.binance.com/api/v3/ticker/24hr"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                return []
            data = await response.json()
            symbols = [
                x for x in data
                if x["symbol"].endswith("USDT") and not x["symbol"].startswith(("USDT", "BUSD", "USDC", "TUSD"))
            ]
            symbols = sorted(symbols, key=lambda x: float(x["quoteVolume"]), reverse=True)
            return [x["symbol"] for x in symbols[:limit]]
