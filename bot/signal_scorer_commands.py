import logging
from signal_scorer import get_model_stats, train_model, set_threshold, get_threshold
logger = logging.getLogger("telegram")

async def mlstats_cmd(update, context):
    try:
        stats = get_model_stats()
        if "status" in stats:
            await update.message.reply_text(f"ℹ️ {stats['status']}")
            return
        fi = stats.get("feature_importance", {})
        fi_text = "\n".join([f"  {k}: {v:.1%}" for k, v in sorted(fi.items(), key=lambda x: x[1], reverse=True)[:5]])
        text = (f"🤖 <b>ML Signal Scorer</b>\n\n<b>Model:</b>\n  Acc: {stats.get('cv_accuracy',0):.1%} ± {stats.get('cv_std',0):.1%}\n"
                f"  Trades: {stats.get('n_trades',0)}\n  WR: {stats.get('win_rate',0):.1%}\n  At: {stats.get('trained_at','N/A')[:16]}\n\n"
                f"<b>Top Features:</b>\n{fi_text}\n\n<b>Threshold:</b> {get_threshold():.1%}")
        await update.message.reply_text(text, parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def retrain_cmd(update, context):
    try:
        await update.message.reply_text("🔄 Retraining...")
        ok, res = train_model(force=True)
        if ok:
            await update.message.reply_text(f"✅ <b>Retrained</b>\nTrades: {res.get('n_trades',0)}\nAcc: {res.get('cv_accuracy',0):.1%}\nWR: {res.get('win_rate',0):.1%}", parse_mode="HTML")
        else:
            await update.message.reply_text(f"⚠️ {res}")
    except Exception as e:
        await update.message.reply_text(f"❌ {e}")

async def mlthreshold_cmd(update, context):
    args = context.args
    if not args:
        await update.message.reply_text(f"ℹ️ <b>ML Threshold</b>\nCurrent: {get_threshold():.1%}\n\n/mlthreshold 0.4", parse_mode="HTML")
        return
    try:
        r = set_threshold(float(args[0]))
        await update.message.reply_text(f"✅ Threshold set to {r:.1%}")
    except:
        await update.message.reply_text("❌ Must be 0-1")