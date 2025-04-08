import json
import os

CONFIG_FILE = "user_configs.json"

def default_user_config():
    return {
        "strategy": {
            "use_ema": True,
            "use_rsi": True,
            "use_macd": True,
            "rsi_threshold": 40,
            "tp_ratio": 1.05,
            "sl_ratio": 0.97,
            "weights": {
                "ema": 0.4,
                "rsi": 0.3,
                "macd": 0.3
            }
        },
        "timeframe": "15m"
    }

def load_user_configs():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_user_configs(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
