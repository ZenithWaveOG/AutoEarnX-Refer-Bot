#!/usr/bin/env python3
"""
Telegram Referral Bot with Force Join, Mini App Verification, Points, Coupons, Admin Panel
Uses python-telegram-bot v20+, Supabase (PostgreSQL), and Telegram Web App.
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    KeyboardButton, WebAppInfo, ChatMember
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ChatMemberHandler
)
from telegram.constants import ParseMode

from supabase import create_client, Client

# ================== CONFIGURATION ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
WEB_APP_URL = os.environ.get("WEB_APP_URL")  # URL of hosted mini app
ADMIN_IDS = [int(id.strip()) for id in os.environ.get("ADMIN_IDS", "").split(",") if id.strip()]
# At least two admins; you can also set is_admin in DB, but we'll use env as primary

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY or not WEB_APP_URL:
    raise ValueError("Missing required environment variables.")

# ================== LOGGING ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================== SUPABASE CLIENT ==================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================== DATABASE HELPERS ==================
async def get_user(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Fetch user from 'users' table by telegram_id."""
    result = supabase.table("users").select("*").eq("telegram_id", telegram_id).execute()
    if result.data:
        return result.data[0]
    return None

async def create_user(telegram_id: int, referred_by: Optional[int] = None, username: str = "", first_name: str = ""):
    """Insert a new user (not yet verified)."""
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "joined_at": datetime.utcnow().isoformat(),
        "points": 0,
        "referrals": 0,
        "referred_by": referred_by,
        "is_verified": False,
        "is_admin": telegram_id in ADMIN_IDS,  # set admin from env
    }
    supabase.table("users").insert(data).execute()

async def mark_user_verified(telegram_id: int, referred_by: Optional[int] = None):
    """Mark user as verified. If referred_by, credit referrer."""
    # Update user
    supabase.table("users").update({"is_verified": True}).eq("telegram_id", telegram_id).execute()
    # If referred by someone, credit them
    if referred_by:
        referrer = await get_user(referred_by)
        if referrer:
            new_points = referrer["points"] + 1
            new_refs = referrer["referrals"] + 1
            supabase.table("users").update({"points": new_points, "referrals": new_refs}).eq("telegram_id", referred_by).execute()
            # Notify referrer (will be sent later via bot)
            return referred_by  # return referrer id to send notification
    return None

async def get_force_join_channels() -> List[Dict[str, Any]]:
    """Fetch all force-join channels from DB."""
    result = supabase.table("force_join_channels").select("*").execute()
    return result.data

async def is_force_join_channel(chat_id: int) -> bool:
    """Check if a chat_id is in force-join list."""
    result = supabase.table("force_join_channels").select("*").eq("channel_id", chat_id).execute()
    return bool(result.data)

async def get_setting(key: str) -> Any:
    """Get a setting from settings table (id=1)."""
    result = supabase.table("settings").select("*").eq("id", 1).execute()
    if result.data:
        return result.data[0].get(key)
    return None

async def update_setting(key: str, value: Any):
    """Update a setting."""
    supabase.table("settings").update({key: value}).eq("id", 1).execute()

async def add_coupon(code: str):
    """Insert a single coupon."""
    supabase.table("coupons").insert({"code": code, "is_used": False}).execute()

async def add_coupons_bulk(codes: List[str]):
    """Insert multiple coupons."""
    data = [{"code": code, "is_used": False} for code in codes]
    supabase.table("coupons").insert(data).execute()

async def remove_coupons(count: int) -> int:
    """Delete 'count' unused coupons (oldest first). Returns number deleted."""
    # Fetch oldest unused coupons
    result = supabase.table("coupons").select("id").eq("is_used", False).order("id").limit(count).execute()
    ids = [row["id"] for row in result.data]
    if ids:
        supabase.table("coupons").delete().in_("id", ids).execute()
    return len(ids)

async def get_unused_coupons_count() -> int:
    """Count unused coupons."""
    result = supabase.table("coupons").select("id", count="exact").eq("is_used", False).execute()
    return result.count

