import os
import logging
import asyncio
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ChatMemberHandler
from telegram.constants import ParseMode
from supabase import create_client, Client

# --- Configuration ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "").split(",") if id]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # e.g., "https://your-app.onrender.com"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Database Helpers (unchanged, but ensure they exist) ---
async def get_user(user_id: int) -> Optional[Dict]:
    resp = supabase.table("users").select("*").eq("user_id", user_id).execute()
    return resp.data[0] if resp.data else None

async def create_user(user_id: int, username: str = "", first_name: str = ""):
    data = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "points": 0,
        "referrals": 0,
        "joined_channels": False,
        "verified": False,
        "device_fingerprint": None,
        "referred_by": None
    }
    supabase.table("users").insert(data).execute()

async def get_channels() -> List[Dict]:
    resp = supabase.table("channels").select("*").execute()
    return resp.data

async def check_membership(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = await get_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch['chat_id'], user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            logger.error(f"Membership check error: {e}")
            return False
    return True

async def get_points_settings() -> int:
    resp = supabase.table("admin_settings").select("value").eq("key", "withdraw_points").execute()
    return int(resp.data[0]['value']) if resp.data else 3

async def get_coupon_count() -> int:
    resp = supabase.table("coupons").select("*", count="exact").eq("used", False).execute()
    return resp.count

async def get_leaderboard(limit: int = 10):
    resp = supabase.table("users").select("user_id, first_name, username, referrals").order("referrals", desc=True).limit(limit).execute()
    return resp.data

async def get_user_rank(user_id: int):
    all_users = supabase.table("users").select("user_id, referrals").order("referrals", desc=True).execute()
    for idx, u in enumerate(all_users.data, start=1):
        if u['user_id'] == user_id:
            return idx, u['referrals']
    return None, 0

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    args = context.args
    referrer_id = int(args[0]) if args and args[0].isdigit() else None

    existing = await get_user(user_id)
    if not existing:
        await create_user(user_id, user.username or "", user.first_name or "")
        if referrer_id and referrer_id != user_id:
            supabase.table("users").update({"referred_by": referrer_id}).eq("user_id", user_id).execute()

    channels = await get_channels()
    if channels:
        keyboard = []
        for ch in channels:
            if ch.get('invite_link'):
                keyboard.append([InlineKeyboardButton("Join Channel", url=ch['invite_link'])])
        keyboard.append([InlineKeyboardButton("I Have Joined All Channels", callback_data="check_join")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Please join all channels below to access the bot:", reply_markup=reply_markup)
    else:
        await send_verification(update, context)

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if await check_membership(user_id, context):
        supabase.table("users").update({"joined_channels": True}).eq("user_id", user_id).execute()
        await send_verification(update, context, is_callback=True)
    else:
        await query.edit_message_text("You haven't joined all channels yet. Please join and click the button again.")

async def send_verification(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback=False):
    text = "🛑 Verification required"
    keyboard = [[InlineKeyboardButton("VERIFY NOW", callback_data="verify_start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if is_callback:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)

async def verify_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    web_app_url = "https://refer-bot-verify.vercel.app"
    keyboard = [[InlineKeyboardButton("🔐 VERIFY NOW", web_app=WebAppInfo(url=web_app_url))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Click below to verify with our secure mini app:", reply_markup=reply_markup)

async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("web_app_data_handler triggered")
    try:
        data = update.effective_message.web_app_data.data
        user_id = update.effective_user.id
        logger.info(f"Raw data: {data}")

        # Immediately acknowledge to prevent timeout
        await update.message.reply_text("Processing verification...")

        payload = json.loads(data)
        fingerprint = payload.get('fingerprint')
        if not fingerprint:
            await update.message.reply_text("Error: No fingerprint received.")
            return

        hashed_fp = hashlib.sha256(fingerprint.encode()).hexdigest()
        logger.info(f"Fingerprint hash: {hashed_fp}")

        # Check if fingerprint exists
        existing = supabase.table("users").select("user_id").eq("device_fingerprint", hashed_fp).execute()
        if existing.data:
            await update.message.reply_text("❌ This device is already linked to another account.")
            return

        user = await get_user(user_id)
        if not user:
            await update.message.reply_text("User not found. Please /start again.")
            return

        if user.get('verified'):
            await update.message.reply_text("You are already verified.")
            await show_main_menu(update.message, context)
            return

        # Mark verified
        supabase.table("users").update({
            "verified": True,
            "device_fingerprint": hashed_fp,
            "joined_channels": True
        }).eq("user_id", user_id).execute()
        logger.info(f"User {user_id} verified")

        # Credit referrer
        if user.get('referred_by'):
            referrer_id = user['referred_by']
            supabase.table("users").update({
                "points": supabase.raw("points + 1"),
                "referrals": supabase.raw("referrals + 1")
            }).eq("user_id", referrer_id).execute()
            try:
                await context.bot.send_message(
                    referrer_id,
                    "🎉 Referral Bonus!\n\n💰 Earned +1 pt(s)\n✅ Full reward credited!"
                )
            except Exception as e:
                logger.error(f"Referrer notify failed: {e}")

        await update.message.reply_text("✅ Verification successful!")
        await show_main_menu(update.message, context)

    except Exception as e:
        logger.error(f"WebApp error: {e}", exc_info=True)
        await update.message.reply_text("Verification failed. Please try again.")

async def show_main_menu(message, context: ContextTypes.DEFAULT_TYPE):
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    user = await get_user(user_id)
    if not user:
        return

    keyboard = [
        [KeyboardButton("💰 BALANCE"), KeyboardButton("🤝 REFER")],
        [KeyboardButton("🎁 WITHDRAW"), KeyboardButton("📜 MY VOUCHERS")],
        [KeyboardButton("📦 STOCK"), KeyboardButton("🏆 LEADERBOARD")],
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton("👑 ADMIN PANEL")])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await message.reply_text("Main Menu:", reply_markup=reply_markup)

# --- Menu Handlers (keep as before) ---
async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        return
    withdraw_points = await get_points_settings()
    text = f"💰 Your Points\n\n⭐ Points: {user['points']}\n👥 Referrals: {user['referrals']}\n\n🎁 Voucher Cost: {withdraw_points} point(s)"
    await update.message.reply_text(text)

async def refer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={user_id}"
    text = f"🤝 Refer & Earn\n\nInvite friends using your link:\n{link}\n\n✅ Each verified user gives you +1 point."
    await update.message.reply_text(text)

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    withdraw_points = await get_points_settings()
    text = f"📜 Terms & Conditions (Shein):\n\n1️⃣ This Coupon Will Apply Only On SheinVerse Products.\n\nDo you agree to spend {withdraw_points} point(s)?"
    keyboard = [[InlineKeyboardButton("AGREE AND GET CODE", callback_data="withdraw_agree")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup)

async def withdraw_agree_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = await get_user(user_id)
    withdraw_points = await get_points_settings()

    if user['points'] < withdraw_points:
        await query.edit_message_text("❌ You don't have enough points.")
        return

    resp = supabase.table("coupons").select("*").eq("used", False).limit(1).execute()
    if not resp.data:
        await query.edit_message_text("❌ No coupons available.")
        return
    coupon = resp.data[0]
    code = coupon['code']

    supabase.table("coupons").update({"used": True, "used_by": user_id, "used_at": datetime.now().isoformat()}).eq("id", coupon['id']).execute()
    supabase.table("users").update({"points": user['points'] - withdraw_points}).eq("user_id", user_id).execute()
    supabase.table("redeemed_vouchers").insert({"user_id": user_id, "code": code}).execute()

    text = f"🎉 Shein Code Generated Successfully!\n\n🎫 Code: `{code}`\n🛍️ Order Here: [Click to Order](https://www.sheinindia.in/c/sverse-5939-37961?query=%3Arelevance%3Agenderfilter%3AMen&gridColumns=5#main-content)\n\n⚠️ Copy the code and use it immediately."
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)

async def my_vouchers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    resp = supabase.table("redeemed_vouchers").select("*").eq("user_id", user_id).order("redeemed_at", desc=True).execute()
    vouchers = resp.data
    lines = ["📜 MY VOUCHERS", "━━━━━━━━━━━━━━━━━━━━"]
    for v in vouchers:
        lines.append(f"• {v['code']} (Redeemed on {v['redeemed_at'][:10]})")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 Total: {len(vouchers)} redemptions")
    await update.message.reply_text("\n".join(lines))

async def stock_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = await get_coupon_count()
    text = f"📦 STOCK\n\nSHEIN COUPON - {count}"
    await update.message.reply_text(text)

async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    leaders = await get_leaderboard(10)
    rank, user_refs = await get_user_rank(user_id)
    lines = ["🏆 Top 10 Leaderboard", "━━━━━━━━━━━━━━━━━━━━"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(leaders, start=1):
        medal = medals[i-1] if i <= 3 else f"{i}️⃣"
        name = u.get('first_name') or u.get('username') or str(u['user_id'])
        lines.append(f"{medal} {name}")
        lines.append(f"     └ {u['referrals']} referrals")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📍 Your Rank: #{rank}  |  {user_refs} referrals")
    await update.message.reply_text("\n".join(lines))

# --- Admin Handlers (shortened for brevity, but include all) ---
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    keyboard = [
        [KeyboardButton("📢 BROADCAST"), KeyboardButton("➕ ADD COUPON")],
        [KeyboardButton("➖ REMOVE COUPON"), KeyboardButton("➕ ADD CHANNEL")],
        [KeyboardButton("➖ REMOVE CHANNEL"), KeyboardButton("🎁 GET FREE CODE")],
        [KeyboardButton("💰 CHANGE WITHDRAW POINTS")],
        [KeyboardButton("🔙 BACK TO MAIN MENU")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("👑 Admin Panel", reply_markup=reply_markup)

async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'broadcast'
    await update.message.reply_text("Send me the message to broadcast:")

async def add_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'add_coupon'
    await update.message.reply_text("Send coupon codes, one per line:")

async def remove_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'remove_coupon'
    await update.message.reply_text("Send number of coupons to remove:")

async def add_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'add_channel'
    await update.message.reply_text("Send in format: `chat_id invite_link`")

async def remove_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'remove_channel'
    await update.message.reply_text("Send invite link of channel to remove:")

async def get_free_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'get_free_code'
    await update.message.reply_text("Send number of coupons needed:")

async def change_withdraw_points_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'change_withdraw'
    await update.message.reply_text("Send new withdraw points amount:")

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    action = context.user_data.get('admin_action')
    if not action:
        return

    text = update.message.text.strip()
    # ... (keep your existing admin input handling code) ...
    # For brevity, I'll assume you keep the full admin logic here.
    # Make sure it's included from your previous working admin code.

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update.message, context)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "check_join":
        await check_join_callback(update, context)
    elif data == "verify_start":
        await verify_start_callback(update, context)
    elif data == "withdraw_agree":
        await withdraw_agree_callback(update, context)

async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (keep your existing channel leave deduction code)
    pass

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Update caused error", exc_info=context.error)

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data_handler))
    application.add_handler(MessageHandler(filters.Regex("^💰 BALANCE$"), balance_handler))
    application.add_handler(MessageHandler(filters.Regex("^🤝 REFER$"), refer_handler))
    application.add_handler(MessageHandler(filters.Regex("^🎁 WITHDRAW$"), withdraw_handler))
    application.add_handler(MessageHandler(filters.Regex("^📜 MY VOUCHERS$"), my_vouchers_handler))
    application.add_handler(MessageHandler(filters.Regex("^📦 STOCK$"), stock_handler))
    application.add_handler(MessageHandler(filters.Regex("^🏆 LEADERBOARD$"), leaderboard_handler))
    application.add_handler(MessageHandler(filters.Regex("^👑 ADMIN PANEL$"), admin_panel_handler))
    application.add_handler(MessageHandler(filters.Regex("^📢 BROADCAST$"), broadcast_handler))
    application.add_handler(MessageHandler(filters.Regex("^➕ ADD COUPON$"), add_coupon_handler))
    application.add_handler(MessageHandler(filters.Regex("^➖ REMOVE COUPON$"), remove_coupon_handler))
    application.add_handler(MessageHandler(filters.Regex("^➕ ADD CHANNEL$"), add_channel_handler))
    application.add_handler(MessageHandler(filters.Regex("^➖ REMOVE CHANNEL$"), remove_channel_handler))
    application.add_handler(MessageHandler(filters.Regex("^🎁 GET FREE CODE$"), get_free_code_handler))
    application.add_handler(MessageHandler(filters.Regex("^💰 CHANGE WITHDRAW POINTS$"), change_withdraw_points_handler))
    application.add_handler(MessageHandler(filters.Regex("^🔙 BACK TO MAIN MENU$"), back_to_main))
    # Admin text input (catch-all)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input))
    application.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.CHAT_MEMBER))
    application.add_error_handler(error_handler)

    if WEBHOOK_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            url_path="webhook",
            webhook_url=f"{WEBHOOK_URL}/webhook"
        )
    else:
        application.run_polling()

if __name__ == "__main__":
    main()
