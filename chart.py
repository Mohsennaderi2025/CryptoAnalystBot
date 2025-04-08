
import matplotlib.pyplot as plt

def plot_signal_chart(df, symbol, entry, tp, sl, filepath):
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 8), sharex=True,
                                        gridspec_kw={'height_ratios': [2, 1, 1]})

    # --- چارت قیمت ---
    ax1.plot(df["time"], df["close"], label="Close Price", linewidth=1.5)
    ax1.axhline(y=entry, color="blue", linestyle="--", label="Entry")
    ax1.axhline(y=tp, color="green", linestyle=":", label="Target")
    ax1.axhline(y=sl, color="red", linestyle=":", label="Stop Loss")
    ax1.set_title(f"Signal Chart - {symbol}", fontsize=14)
    ax1.set_ylabel("Price")
    ax1.legend()

    # --- RSI ---
    ax2.plot(df["time"], df["RSI"], label="RSI", color="purple")
    ax2.axhline(y=30, color="red", linestyle="--", linewidth=0.8)
    ax2.axhline(y=70, color="green", linestyle="--", linewidth=0.8)
    ax2.set_ylabel("RSI")
    ax2.legend(loc="upper left")

    # --- MACD ---
    ax3.plot(df["time"], df["MACD"], label="MACD", color="darkorange")
    ax3.plot(df["time"], df["Signal"], label="Signal Line", color="gray", linestyle="--")
    ax3.axhline(0, color="black", linewidth=0.5)
    ax3.set_ylabel("MACD")
    ax3.legend(loc="upper left")

    plt.xlabel("Time")
    plt.tight_layout()
    plt.savefig(filepath)
    plt.close()
