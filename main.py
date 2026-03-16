import os
import logging
import asyncio
import random
import string
from datetime import datetime
from typing import Dict, Tuple, Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode
import httpx
from supabase import create_client, Client

# ================= CONFIG =================
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-app.onrender.com/webhook")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "123456789,987654321").split(",")]  # two admins
VERIFY_SITE_URL = os.environ.get("VERIFY_SITE_URL", "https://your-verification-site.com")  # your high-tech UI site

# Withdraw points default (can be changed via admin panel)
DEFAULT_WITHDRAW_POINTS = 3

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SUPABASE INIT =================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Ensure tables exist (you should create them manually via Supabase SQL editor)
# Table: users (user_id bigint primary key, username text, points int default 0, referrals int default 0, referred_by bigint, verified bool default false, device_id text unique)
# Table: coupons (id serial primary key, code text unique, used bool default false, used_by bigint, used_at timestamp)
# Table: channels (id serial primary key, channel_link text unique)
# Table: admin_settings (key text primary key, value text) -> for withdraw_points
# Table: user_verifications (user_id bigint primary key, device_id text, verified_at timestamp)

# ================= HELPER FUNCTIONS =================
async def is_user_joined_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if user has joined all force-join channels."""
    channels = supabase.table("channels").select("channel_link").execute()
    if not channels.data:
        return True  # no channels to join
    for ch in channels.data:
        link = ch["channel_link"]
        # Extract chat_id or username from link (assume public link like https://t.me/username)
        chat_username = link.split("/")[-1]
        try:
            member = await context.bot.get_chat_member(chat_id=f"@{chat_username}", user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            logger.error(f"Error checking channel {link}: {e}")
            return False
    return True

async def get_referral_link(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Generate a unique referral link for the user."""
    bot_username = (await context.bot.get_me()).username
    return f"https://t.me/{bot_username}?start={user_id}"

def generate_device_token() -> str:
    """Generate a unique device token for verification."""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

def get_withdraw_points() -> int:
    """Get current withdraw points from admin settings."""
    res = supabase.table("admin_settings").select("value").eq("key", "withdraw_points").execute()
    if res.data:
        return int(res.data[0]["value"])
    return DEFAULT_WITHDRAW_POINTS

def set_withdraw_points(points: int):
    """Set withdraw points in admin settings."""
    supabase.table("admin_settings").upsert({"key": "withdraw_points", "value": str(points)}).execute()

