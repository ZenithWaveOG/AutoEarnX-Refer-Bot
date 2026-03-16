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

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Database Helpers ---
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
            # Store referrer in database
            supabase.table("users").update({"referred_by": referrer_id}).eq("user_id", user_id).execute()

    # Force join check
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
        # No force join, go directly to verification
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
    """Send WebApp button to user."""
    query = update.callback_query
    await query.answer()
    web_app_url = "https://refer-bot-verify.vercel.app"  # Your hosted HTML
    keyboard = [[InlineKeyboardButton("🔐 VERIFY NOW", web_app=WebAppInfo(url=web_app_url))]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Click below to verify with our secure mini app:", reply_markup=reply_markup)

async def web_app_data_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive data from WebApp."""
    data = update.effective_message.web_app_data.data
    user_id = update.effective_user.id
    logger.info(f"WebApp data received from user {user_id}: {data}")
    try:
        payload = json.loads(data)
        fingerprint = payload.get('fingerprint')
        if not fingerprint:
            await update.message.reply_text("Verification failed: missing fingerprint.")
            return
        # Hash the fingerprint
        hashed_fp = hashlib.sha256(fingerprint.encode()).hexdigest()
        # Check if fingerprint already used
        existing = supabase.table("users").select("user_id").eq("device_fingerprint", hashed_fp).execute()
        if existing.data:
            await update.message.reply_text("❌ Authorization Declined: This device is already linked to another account.")
            return
        # Get user
        user = await get_user(user_id)
        if not user:
            await update.message.reply_text("User not found. Please start the bot again.")
            return
        if user.get('verified'):
            await update.message.reply_text("You are already verified.")
            await show_main_menu(update.message, context)
            return
        # Mark user as verified
        supabase.table("users").update({
            "verified": True,
            "device_fingerprint": hashed_fp,
            "joined_channels": True
        }).eq("user_id", user_id).execute()
        # Credit referrer if exists
        if user.get('referred_by'):
            referrer_id = user['referred_by']
            supabase.table("users").update({
                "points": supabase.raw("points + 1"),
                "referrals": supabase.raw("referrals + 1")
            }).eq("user_id", referrer_id).execute()
            try:
                await context.bot.send_message(
                    referrer_id,
                    "🎉 Referral Bonus!\n\n💰 Earned +1 pt(s)\n✅ Full reward credited!\n\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically."
                )
            except Exception as e:
                logger.error(f"Failed to notify referrer {referrer_id}: {e}")
        await update.message.reply_text("✅ Verification successful! Redirecting...")
        await show_main_menu(update.message, context)
    except Exception as e:
        logger.error(f"WebApp data error: {e}")
        await update.message.reply_text("Verification failed. Please try again.")

async def show_main_menu(message, context: ContextTypes.DEFAULT_TYPE):
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    user = await get_user(user_id)
    if not user:
        return

    # Reply keyboard menu
    keyboard = [
        [KeyboardButton("💰 BALANCE"), KeyboardButton("🤝 REFER")],
        [KeyboardButton("🎁 WITHDRAW"), KeyboardButton("📜 MY VOUCHERS")],
        [KeyboardButton("📦 STOCK"), KeyboardButton("🏆 LEADERBOARD")],
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton("👑 ADMIN PANEL")])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await message.reply_text("Main Menu:", reply_markup=reply_markup)

# --- Menu Handlers ---
async def balance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = await get_user(user_id)
    if not user:
        return
    withdraw_points = await get_points_settings()
    text = (
        f"💰 Your Points\n\n"
        f"⭐ Points: {user['points']}\n"
        f"👥 Referrals: {user['referrals']}\n\n"
        f"🎁 Voucher Cost: {withdraw_points} point(s)"
    )
    await update.message.reply_text(text)

async def refer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={user_id}"
    text = (
        "🤝 Refer & Earn\n\n"
        "Invite friends using your link:\n"
        f"{link}\n\n"
        "✅ Each verified user gives you +1 point."
    )
    await update.message.reply_text(text)

async def withdraw_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    withdraw_points = await get_points_settings()
    text = (
        "📜 Terms & Conditions (Shein):\n\n"
        "1️⃣ This Coupon Will Apply Only On SheinVerse Products.\n\n"
        f"Do you agree to spend {withdraw_points} point(s)?"
    )
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

    text = (
        "🎉 Shein Code Generated Successfully!\n\n"
        f"🎫 Code: `{code}`\n"
        "🛍️ Order Here: [Click to Order](https://www.sheinindia.in/c/sverse-5939-37961?query=%3Arelevance%3Agenderfilter%3AMen&gridColumns=5#main-content)\n\n"
        "⚠️ Copy the code and use it immediately."
    )
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

# --- Admin Handlers ---
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
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
    await update.message.reply_text("Send me the message you want to broadcast to all users:")

async def add_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'add_coupon'
    await update.message.reply_text("Send me the coupon codes, one per line:")

async def remove_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'remove_coupon'
    await update.message.reply_text("Send the number of coupons to remove:")

async def add_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'add_channel'
    await update.message.reply_text("Send in format: `chat_id invite_link`")

async def remove_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'remove_channel'
    await update.message.reply_text("Send the invite link of the channel to remove:")

async def get_free_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'get_free_code'
    await update.message.reply_text("Send the number of coupons you need:")

async def change_withdraw_points_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data['admin_action'] = 'change_withdraw'
    await update.message.reply_text("Send the new number of points required for withdrawal:")

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    action = context.user_data.get('admin_action')
    if not action:
        return

    text = update.message.text.strip()
    if action == 'broadcast':
        users = supabase.table("users").select("user_id").execute()
        count = 0
        for u in users.data:
            try:
                await context.bot.send_message(u['user_id'], text)
                count += 1
                await asyncio.sleep(0.05)
            except:
                pass
        await update.message.reply_text(f"✅ Broadcast sent to {count} users.")
    elif action == 'add_coupon':
        codes = text.splitlines()
        inserted = 0
        for code in codes:
            code = code.strip()
            if code:
                try:
                    supabase.table("coupons").insert({"code": code}).execute()
                    inserted += 1
                except:
                    pass
        await update.message.reply_text(f"✅ Added {inserted} coupons.")
    elif action == 'remove_coupon':
        try:
            num = int(text)
            resp = supabase.table("coupons").select("id").eq("used", False).limit(num).execute()
            ids = [r['id'] for r in resp.data]
            if ids:
                supabase.table("coupons").delete().in_("id", ids).execute()
                await update.message.reply_text(f"✅ Removed {len(ids)} coupons.")
            else:
                await update.message.reply_text("No unused coupons to remove.")
        except:
            await update.message.reply_text("Invalid number.")
    elif action == 'add_channel':
        parts = text.split()
        if len(parts) >= 2:
            try:
                chat_id = int(parts[0])
                invite_link = parts[1]
                supabase.table("channels").insert({"chat_id": chat_id, "invite_link": invite_link}).execute()
                await update.message.reply_text("✅ Channel added.")
            except:
                await update.message.reply_text("Invalid data.")
        else:
            await update.message.reply_text("Please send in format: `chat_id invite_link`")
    elif action == 'remove_channel':
        supabase.table("channels").delete().eq("invite_link", text).execute()
        await update.message.reply_text("✅ Channel removed (if existed).")
    elif action == 'get_free_code':
        try:
            num = int(text)
            resp = supabase.table("coupons").select("*").eq("used", False).limit(num).execute()
            codes = [r['code'] for r in resp.data]
            if codes:
                ids = [r['id'] for r in resp.data]
                supabase.table("coupons").update({"used": True, "used_by": user_id, "used_at": datetime.now().isoformat()}).in_("id", ids).execute()
                await update.message.reply_text("Here are your codes:\n" + "\n".join(codes))
            else:
                await update.message.reply_text("Not enough unused coupons.")
        except:
            await update.message.reply_text("Invalid number.")
    elif action == 'change_withdraw':
        try:
            points = int(text)
            supabase.table("admin_settings").upsert({"key": "withdraw_points", "value": str(points)}).execute()
            await update.message.reply_text(f"✅ Withdraw points set to {points}.")
        except:
            await update.message.reply_text("Invalid number.")
    context.user_data.pop('admin_action')

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_main_menu(update.message, context)

# --- Callback query handler ---
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data == "check_join":
        await check_join_callback(update, context)
    elif data == "verify_start":
        await verify_start_callback(update, context)
    elif data == "withdraw_agree":
        await withdraw_agree_callback(update, context)

# --- Channel leave detection ---
async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member
    if not chat_member:
        return
    chat_id = chat_member.chat.id
    user_id = chat_member.new_chat_member.user.id
    old_status = chat_member.old_chat_member.status
    new_status = chat_member.new_chat_member.status
    if old_status in ["member", "administrator", "creator"] and new_status not in ["member", "administrator", "creator"]:
        channels = await get_channels()
        channel_ids = [ch['chat_id'] for ch in channels]
        if chat_id in channel_ids:
            user = await get_user(user_id)
            if user and user.get('referred_by'):
                referrer_id = user['referred_by']
                supabase.table("users").update({"points": supabase.raw("points - 1")}).eq("user_id", referrer_id).execute()
                try:
                    await context.bot.send_message(
                        referrer_id,
                        "🎉 Referral Leaved Channels!\n\n💰 Earned -1 pt(s)\n✅ Full reward deducted!\n\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically."
                    )
                except:
                    pass

# --- Error handler ---
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- Main function ---
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
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
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input))
    application.add_handler(ChatMemberHandler(chat_member_handler, ChatMemberHandler.CHAT_MEMBER))

    application.add_error_handler(error_handler)

    # Start webhook
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