async def get_free_codes(count: int) -> List[str]:
    """Fetch 'count' unused coupons and mark them as used (by admin). Returns codes."""
    result = supabase.table("coupons").select("*").eq("is_used", False).order("id").limit(count).execute()
    codes = [row["code"] for row in result.data]
    ids = [row["id"] for row in result.data]
    if ids:
        supabase.table("coupons").update({"is_used": True}).in_("id", ids).execute()
    return codes

async def redeem_coupon(user_id: int, points_cost: int) -> Optional[str]:
    """Redeem a coupon for user: deduct points, get unused code, mark used, log in redeemed_vouchers."""
    # Check user points
    user = await get_user(user_id)
    if not user or user["points"] < points_cost:
        return None
    # Find an unused coupon
    result = supabase.table("coupons").select("*").eq("is_used", False).order("id").limit(1).execute()
    if not result.data:
        return None  # no coupons
    coupon = result.data[0]
    # Update coupon
    supabase.table("coupons").update({"is_used": True, "used_by": user_id, "used_at": datetime.utcnow().isoformat()}).eq("id", coupon["id"]).execute()
    # Deduct points from user
    new_points = user["points"] - points_cost
    supabase.table("users").update({"points": new_points}).eq("telegram_id", user_id).execute()
    # Log redeemed voucher
    supabase.table("redeemed_vouchers").insert({
        "user_id": user_id,
        "coupon_code": coupon["code"],
        "redeemed_at": datetime.utcnow().isoformat()
    }).execute()
    return coupon["code"]

async def get_user_redeemed_vouchers(user_id: int) -> List[Dict[str, Any]]:
    """Get list of vouchers redeemed by user."""
    result = supabase.table("redeemed_vouchers").select("*").eq("user_id", user_id).order("redeemed_at", desc=True).execute()
    return result.data

async def get_leaderboard(limit: int = 10) -> List[Dict[str, Any]]:
    """Get top users by referrals."""
    result = supabase.table("users").select("telegram_id, first_name, username, referrals").eq("is_verified", True).order("referrals", desc=True).limit(limit).execute()
    return result.data

async def get_user_rank(telegram_id: int) -> Optional[int]:
    """Get rank of user based on referrals (1-based)."""
    # This is a bit inefficient; for production, consider a DB function.
    all_users = supabase.table("users").select("telegram_id, referrals").eq("is_verified", True).order("referrals", desc=True).execute()
    for i, u in enumerate(all_users.data, start=1):
        if u["telegram_id"] == telegram_id:
            return i
    return None

# ================== KEYBOARDS ==================
def user_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Main user menu keyboard."""
    keyboard = [
        ["💰 Balance", "🤝 Refer"],
        ["🎁 Withdraw", "📜 My Vouchers"],
        ["📦 Stock", "🏆 Leaderboard"]
    ]
    if is_admin:
        keyboard.append(["🔧 Admin Panel"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_panel_keyboard() -> ReplyKeyboardMarkup:
    """Admin panel keyboard."""
    keyboard = [
        ["📢 Broadcast", "➕ Add Coupon", "➖ Remove Coupon"],
        ["📣 Add Channel", "🚫 Remove Channel", "🎟 Get Free Code"],
        ["⚙️ Change Withdraw Points", "◀️ Back to User Menu"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def force_join_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for force join check."""
    keyboard = [[InlineKeyboardButton("✅ I have joined all channels", callback_data="check_join")]]
    return InlineKeyboardMarkup(keyboard)

def verification_keyboard() -> InlineKeyboardMarkup:
    """Inline button to open mini app for verification."""
    keyboard = [[InlineKeyboardButton("🛑 Verification required", web_app=WebAppInfo(url=WEB_APP_URL))]]
    return InlineKeyboardMarkup(keyboard)

def agree_keyboard() -> InlineKeyboardMarkup:
    """Inline button for agreeing to terms."""
    keyboard = [[InlineKeyboardButton("✅ AGREE AND GET CODE", callback_data="agree_withdraw")]]
    return InlineKeyboardMarkup(keyboard)

# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    user = update.effective_user
    args = context.args
    referred_by = None
    if args and args[0].startswith("ref_"):
        try:
            referred_by = int(args[0].split("_")[1])
        except:
            pass

    # Check if user exists in DB, if not create
    db_user = await get_user(user.id)
    if not db_user:
        await create_user(user.id, referred_by, user.username or "", user.first_name or "")
        db_user = await get_user(user.id)

    # If already verified, show main menu
    if db_user and db_user["is_verified"]:
        await show_main_menu(update, db_user)
        return

    # Force join check
    channels = await get_force_join_channels()
    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch["channel_id"], user_id=user.id)
            if member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                not_joined.append(ch)
        except Exception as e:
            logger.warning(f"Failed to check channel {ch['channel_id']}: {e}")
            not_joined.append(ch)  # treat as not joined if error

    if not_joined:
        # Show channels list
        text = "🔒 *Please join these channels first:*\n"
        for ch in not_joined:
            text += f"• {ch['channel_url']}\n"
        text += "\nAfter joining, click the button below."
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN,
            reply_markup=force_join_keyboard()
        )
    else:
        # All joined, proceed to verification
        await update.message.reply_text(
            "✅ You have joined all required channels.\nNow complete verification:",
            reply_markup=verification_keyboard()
        )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback for 'I have joined all channels' button."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    channels = await get_force_join_channels()
    not_joined = []
    for ch in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch["channel_id"], user_id=user.id)
            if member.status not in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                not_joined.append(ch)
        except:
            not_joined.append(ch)
    if not_joined:
        await query.edit_message_text("❌ You haven't joined all channels. Please join them first.")
    else:
        await query.edit_message_text(
            "✅ You joined all channels! Now click below to verify:",
            reply_markup=verification_keyboard()
        )