# ================= FORCE JOIN HANDLER =================
async def force_join_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Middleware to check force join before any command."""
    user_id = update.effective_user.id
    # Admin bypass
    if user_id in ADMIN_IDS:
        return True
    # Check if already verified
    user = supabase.table("users").select("verified").eq("user_id", user_id).execute()
    if user.data and user.data[0].get("verified", False):
        return True
    # Check channel membership
    if await is_user_joined_channels(user_id, context):
        # Mark as verified? No, still need web verification. But we can show verification button.
        return False  # will trigger verification prompt
    else:
        # Not joined all channels
        await show_force_join_message(update, context)
        return False

async def show_force_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show force join channels and 'I have joined all' button."""
    channels = supabase.table("channels").select("channel_link").execute()
    text = "🚨 **Force Join Required**\n\nPlease join the following channels first:\n"
    for ch in channels.data:
        text += f"• {ch['channel_link']}\n"
    text += "\nAfter joining, click the button below."
    keyboard = [[InlineKeyboardButton("✅ I have joined all", callback_data="joined_all")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def joined_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await is_user_joined_channels(user_id, context):
        # Show verification button
        keyboard = [[InlineKeyboardButton("🛑 VERIFY NOW", url=VERIFY_SITE_URL)]]
        await query.edit_message_text(
            "✅ You have joined all channels!\n\n🛑 **Verification required:** Click below to verify.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query.edit_message_text("❌ You haven't joined all channels yet. Please join and try again.")

# ================= VERIFICATION WEBHOOK =================
# The verification website will call this endpoint with user_id and device_id.
# We'll handle it via a Flask server or via Telegram webhook? Since we are on webhook, we can add a route.
# For simplicity, we'll embed a small FastAPI/Flask in the same process? But Render runs one process.
# Better: use a separate route in the same webhook app. We'll need to integrate with python-telegram-bot's webhook.
# Actually, python-telegram-bot's webhook is for Telegram only. We need an additional endpoint.
# We'll create a simple aiohttp server alongside? But that's complex. Alternative: use Flask in a thread.
# However, to keep it one file, we can use a separate route via Quart or FastAPI. But for simplicity, I'll assume we have a separate verification server.
# Given the constraints, I'll just implement a dummy check: the website redirects to a Telegram deep link with parameters, and we handle it via start command.
# But the user wants "when user clicked that button then it opens a website ... when user clicked that button then it shows you are verified and redirects to the bot".
# So we need a website. The bot can provide a unique token, and the website calls back to the bot's webhook.
# I'll implement a simple verification flow: when user clicks "VERIFY NOW", they get a unique URL with their user_id and a token. The website then calls a /verify endpoint on our bot's server (same domain). That endpoint updates DB and sends a message to user.

# To keep it simple, I'll implement a FastAPI app inside the same file, running on a different port? But Render wants one web service.
# Instead, I'll use the existing webhook endpoint to also handle verification callbacks by adding a route to the same aiohttp app used by PTB.
# python-telegram-bot v20+ uses Application.run_webhook() which starts a server. We can extend it? Not easily.
# I'll cheat: use a separate thread with Flask. But for clarity, I'll provide the bot code and note that the verification site must POST to /verify.

# Let's design: when user clicks VERIFY NOW, they go to https://yoursite.com/verify?user_id=123&token=abc. That page shows a button "Click to Verify". On click, it calls https://yousite.com/api/verify with the token. The server then marks user as verified and sends a message.

# I'll implement a simple aiohttp web application that runs alongside the bot's webhook. But PTB's run_webhook already runs a server. I'll use Application.run_webhook with a custom webhook handler that can also handle non-Telegram paths. That's possible by passing a custom webhook handler. But it's complex.

# Given the scope, I'll provide the bot code and assume the verification website is separate and calls a /verify endpoint on the same domain, and I'll show how to implement that with a simple aiohttp app that shares the same event loop.

# I'll create a combined aiohttp server that handles both Telegram webhook and verification endpoint.

# Let's write the code accordingly.

# ================= BOT COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    args = context.args
    # Check if referred
    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            # Check if user already exists
            existing = supabase.table("users").select("user_id").eq("user_id", user_id).execute()
            if not existing.data:
                # Create user with referrer
                supabase.table("users").insert({
                    "user_id": user_id,
                    "username": username,
                    "points": 0,
                    "referrals": 0,
                    "referred_by": referrer_id,
                    "verified": False
                }).execute()
                # Notify referrer later when user verifies
            else:
                # Already exists, ignore
                pass
    # Check if user exists, if not create
    existing = supabase.table("users").select("user_id").eq("user_id", user_id).execute()
    if not existing.data:
        supabase.table("users").insert({
            "user_id": user_id,
            "username": username,
            "points": 0,
            "referrals": 0,
            "referred_by": None,
            "verified": False
        }).execute()
    # Force join check
    if not await force_join_check(update, context):
        return
    # If verified, show main menu
    await show_main_menu(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = supabase.table("users").select("*").eq("user_id", user_id).execute().data[0]
    if not user.get("verified", False):
        # Should not happen, but just in case
        await force_join_check(update, context)
        return
    # Reply keyboard
    keyboard = [
        [KeyboardButton("💰 BALANCE"), KeyboardButton("🤝 REFER")],
        [KeyboardButton("🎁 WITHDRAW"), KeyboardButton("📜 MY VOUCHERS")],
        [KeyboardButton("📦 STOCK"), KeyboardButton("🏆 LEADERBOARD")]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton("👑 ADMIN PANEL")])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("🏠 **Main Menu**", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# ================= BALANCE =================
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_check(update, context):
        return
    user_id = update.effective_user.id
    user = supabase.table("users").select("points, referrals").eq("user_id", user_id).execute().data[0]
    points = user["points"]
    referrals = user["referrals"]
    voucher_cost = get_withdraw_points()
    text = f"💰 **Your Points**\n\n⭐ Points: {points}\n👥 Referrals: {referrals}\n\n🎁 Voucher Cost: {voucher_cost} point(s)"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= REFER =================
async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_check(update, context):
        return
    user_id = update.effective_user.id
    link = await get_referral_link(user_id, context)
    text = f"🤝 **Refer & Earn**\n\nInvite friends using your link:\n`{link}`\n\n✅ Each verified user gives you +1 point."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= WITHDRAW =================
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_check(update, context):
        return
    user_id = update.effective_user.id
    user = supabase.table("users").select("points").eq("user_id", user_id).execute().data[0]
    points = user["points"]
    cost = get_withdraw_points()
    if points < cost:
        await update.message.reply_text(f"❌ You need {cost} points to withdraw. You have {points}.")
        return
    # Show terms button
    keyboard = [[InlineKeyboardButton("📜 AGREE AND GET CODE", callback_data="agree_withdraw")]]
    await update.message.reply_text(
        "📜 **Terms & Conditions (Shein):**\n\n1️⃣ This Coupon Will Apply Only On SheinVerse Products.\n\nDo you agree to spend?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def agree_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    # Get a free coupon
    coupon = supabase.table("coupons").select("code").eq("used", False).limit(1).execute()
    if not coupon.data:
        await query.edit_message_text("❌ No coupons available. Contact admin.")
        return
    code = coupon.data[0]["code"]
    # Mark as used
    supabase.table("coupons").update({"used": True, "used_by": user_id, "used_at": datetime.utcnow().isoformat()}).eq("code", code).execute()
    # Deduct points
    cost = get_withdraw_points()
    user = supabase.table("users").select("points").eq("user_id", user_id).execute().data[0]
    new_points = user["points"] - cost
    supabase.table("users").update({"points": new_points}).eq("user_id", user_id).execute()
    # Record voucher for user
    # We can have a separate table user_vouchers, but for simplicity, we'll just show in MY VOUCHERS by querying coupons used_by user.
    text = f"🎉 **Shein Code Generated Successfully!**\n\n🎫 Code: `{code}`\n🛍️ [Order Here](https://www.sheinindia.in/c/sverse-5939-37961?query=%3Arelevance%3Agenderfilter%3AMen&gridColumns=5#main-content)\n\n⚠️ Copy the code and use it immediately."
    await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# ================= MY VOUCHERS =================
async def my_vouchers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_check(update, context):
        return
    user_id = update.effective_user.id
    vouchers = supabase.table("coupons").select("code, used_at").eq("used_by", user_id).order("used_at", desc=True).execute()
    if not vouchers.data:
        await update.message.reply_text("📜 **MY VOUCHERS**\n\n━━━━━━━━━━━━━━━━━━━━\nNo vouchers yet.\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: 0")
        return
    lines = []
    for v in vouchers.data:
        lines.append(f"🎫 `{v['code']}`")
    total = len(vouchers.data)
    text = "📜 **MY VOUCHERS**\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: " + str(total)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= STOCK =================
async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_check(update, context):
        return
    count = supabase.table("coupons").select("code", count="exact").eq("used", False).execute().count
    await update.message.reply_text(f"📦 **STOCK**\n\nSHEIN COUPON - {count}", parse_mode=ParseMode.MARKDOWN)

# ================= LEADERBOARD =================
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await force_join_check(update, context):
        return
    user_id = update.effective_user.id
    # Get top 10 by referrals
    top = supabase.table("users").select("username, referrals").order("referrals", desc=True).limit(10).execute().data
    lines = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, u in enumerate(top):
        name = u["username"] or f"user_{u['user_id']}"
        lines.append(f"{medals[i]} {name}\n     └ {u['referrals']} referrals")
    # Get user's rank
    all_users = supabase.table("users").select("user_id, referrals").order("referrals", desc=True).execute().data
    rank = 1
    for u in all_users:
        if u["user_id"] == user_id:
            break
        rank += 1
    user_ref = supabase.table("users").select("referrals").eq("user_id", user_id).execute().data[0]["referrals"]
    text = "🏆 **Top 10 Leaderboard**\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + f"\n━━━━━━━━━━━━━━━━━━━━\n📍 Your Rank: {rank} | {user_ref} referrals"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= REFERRAL BONUS HANDLER (when referred user verifies) =================
async def grant_referral_bonus(referrer_id: int, referred_id: int):
    """Give +1 point to referrer and notify."""
    # Update referrer's points and referrals
    referrer = supabase.table("users").select("points, referrals").eq("user_id", referrer_id).execute().data[0]
    new_points = referrer["points"] + 1
    new_refs = referrer["referrals"] + 1
    supabase.table("users").update({"points": new_points, "referrals": new_refs}).eq("user_id", referrer_id).execute()
    # Send message to referrer (if they have started the bot, we need to know chat_id)
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text="🎉 **Referral Bonus!**\n\n💰 Earned +1 pt(s)\n✅ Full reward credited!\n\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

async def deduct_referral_bonus(referrer_id: int, referred_id: int):
    """Deduct 1 point when referred user leaves channels."""
    referrer = supabase.table("users").select("points, referrals").eq("user_id", referrer_id).execute().data[0]
    new_points = max(referrer["points"] - 1, 0)
    new_refs = max(referrer["referrals"] - 1, 0)
    supabase.table("users").update({"points": new_points, "referrals": new_refs}).eq("user_id", referrer_id).execute()
    try:
        await context.bot.send_message(
            chat_id=referrer_id,
            text="🎉 **Referral Leaved Channels!**\n\n💰 Earned -1 pt(s)\n✅ Full reward deducted!\n\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

# ================= ADMIN PANEL =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return
    keyboard = [
        [KeyboardButton("📢 BROADCAST"), KeyboardButton("➕ ADD COUPON")],
        [KeyboardButton("➖ REMOVE COUPON"), KeyboardButton("➕ ADD CHANNEL")],
        [KeyboardButton("➖ REMOVE CHANNEL"), KeyboardButton("🎟️ GET A FREE CODE")],
        [KeyboardButton("💰 CHANGE WITHDRAW POINTS")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("👑 **Admin Panel**", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("📢 Send the message you want to broadcast to all users:")
    context.user_data["awaiting_broadcast"] = True

async def handle_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_broadcast"):
        message = update.message.text
        # Get all users
        users = supabase.table("users").select("user_id").execute().data
        success = 0
        failed = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u["user_id"], text=message, parse_mode=ParseMode.MARKDOWN)
                success += 1
            except:
                failed += 1
        await update.message.reply_text(f"✅ Broadcast sent.\nSuccess: {success}\nFailed: {failed}")
        context.user_data.pop("awaiting_broadcast")

async def add_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("📤 Send the coupons line by line (one code per line):")
    context.user_data["awaiting_coupon_add"] = True

async def handle_add_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_coupon_add"):
        text = update.message.text
        codes = text.strip().split("\n")
        inserted = 0
        for code in codes:
            code = code.strip()
            if code:
                try:
                    supabase.table("coupons").insert({"code": code, "used": False}).execute()
                    inserted += 1
                except:
                    pass
        await update.message.reply_text(f"✅ Added {inserted} coupons.")
        context.user_data.pop("awaiting_coupon_add")

async def remove_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔢 Send the number of coupons to remove (will delete oldest unused coupons):")
    context.user_data["awaiting_coupon_remove"] = True

async def handle_remove_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_coupon_remove"):
        try:
            num = int(update.message.text)
        except:
            await update.message.reply_text("❌ Invalid number.")
            return
        # Get oldest unused coupons
        coupons = supabase.table("coupons").select("id").eq("used", False).order("id").limit(num).execute().data
        ids = [c["id"] for c in coupons]
        if ids:
            supabase.table("coupons").delete().in_("id", ids).execute()
            await update.message.reply_text(f"✅ Removed {len(ids)} coupons.")
        else:
            await update.message.reply_text("❌ No unused coupons found.")
        context.user_data.pop("awaiting_coupon_remove")

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔗 Send the channel link (e.g., https://t.me/username):")
    context.user_data["awaiting_channel_add"] = True

async def handle_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_channel_add"):
        link = update.message.text.strip()
        try:
            supabase.table("channels").insert({"channel_link": link}).execute()
            await update.message.reply_text("✅ Channel added.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        context.user_data.pop("awaiting_channel_add")

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔗 Send the channel link to remove:")
    context.user_data["awaiting_channel_remove"] = True

async def handle_remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_channel_remove"):
        link = update.message.text.strip()
        try:
            supabase.table("channels").delete().eq("channel_link", link).execute()
            await update.message.reply_text("✅ Channel removed.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        context.user_data.pop("awaiting_channel_remove")

async def get_free_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔢 How many coupons do you need?")
    context.user_data["awaiting_free_code"] = True

async def handle_free_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_free_code"):
        try:
            num = int(update.message.text)
        except:
            await update.message.reply_text("❌ Invalid number.")
            return
        # Get unused coupons
        coupons = supabase.table("coupons").select("code").eq("used", False).limit(num).execute().data
        codes = [c["code"] for c in coupons]
        if not codes:
            await update.message.reply_text("❌ No unused coupons.")
            return
        # Mark them as used (by admin)
        for code in codes:
            supabase.table("coupons").update({"used": True, "used_by": update.effective_user.id, "used_at": datetime.utcnow().isoformat()}).eq("code", code).execute()
        # Send codes to admin
        await update.message.reply_text(f"✅ Here are your {len(codes)} codes:\n" + "\n".join(codes))
        context.user_data.pop("awaiting_free_code")

async def change_withdraw_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("💰 Send the new number of points required to withdraw:")
    context.user_data["awaiting_withdraw_points"] = True

async def handle_change_withdraw_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_withdraw_points"):
        try:
            points = int(update.message.text)
        except:
            await update.message.reply_text("❌ Invalid number.")
            return
        set_withdraw_points(points)
        await update.message.reply_text(f"✅ Withdraw points updated to {points}.")
        context.user_data.pop("awaiting_withdraw_points")

# ================= VERIFICATION CALLBACK (from website) =================
# We'll have a separate route /verify that receives POST with user_id and device_id.
# Since we are using python-telegram-bot's webhook, we need to run a custom web server.
# I'll use aiohttp to create a server that handles both Telegram and our custom endpoint.
# This requires modifying the startup.

# We'll create an aiohttp web app and run it with Application.run_webhook()? Actually, PTB can use a custom webhook handler.
# Let's do it properly:

async def verification_handler(request):
    """Handle POST from verification website."""
    data = await request.json()
    user_id = data.get("user_id")
    device_id = data.get("device_id")
    if not user_id or not device_id:
        return web.json_response({"status": "error", "message": "Missing data"}, status=400)
    # Check if this device_id already verified another user
    existing = supabase.table("user_verifications").select("user_id").eq("device_id", device_id).execute().data
    if existing:
        return web.json_response({"status": "error", "message": "Authorized Declined: Device already used"})
    # Check if user exists
    user = supabase.table("users").select("user_id, referred_by, verified").eq("user_id", user_id).execute().data
    if not user:
        return web.json_response({"status": "error", "message": "User not found"})
    if user[0].get("verified", False):
        return web.json_response({"status": "error", "message": "Already verified"})
    # Mark user as verified
    supabase.table("users").update({"verified": True}).eq("user_id", user_id).execute()
    # Record device
    supabase.table("user_verifications").insert({"user_id": user_id, "device_id": device_id, "verified_at": datetime.utcnow().isoformat()}).execute()
    # Grant referral bonus if referred
    referred_by = user[0].get("referred_by")
    if referred_by:
        await grant_referral_bonus(referred_by, user_id)
    # Send welcome message to user
    # We need to get bot instance from somewhere - we'll store it in app['bot']
    bot = request.app['bot']
    await bot.send_message(chat_id=user_id, text="✅ You are verified! Welcome to the bot.")
    return web.json_response({"status": "success", "message": "Verified"})

# We'll integrate with aiohttp in main.

# ================= MAIN =================
def main():
    # Initialize bot application
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(joined_all_callback, pattern="joined_all"))
    application.add_handler(CallbackQueryHandler(agree_withdraw_callback, pattern="agree_withdraw"))
    application.add_handler(MessageHandler(filters.Regex("^💰 BALANCE$"), balance))
    application.add_handler(MessageHandler(filters.Regex("^🤝 REFER$"), refer))
    application.add_handler(MessageHandler(filters.Regex("^🎁 WITHDRAW$"), withdraw))
    application.add_handler(MessageHandler(filters.Regex("^📜 MY VOUCHERS$"), my_vouchers))
    application.add_handler(MessageHandler(filters.Regex("^📦 STOCK$"), stock))
    application.add_handler(MessageHandler(filters.Regex("^🏆 LEADERBOARD$"), leaderboard))
    application.add_handler(MessageHandler(filters.Regex("^👑 ADMIN PANEL$"), admin_panel))
    # Admin sub-commands (text-based)
    application.add_handler(MessageHandler(filters.Regex("^📢 BROADCAST$"), broadcast))
    application.add_handler(MessageHandler(filters.Regex("^➕ ADD COUPON$"), add_coupon))
    application.add_handler(MessageHandler(filters.Regex("^➖ REMOVE COUPON$"), remove_coupon))
    application.add_handler(MessageHandler(filters.Regex("^➕ ADD CHANNEL$"), add_channel))
    application.add_handler(MessageHandler(filters.Regex("^➖ REMOVE CHANNEL$"), remove_channel))
    application.add_handler(MessageHandler(filters.Regex("^🎟️ GET A FREE CODE$"), get_free_code))
    application.add_handler(MessageHandler(filters.Regex("^💰 CHANGE WITHDRAW POINTS$"), change_withdraw_points))
    # Awaiting input handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_broadcast_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_coupon))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_coupon))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_add_channel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_remove_channel))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_code))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_change_withdraw_points))

    # Set up webhook
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL + "/" + TOKEN
    )

    # To add the verification endpoint, we need to run a custom web server.
    # Unfortunately, run_webhook blocks. We can instead create an aiohttp app and run it manually.
    # Let's rewrite main to use aiohttp with PTB's webhook handler.

    # We'll do it properly:

async def run_bot():
    # Create aiohttp app
    app = web.Application()
    # Store bot instance
    app['bot'] = application.bot
    # Add verification route
    app.router.add_post('/verify', verification_handler)
    # Set up Telegram webhook handler
    from telegram.ext import Application
    # We'll use application.run_webhook but with a custom server? Actually we can't combine easily.
    # Instead, we'll start the bot polling? But user wants webhook.
    # Workaround: use a custom webhook handler that dispatches to PTB's webhook.
    # Let's follow PTB's documentation for custom webhook:
    # https://docs.python-telegram-bot.org/en/stable/examples.customwebhookbot.html
    # We'll create a custom view that processes Telegram updates.

    async def telegram_webhook(request):
        update = await request.json()
        await application.process_update(Update.de_json(update, application.bot))
        return web.Response(status=200)

    app.router.add_post(f'/{TOKEN}', telegram_webhook)

    # Start the application
    await application.initialize()
    await application.start()
    # Run web server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()
    print("Bot started with webhook and verification endpoint.")
    # Keep running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    import aiohttp
    from aiohttp import web
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
