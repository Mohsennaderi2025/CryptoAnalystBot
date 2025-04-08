import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import default_user_config, load_user_configs, save_user_configs
from data import fetch_klines, fetch_top_symbols_by_volume
from indicators import calculate_indicators
from strategy import score_signal_strength, generate_signal_label
from allocator import allocate_budget_among_signals
from chart import plot_signal_chart

user_settings = load_user_configs()


def build_signal_message(symbol, tf, df, strat, amount=None):
    latest = df.iloc[-1]
    label = generate_signal_label(df, strat)
    score = score_signal_strength(df, strat)

    price = latest["close"]
    tp = price * strat.get("tp_ratio", 1.05)
    sl = price * strat.get("sl_ratio", 0.97)
    rsi = latest["RSI"]
    macd = latest["MACD"]
    signal = latest["Signal"]
    ema50 = latest["EMA50"]
    ema200 = latest["EMA200"]

    msg = f"{label} برای {symbol} (تایم‌فریم {tf})\n\n"

    if label == "⚪️ سیگنال خنثی":
        msg += "🤔 بازار در وضعیت بی‌تصمیمی قرار دارد و هیچ سیگنال مشخصی صادر نشده است.\n"

        # تخمین مدت ادامه وضعیت خنثی (بر اساس نزدیکی شاخص‌ها)
        neutrality_score = 0
        if abs(ema50 - ema200) / ema200 < 0.005:
            neutrality_score += 1
        if 45 <= rsi <= 55:
            neutrality_score += 1
        if abs(macd - signal) < 5:
            neutrality_score += 1
        neutral_minutes = 15 + neutrality_score * 15

        msg += f"\n⏳ پیش‌بینی: احتمالاً تا {neutral_minutes} دقیقه آینده همچنان وضعیت خنثی ادامه دارد."

    else:
        msg += "📌 دلایل صدور سیگنال:\n"
        if strat["use_ema"]:
            msg += "- EMA50 بالاتر از EMA200 → روند صعودی\n" if ema50 > ema200 else "- EMA50 پایین‌تر از EMA200 → روند نزولی\n"
        if strat["use_rsi"]:
            rsi_symbol = "<" if rsi < strat["rsi_threshold"] else ">"
            msg += f"- RSI = {rsi:.2f} {rsi_symbol} آستانه {strat['rsi_threshold']}\n"
        if strat["use_macd"]:
            macd_relation = ">" if macd > signal else "<"
            msg += f"- MACD ({macd:.2f}) {macd_relation} Signal ({signal:.2f})\n"

        msg += f"\n🎯 قیمت ورود: {price:.2f}\n✅ هدف سود (TP): {tp:.2f}\n⛔️ حد ضرر (SL): {sl:.2f}"

        if amount:
            quantity = amount / price
            profit = (tp - price) * quantity
            loss = (price - sl) * quantity
            msg += f"\n💰 مبلغ سرمایه‌گذاری: {amount:.2f} دلار\n📈 سود احتمالی: {profit:.2f} دلار\n📉 زیان احتمالی: {loss:.2f} دلار"

        # پیش‌بینی مدت رسیدن به TP (موقتی بر اساس اختلاف قیمت / تخمینی سرعت)
        price_change_speed = abs(df["close"].diff().tail(10).mean()) or 1
        time_to_tp = abs(tp - price) / price_change_speed
        time_label = "دقایق" if time_to_tp < 60 else "ساعت"
        est_time = int(time_to_tp) if time_to_tp < 60 else round(time_to_tp / 60, 1)
        msg += f"\n🕓 تخمین: طی {est_time} {time_label} آینده به هدف برسد."

    # ✨ خلاصه استراتژی (بدون توضیح متنی)
    msg += "\n\n📘 استراتژی فعال:\n"
    msg += f"- EMA50: {ema50:.2f} | EMA200: {ema200:.2f}\n"
    msg += f"- RSI: {rsi:.2f} (آستانه: {strat['rsi_threshold']})\n"
    msg += f"- MACD: {macd:.2f} | Signal: {signal:.2f}"
    msg += f"\n⏰ زمان تحلیل: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    return msg, price, tp, sl


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id not in user_settings:
        user_settings[user_id] = default_user_config()
        save_user_configs(user_settings)

    keyboard = [
        [InlineKeyboardButton("📈 تحلیل تک‌نماد", callback_data="analyze_single")],
        [InlineKeyboardButton("📊 سیگنال‌دهی و بودجه‌ریزی", callback_data="group_analysis")],
        [InlineKeyboardButton("⚙️ تنظیمات تحلیلگر", callback_data="show_settings")]
    ]
    await update.message.reply_text("👋 به ربات تحلیل‌گر خوش آمدید. لطفاً یک گزینه را انتخاب کنید:",
                                    reply_markup=InlineKeyboardMarkup(keyboard))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    # تحلیل تک‌نماد با دکمه نمادها
    if query.data == "analyze_single":
        await query.edit_message_text("🔍 لطفاً نماد مورد نظر را وارد کنید (مثلاً BTCUSDT):")
        top_symbols = await fetch_top_symbols_by_volume(limit=10)

        if top_symbols:
            keyboard = []
            for i in range(0, len(top_symbols), 2):
                row = [InlineKeyboardButton(sym, callback_data=f"symbol_{sym}") for sym in top_symbols[i:i+2]]
                keyboard.append(row)

            await query.message.reply_text("یا از بین نمادهای زیر انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

    # تحلیل مستقیم نماد انتخاب‌شده از دکمه‌ها
    elif query.data.startswith("symbol_"):
        symbol = query.data.replace("symbol_", "")
        tf = user_settings.get(user_id, default_user_config()).get("timeframe", "15m")
        df = await fetch_klines(symbol, tf)

        if df is None or df.empty:
            await query.edit_message_text("❌ داده‌ای برای این نماد در دسترس نیست.")
            return

        df = calculate_indicators(df)
        strat = user_settings[user_id]["strategy"]
        msg, price, tp, sl = build_signal_message(symbol, tf, df, strat)
        await query.edit_message_text(msg)

        chart_path = f"{symbol}_chart.png"
        plot_signal_chart(df, symbol, price, tp, sl, chart_path)
        await query.message.reply_photo(photo=open(chart_path, "rb"))

    # تحلیل گروهی
    elif query.data == "group_analysis":
        await query.edit_message_text("💰 لطفاً بودجه دلاری خود را وارد کنید:")
        context.user_data["expecting_budget"] = True

    # منوی تنظیمات
    elif query.data == "show_settings":
        config = user_settings.get(user_id, default_user_config())
        strat = config["strategy"]

        msg = (
            f"تنظیمات فعلی تحلیلگر:\n"
            f"- EMA: {'فعال' if strat['use_ema'] else 'غیرفعال'}\n"
            f"- RSI: {'فعال' if strat['use_rsi'] else 'غیرفعال'}\n"
            f"- MACD: {'فعال' if strat['use_macd'] else 'غیرفعال'}\n\n"
            f"آستانه RSI: {strat['rsi_threshold']}\n"
            f"وزن‌ها:\n EMA = {strat['weights']['ema']} | "
            f"RSI = {strat['weights']['rsi']} | "
            f"MACD = {strat['weights']['macd']}\n\n"
            f"TP: {strat['tp_ratio']} | SL: {strat['sl_ratio']}\n"
            f"تایم‌فریم: {config.get('timeframe', '15m')}"
        )

        keyboard = [
            [InlineKeyboardButton("تغییر آستانه RSI", callback_data="edit_rsi_threshold")],
            [InlineKeyboardButton("تغییر وزن‌ها", callback_data="edit_weights")],
            [InlineKeyboardButton("تغییر TP / SL", callback_data="edit_tp_sl")],
            [InlineKeyboardButton("تغییر تایم‌فریم", callback_data="edit_timeframe")],
            [InlineKeyboardButton("بازگردانی تنظیمات پیش‌فرض", callback_data="reset_settings")],
            [InlineKeyboardButton("بازگشت به منوی اصلی", callback_data="start")]
        ]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

    # بازگردانی تنظیمات پیش‌فرض
    elif query.data == "reset_settings":
        user_settings[user_id] = default_user_config()
        save_user_configs(user_settings)
        await query.edit_message_text("✅ تنظیمات به حالت پیش‌فرض بازنشانی شد.")

        keyboard = [
            [InlineKeyboardButton("تحلیل تک‌نماد", callback_data="analyze_single")],
            [InlineKeyboardButton("سیگنال‌دهی و بودجه‌ریزی", callback_data="group_analysis")],
            [InlineKeyboardButton("تنظیمات تحلیلگر", callback_data="show_settings")]
        ]
        await query.message.reply_text("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ویرایش آستانه RSI
    elif query.data == "edit_rsi_threshold":
        context.user_data["expecting_rsi_threshold"] = True
        await query.edit_message_text("🎚 لطفاً مقدار جدید RSI را وارد کنید (مثلاً 40):")

    # ویرایش وزن‌ها
    elif query.data == "edit_weights":
        context.user_data["expecting_weights"] = True
        await query.edit_message_text("⚖️ لطفاً وزن‌ها را وارد کنید (مثلاً: 0.4 0.3 0.3):")

    # ویرایش TP/SL
    elif query.data == "edit_tp_sl":
        context.user_data["expecting_tp_sl"] = True
        await query.edit_message_text("📊 لطفاً نسبت TP / SL را وارد کنید (مثلاً: 1.05 0.97):")

    # انتخاب تایم‌فریم
    elif query.data == "edit_timeframe":
        tfs = ["1m", "5m", "15m", "1h", "4h", "1d"]
        keyboard = [[InlineKeyboardButton(tf, callback_data=f"tf_{tf}") for tf in tfs[i:i+3]] for i in range(0, len(tfs), 3)]
        keyboard.append([InlineKeyboardButton("بازگشت", callback_data="show_settings")])
        await query.edit_message_text("⏱ لطفاً تایم‌فریم مورد نظر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))

    # ذخیره تایم‌فریم انتخاب‌شده
    elif query.data.startswith("tf_"):
        tf = query.data.replace("tf_", "")
        user_settings[user_id]["timeframe"] = tf
        save_user_configs(user_settings)
        await query.edit_message_text(f"✅ تایم‌فریم با موفقیت به {tf} تغییر یافت.")




async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = str(update.effective_user.id)

    if context.user_data.get("expecting_budget"):
        try:
            budget = float(text)
            context.user_data["expecting_budget"] = False
            await update.message.reply_text("⌛ در حال تحلیل رمزارزها...")
            await group_analysis_and_allocation(update, context, user_id, budget)
        except ValueError:
            await update.message.reply_text("❌ لطفاً یک عدد معتبر وارد کنید.")
        return

    if context.user_data.get("expecting_rsi_threshold"):
        try:
            rsi_val = float(text)
            user_settings[user_id]["strategy"]["rsi_threshold"] = rsi_val
            save_user_configs(user_settings)
            context.user_data["expecting_rsi_threshold"] = False
            await update.message.reply_text(f"🎚 آستانه RSI به {rsi_val} تغییر یافت.")
        except:
            await update.message.reply_text("❌ عدد وارد شده معتبر نیست.")
        return

    if context.user_data.get("expecting_weights"):
        try:
            parts = list(map(float, text.split()))
            if len(parts) != 3 or abs(sum(parts) - 1.0) > 0.01:
                raise ValueError("⚖️ وزن‌ها باید شامل ۳ عدد باشند که مجموع‌شان ۱ شود.")
            user_settings[user_id]["strategy"]["weights"] = dict(zip(["ema", "rsi", "macd"], parts))
            save_user_configs(user_settings)
            context.user_data["expecting_weights"] = False
            await update.message.reply_text("✅ وزن‌ها با موفقیت ذخیره شدند.")
        except Exception as e:
            await update.message.reply_text(f"❌ خطا: {str(e)}")
        return

    if context.user_data.get("expecting_tp_sl"):
        try:
            tp, sl = map(float, text.split())
            user_settings[user_id]["strategy"]["tp_ratio"] = tp
            user_settings[user_id]["strategy"]["sl_ratio"] = sl
            save_user_configs(user_settings)
            context.user_data["expecting_tp_sl"] = False
            await update.message.reply_text("🎯 نسبت TP / SL با موفقیت ذخیره شد.")
        except:
            await update.message.reply_text("❌ لطفاً دو عدد مثل '1.05 0.97' وارد کنید.")
        return

    # تحلیل تک نماد
    symbol = text.upper()
    tf = user_settings.get(user_id, default_user_config()).get("timeframe", "15m")
    df = await fetch_klines(symbol, tf)
    if df is None or df.empty:
        await update.message.reply_text("❌ نماد نامعتبر یا اطلاعاتی در دسترس نیست.")
        return

    df = calculate_indicators(df)
    strat = user_settings[user_id]["strategy"]
    msg, price, tp, sl = build_signal_message(symbol, tf, df, strat)
    await update.message.reply_text(msg)

    # 🔽 نمایش نمودار
    chart_path = f"{symbol}_chart.png"
    plot_signal_chart(df, symbol, price, tp, sl, chart_path)
    await update.message.reply_photo(photo=open(chart_path, "rb"))



async def group_analysis_and_allocation(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, budget: float):
    tf = user_settings[user_id].get("timeframe", "15m")
    strat = user_settings[user_id]["strategy"]

    top_symbols = await fetch_top_symbols_by_volume()
    results = []

    for symbol in top_symbols:
        df = await fetch_klines(symbol, tf)
        if df is None or df.empty:
            continue
        df = calculate_indicators(df)
        score = score_signal_strength(df, strat)
        results.append((symbol, score, df))

    results = sorted(results, key=lambda x: x[1], reverse=True)[:4]
    allocation = allocate_budget_among_signals(results, budget)

    await update.message.reply_text(f"📊 سیگنال‌دهی و بودجه‌ریزی برای تایم‌فریم {tf} و بودجه ${budget:.2f}:")

    total_profit, total_loss = 0, 0

    for symbol, score, df in results:
        amount = allocation[symbol]["amount"]
        msg, price, tp, sl = build_signal_message(symbol, tf, df, strat, amount)
        await update.message.reply_text(msg)

        chart_path = f"{symbol}_chart.png"
        plot_signal_chart(df, symbol, price, tp, sl, chart_path)
        await update.message.reply_photo(photo=open(chart_path, "rb"))

        quantity = amount / price
        total_profit += (tp - price) * quantity
        total_loss += (price - sl) * quantity

    await update.message.reply_text(f"💼 سود کل احتمالی: ${total_profit:.2f}\n💣 زیان کل احتمالی: ${total_loss:.2f}")




