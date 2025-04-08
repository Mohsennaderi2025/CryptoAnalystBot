# allocator.py
from typing import List, Tuple, Dict
import pandas as pd

def allocate_budget_among_signals(
    results: List[Tuple[str, float, pd.DataFrame]],
    total_budget: float,
    tp_ratio: float = 1.05,
    sl_ratio: float = 0.97
) -> Dict[str, Dict]:
    total_score = sum([r[1] for r in results]) or 1
    allocation = {}

    for symbol, score, df in results:
        percent = score / total_score
        amount = total_budget * percent
        close_price = df['close'].iloc[-1]
        allocation[symbol] = {
            "amount": round(amount, 2),
            "entry": round(close_price, 2),
            "tp": round(close_price * tp_ratio, 2),
            "sl": round(close_price * sl_ratio, 2),
            "score": round(score, 2)
        }

    return allocation