async def web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle data received from mini app."""
    data = json.loads(update.effective_message.web_app_data.data)
    if data.get("action") == "verify":
        user = update.effective_user
        db_user = await get_user(user.id)
        if not db_user:
            await create_user(user.id, None, user.username or "", user.first_name or "")
            db_user = await get_user(user.id)

        if db_user["is_verified"]:
            await update.message.reply_text("You are already verified.", reply_markup=user_menu_keyboard(db_user["is_admin"]))
            return

        # Mark verified and credit referrer if any
        referrer_id = db_user.get("referred_by")
        credited_referrer = await mark_user_verified(user.id, referrer_id)
        if credited_referrer:
            # Send notification to referrer
            try:
                await context.bot.send_message(
                    credited_referrer,
                    "🎉 Referral Bonus!\n💰 Earned +1 pt(s)\n✅ Full reward credited!\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically."
                )
            except Exception as e:
                logger.warning(f"Failed to notify referrer {credited_referrer}: {e}")

        await update.message.reply_text(
            "✅ You are verified! Welcome to the bot.",
            reply_markup=user_menu_keyboard(db_user["is_admin"] or user.id in ADMIN_IDS)
        )

async def show_main_menu(update: Update, db_user: Dict):
    """Send main menu to user."""
    await update.message.reply_text(
        "Main Menu:",
        reply_markup=user_menu_keyboard(db_user["is_admin"])
    )

# ================== USER MENU HANDLERS ==================
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💰 Balance handler."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not db_user["is_verified"]:
        await start(update, context)
        return
    points = db_user["points"]
    referrals = db_user["referrals"]
    voucher_cost = await get_setting("withdraw_points") or 3
    text = (
        f"💰 Your Points\n\n"
        f"⭐ Points: {points}\n"
        f"👥 Referrals: {referrals}\n\n"
        f"🎁 Voucher Cost: {voucher_cost} point(s)"
    )
    await update.message.reply_text(text)

async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🤝 Refer handler."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not db_user["is_verified"]:
        await start(update, context)
        return
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start=ref_{user.id}"
    text = (
        "🤝 Refer & Earn\n\n"
        "Invite friends using your link:\n"
        f"{link}\n\n"
        "✅ Each verified user gives you +1 point."
    )
    await update.message.reply_text(text)

async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🎁 Withdraw handler."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not db_user["is_verified"]:
        await start(update, context)
        return
    points_needed = await get_setting("withdraw_points") or 3
    if db_user["points"] < points_needed:
        await update.message.reply_text(f"Insufficient points. You need {points_needed} points.")
        return
    text = (
        "📜 Terms & Conditions (Shein):\n"
        "1️⃣ This Coupon Will Apply Only On SheinVerse Products.\n\n"
        f"Do you agree to spend {points_needed} points?"
    )
    await update.message.reply_text(text, reply_markup=agree_keyboard())

async def agree_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback when user agrees to withdraw."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    db_user = await get_user(user.id)
    if not db_user or not db_user["is_verified"]:
        await query.edit_message_text("You are not verified. Please /start again.")
        return
    points_needed = await get_setting("withdraw_points") or 3
    if db_user["points"] < points_needed:
        await query.edit_message_text("Insufficient points.")
        return
    code = await redeem_coupon(user.id, points_needed)
    if code:
        text = (
            "🎉 Shein Code Generated Successfully!\n\n"
            f"🎫 Code: `{code}`\n"
            "🛍️ Order Here: [Click to Order](https://www.sheinindia.in/c/sverse-5939-37961?query=%3Arelevance%3Agenderfilter%3AMen&gridColumns=5#main-content)\n\n"
            "⚠️ Copy the code and use it immediately."
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN)
    else:
        await query.edit_message_text("Sorry, no coupons available at the moment. Try again later.")

async def my_vouchers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📜 My Vouchers handler."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not db_user["is_verified"]:
        await start(update, context)
        return
    vouchers = await get_user_redeemed_vouchers(user.id)
    if not vouchers:
        await update.message.reply_text("You haven't redeemed any vouchers yet.")
        return
    text = "📜 MY VOUCHERS\n━━━━━━━━━━━━━━━━━━━━\n"
    for v in vouchers:
        date = v["redeemed_at"][:10]  # YYYY-MM-DD
        text += f"• {v['coupon_code']} ({date})\n"
    text += f"\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(vouchers)} redemptions"
    await update.message.reply_text(text)

async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📦 Stock handler."""
    count = await get_unused_coupons_count()
    await update.message.reply_text(f"SHEIN COUPON - {count}")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🏆 Leaderboard handler."""
    user = update.effective_user
    top = await get_leaderboard(10)
    rank = await get_user_rank(user.id) or "N/A"
    text = "🏆 Top 10 Leaderboard\n━━━━━━━━━━━━━━━━━━━━\n"
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, u in enumerate(top):
        name = u.get("first_name") or u.get("username") or f"User{u['telegram_id']}"
        refs = u["referrals"]
        medal = medals[i] if i < len(medals) else f"{i+1}."
        text += f"{medal} {name}\n     └ {refs} referral(s)\n"
    text += f"\n━━━━━━━━━━━━━━━━━━━━\n📍 Your Rank: {rank}"
    await update.message.reply_text(text)

# ================== ADMIN PANEL HANDLERS ==================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin panel to admin users."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        await update.message.reply_text("Access denied.")
        return
    await update.message.reply_text("Admin Panel:", reply_markup=admin_panel_keyboard())

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "broadcast"
    await update.message.reply_text("Send me the message you want to broadcast to all users.")

async def admin_add_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add coupon process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "add_coupon"
    await update.message.reply_text("Send me the coupon codes, one per line.")

async def admin_remove_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start remove coupon process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "remove_coupon"
    await update.message.reply_text("Send me the number of coupons to remove.")

async def admin_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add channel process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "add_channel"
    await update.message.reply_text("Send me the channel invite link or username (e.g., @channel).")

async def admin_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start remove channel process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "remove_channel"
    await update.message.reply_text("Send me the channel invite link or username to remove.")

async def admin_get_free_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start get free code process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "get_free_code"
    await update.message.reply_text("Send me the number of codes you need.")

async def admin_change_withdraw_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start change withdraw points process."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        return
    context.user_data["admin_action"] = "change_withdraw"
    await update.message.reply_text("Send me the new points required for withdrawal.")

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input during admin actions."""
    action = context.user_data.get("admin_action")
    if not action:
        return
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not (db_user["is_admin"] or user.id in ADMIN_IDS):
        context.user_data.pop("admin_action", None)
        return

    text = update.message.text.strip()
    if action == "broadcast":
        # Broadcast to all verified users
        all_users = supabase.table("users").select("telegram_id").eq("is_verified", True).execute()
        success = 0
        failed = 0
        for u in all_users.data:
            try:
                await context.bot.send_message(u["telegram_id"], text)
                success += 1
                await asyncio.sleep(0.05)  # avoid flood
            except Exception as e:
                failed += 1
                logger.warning(f"Broadcast failed to {u['telegram_id']}: {e}")
        await update.message.reply_text(f"Broadcast completed.\n✅ Sent: {success}\n❌ Failed: {failed}")
        context.user_data.pop("admin_action")

    elif action == "add_coupon":
        codes = [line.strip() for line in text.splitlines() if line.strip()]
        await add_coupons_bulk(codes)
        await update.message.reply_text(f"✅ Added {len(codes)} coupon(s).")
        context.user_data.pop("admin_action")

    elif action == "remove_coupon":
        try:
            count = int(text)
        except:
            await update.message.reply_text("Please send a valid number.")
            return
        removed = await remove_coupons(count)
        await update.message.reply_text(f"✅ Removed {removed} coupon(s).")
        context.user_data.pop("admin_action")

    elif action == "add_channel":
        # We need to resolve channel identifier to channel_id
        # For simplicity, we assume admin sends a username or invite link.
        # We'll try to get chat by username. If invite link, extract username.
        identifier = text.strip()
        if identifier.startswith("https://t.me/+"):
            await update.message.reply_text("Private invite links not supported. Please provide channel username (e.g., @channel).")
            return
        if identifier.startswith("https://t.me/"):
            parts = identifier.split("/")
            identifier = parts[-1]  # get username
        if not identifier.startswith("@"):
            identifier = "@" + identifier
        try:
            chat = await context.bot.get_chat(identifier)
            channel_id = chat.id
            # Check if bot is admin
            bot_member = await context.bot.get_chat_member(chat_id=channel_id, user_id=context.bot.id)
            if bot_member.status not in [ChatMember.ADMINISTRATOR, ChatMember.OWNER]:
                await update.message.reply_text("Bot is not an admin in that channel. Please add bot as admin first.")
                return
            # Insert into DB
            supabase.table("force_join_channels").insert({
                "channel_id": channel_id,
                "channel_url": identifier  # store username or link
            }).execute()
            await update.message.reply_text(f"✅ Channel {identifier} added.")
        except Exception as e:
            await update.message.reply_text(f"Failed to add channel: {e}")
        context.user_data.pop("admin_action")

    elif action == "remove_channel":
        identifier = text.strip()
        if identifier.startswith("https://t.me/+"):
            await update.message.reply_text("Please provide channel username (e.g., @channel).")
            return
        if identifier.startswith("https://t.me/"):
            parts = identifier.split("/")
            identifier = parts[-1]
        if not identifier.startswith("@"):
            identifier = "@" + identifier
        # Find channel by url (we stored as identifier)
        result = supabase.table("force_join_channels").select("*").eq("channel_url", identifier).execute()
        if result.data:
            supabase.table("force_join_channels").delete().eq("id", result.data[0]["id"]).execute()
            await update.message.reply_text(f"✅ Channel {identifier} removed.")
        else:
            await update.message.reply_text("Channel not found in force-join list.")
        context.user_data.pop("admin_action")

    elif action == "get_free_code":
        try:
            count = int(text)
        except:
            await update.message.reply_text("Please send a valid number.")
            return
        codes = await get_free_codes(count)
        if codes:
            await update.message.reply_text(f"Here are your codes:\n" + "\n".join(codes))
        else:
            await update.message.reply_text("No unused coupons available.")
        context.user_data.pop("admin_action")

    elif action == "change_withdraw":
        try:
            new_points = int(text)
        except:
            await update.message.reply_text("Please send a valid number.")
            return
        await update_setting("withdraw_points", new_points)
        await update.message.reply_text(f"✅ Withdraw points changed to {new_points}.")
        context.user_data.pop("admin_action")

