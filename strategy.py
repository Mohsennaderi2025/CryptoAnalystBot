import pandas as pd

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["EMA50"] = df["close"].ewm(span=50).mean()
    df["EMA200"] = df["close"].ewm(span=200).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    exp1 = df["close"].ewm(span=12, adjust=False).mean()
    exp2 = df["close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = exp1 - exp2
    df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()

    return df


def score_signal_strength(df: pd.DataFrame, strat: dict) -> float:
    latest = df.iloc[-1]
    score = 0
    weights = strat.get("weights", {"ema": 0.4, "rsi": 0.3, "macd": 0.3})

    if strat["use_ema"]:
        score += weights["ema"] if latest["EMA50"] > latest["EMA200"] else 0

    if strat["use_rsi"]:
        if latest["RSI"] < strat["rsi_threshold"]:
            score += weights["rsi"]

    if strat["use_macd"]:
        if latest["MACD"] > latest["Signal"]:
            score += weights["macd"]

    return round(score, 2)


def generate_signal_label(df: pd.DataFrame, strat: dict) -> str:
    score = score_signal_strength(df, strat)
    if score == 0:
        return "⚪️ سیگنال خنثی"
    elif score >= 0.75:
        return "🟢 سیگنال خرید"
    elif score <= 0.3:
        return "🔴 سیگنال فروش"
    else:
        return "⚪️ سیگنال خنثی"


def allocate_budget_among_signals(results, total_budget: float):
    total_score = sum([r[1] for r in results]) or 1
    allocation = {}

    for symbol, score, df in results:
        portion = score / total_score
        amount = round(total_budget * portion, 2)
        latest_price = df["close"].iloc[-1]
        allocation[symbol] = {
            "amount": amount,
            "entry": round(latest_price, 2),
            "tp": round(latest_price * 1.05, 2),
            "sl": round(latest_price * 0.97, 2)
        }

    return allocation