# ================== PENALTY HANDLER ==================
async def chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Monitor when a user leaves a force-join channel."""
    chat_member = update.chat_member
    if chat_member.new_chat_member.status in [ChatMember.LEFT, ChatMember.KICKED]:
        user_id = chat_member.new_chat_member.user.id
        chat_id = chat_member.chat.id
        # Check if this chat is a force-join channel
        if await is_force_join_channel(chat_id):
            # Find if this user was referred
            user = await get_user(user_id)
            if user and user["referred_by"]:
                referrer_id = user["referred_by"]
                referrer = await get_user(referrer_id)
                if referrer and referrer["points"] > 0:
                    new_points = referrer["points"] - 1
                    new_refs = max(0, referrer["referrals"] - 1)  # Decrement referrals as well
                    supabase.table("users").update({"points": new_points, "referrals": new_refs}).eq("telegram_id", referrer_id).execute()
                    # Notify referrer
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            "🎉 Referral Leaved Channels!\n💰 Earned -1 pt(s)\n✅ Full reward deducted!\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically."
                        )
                    except Exception as e:
                        logger.warning(f"Failed to notify referrer {referrer_id}: {e}")

# ================== MESSAGE ROUTER ==================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route messages based on text."""
    user = update.effective_user
    db_user = await get_user(user.id)
    if not db_user or not db_user["is_verified"]:
        await start(update, context)
        return

    text = update.message.text
    if text == "💰 Balance":
        await balance(update, context)
    elif text == "🤝 Refer":
        await refer(update, context)
    elif text == "🎁 Withdraw":
        await withdraw(update, context)
    elif text == "📜 My Vouchers":
        await my_vouchers(update, context)
    elif text == "📦 Stock":
        await stock(update, context)
    elif text == "🏆 Leaderboard":
        await leaderboard(update, context)
    elif text == "🔧 Admin Panel" and (db_user["is_admin"] or user.id in ADMIN_IDS):
        await admin_panel(update, context)
    elif text == "◀️ Back to User Menu":
        await show_main_menu(update, db_user)
    elif text in ["📢 Broadcast", "➕ Add Coupon", "➖ Remove Coupon", "📣 Add Channel", "🚫 Remove Channel", "🎟 Get Free Code", "⚙️ Change Withdraw Points"]:
        # Admin actions
        if not (db_user["is_admin"] or user.id in ADMIN_IDS):
            return
        if text == "📢 Broadcast":
            await admin_broadcast(update, context)
        elif text == "➕ Add Coupon":
            await admin_add_coupon(update, context)
        elif text == "➖ Remove Coupon":
            await admin_remove_coupon(update, context)
        elif text == "📣 Add Channel":
            await admin_add_channel(update, context)
        elif text == "🚫 Remove Channel":
            await admin_remove_channel(update, context)
        elif text == "🎟 Get Free Code":
            await admin_get_free_code(update, context)
        elif text == "⚙️ Change Withdraw Points":
            await admin_change_withdraw_points(update, context)
    else:
        # Possibly admin input
        await handle_admin_input(update, context)

# ================== ERROR HANDLER ==================
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# ================== MAIN ==================
def main():
    """Start the bot."""
    # Create Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(agree_withdraw_callback, pattern="^agree_withdraw$"))
    application.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, web_app_data))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(ChatMemberHandler(chat_member_update, ChatMemberHandler.CHAT_MEMBER))

    # Errors
    application.add_error_handler(error_handler)

    # Start polling
    logger.info("Bot started polling...")
    application.run_polling()

if __name__ == "__main__":
    main()
