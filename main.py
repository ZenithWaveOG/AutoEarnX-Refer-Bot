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
from aiohttp import web

# ================= CONFIG =================
TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://your-app.onrender.com/webhook")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "123456789,987654321").split(",")]
VERIFY_SITE_URL = os.environ.get("VERIFY_SITE_URL", "https://your-verification-site.com")

DEFAULT_WITHDRAW_POINTS = 3

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= SUPABASE INIT =================
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ================= HELPER FUNCTIONS =================
async def is_user_joined_channels(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    channels = supabase.table("channels").select("channel_link").execute()
    if not channels.data:
        return True
    for ch in channels.data:
        link = ch["channel_link"]
        chat_username = link.split("/")[-1]
        try:
            member = await context.bot.get_chat_member(chat_id=f"@{chat_username}", user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            logger.error(f"Error checking channel {link}: {e}")
            return False
    return True

async def is_user_verified(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    user = supabase.table("users").select("verified").eq("user_id", user_id).execute()
    return user.data and user.data[0].get("verified", False)

async def get_referral_link(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> str:
    bot_username = (await context.bot.get_me()).username
    return f"https://t.me/{bot_username}?start={user_id}"

def get_withdraw_points() -> int:
    res = supabase.table("admin_settings").select("value").eq("key", "withdraw_points").execute()
    if res.data:
        return int(res.data[0]["value"])
    return DEFAULT_WITHDRAW_POINTS

def set_withdraw_points(points: int):
    supabase.table("admin_settings").upsert({"key": "withdraw_points", "value": str(points)}).execute()

async def require_verified(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if await is_user_verified(user_id):
        return True
    await update.message.reply_text("❌ You need to verify first. Use /start to begin.")
    return False

# ================= FORCE JOIN HANDLERS =================
async def show_force_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = supabase.table("channels").select("channel_link").execute()
    text = "<b>🚨 Force Join Required</b>\n\nPlease join the following channels first:\n"
    for ch in channels.data:
        text += f"• {ch['channel_link']}\n"
    text += "\nAfter joining, click the button below."
    keyboard = [[InlineKeyboardButton("✅ I have joined all", callback_data="joined_all")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)

async def joined_all_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if await is_user_joined_channels(user_id, context):
        keyboard = [[InlineKeyboardButton("🛑 VERIFY NOW", url=f"{VERIFY_SITE_URL}?user_id={user_id}")]]
        await query.edit_message_text(
            "<b>✅ You have joined all channels!</b>\n\n<b>🛑 Verification required:</b> Click below to verify.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    else:
        await query.edit_message_text("❌ You haven't joined all channels yet. Please join and try again.")

# ================= BOT COMMANDS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    args = context.args

    if args and args[0].isdigit():
        referrer_id = int(args[0])
        if referrer_id != user_id:
            existing = supabase.table("users").select("user_id").eq("user_id", user_id).execute()
            if not existing.data:
                supabase.table("users").insert({
                    "user_id": user_id,
                    "username": username,
                    "points": 0,
                    "referrals": 0,
                    "referred_by": referrer_id,
                    "verified": False
                }).execute()

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

    if await is_user_verified(user_id):
        await show_main_menu(update, context)
        return

    if await is_user_joined_channels(user_id, context):
        keyboard = [[InlineKeyboardButton("🛑 VERIFY NOW", url=f"{VERIFY_SITE_URL}?user_id={user_id}")]]
        await update.message.reply_text(
            "<b>✅ You have joined all channels!</b>\n\n<b>🛑 Verification required:</b> Click below to verify.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    else:
        await show_force_join_message(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [KeyboardButton("💰 BALANCE"), KeyboardButton("🤝 REFER")],
        [KeyboardButton("🎁 WITHDRAW"), KeyboardButton("📜 MY VOUCHERS")],
        [KeyboardButton("📦 STOCK"), KeyboardButton("🏆 LEADERBOARD")]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append([KeyboardButton("👑 ADMIN PANEL")])
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("<b>🏠 Main Menu</b>", reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# ================= BALANCE =================
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_verified(update, context):
        return
    user_id = update.effective_user.id
    user = supabase.table("users").select("points, referrals").eq("user_id", user_id).execute().data[0]
    points = user["points"]
    referrals = user["referrals"]
    voucher_cost = get_withdraw_points()
    text = f"<b>💰 Your Points</b>\n\n⭐ Points: {points}\n👥 Referrals: {referrals}\n\n🎁 Voucher Cost: {voucher_cost} point(s)"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================= REFER =================
async def refer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_verified(update, context):
        return
    user_id = update.effective_user.id
    link = await get_referral_link(user_id, context)
    text = f"<b>🤝 Refer & Earn</b>\n\nInvite friends using your link:\n<code>{link}</code>\n\n✅ Each verified user gives you +1 point."
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================= WITHDRAW =================
async def withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_verified(update, context):
        return
    user_id = update.effective_user.id
    user = supabase.table("users").select("points").eq("user_id", user_id).execute().data[0]
    points = user["points"]
    cost = get_withdraw_points()
    if points < cost:
        await update.message.reply_text(f"❌ You need {cost} points to withdraw. You have {points}.")
        return
    keyboard = [[InlineKeyboardButton("📜 AGREE AND GET CODE", callback_data="agree_withdraw")]]
    await update.message.reply_text(
        "<b>📜 Terms & Conditions (Shein):</b>\n\n1️⃣ This Coupon Will Apply Only On SheinVerse Products.\n\nDo you agree to spend?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

async def agree_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    coupon = supabase.table("coupons").select("code").eq("used", False).limit(1).execute()
    if not coupon.data:
        await query.edit_message_text("❌ No coupons available. Contact admin.")
        return
    code = coupon.data[0]["code"]
    supabase.table("coupons").update({"used": True, "used_by": user_id, "used_at": datetime.utcnow().isoformat()}).eq("code", code).execute()
    cost = get_withdraw_points()
    user = supabase.table("users").select("points").eq("user_id", user_id).execute().data[0]
    new_points = user["points"] - cost
    supabase.table("users").update({"points": new_points}).eq("user_id", user_id).execute()
    text = f"<b>🎉 Shein Code Generated Successfully!</b>\n\n🎫 Code: <code>{code}</code>\n🛍️ <a href='https://www.sheinindia.in/c/sverse-5939-37961?query=%3Arelevance%3Agenderfilter%3AMen&gridColumns=5#main-content'>Order Here</a>\n\n⚠️ Copy the code and use it immediately."
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# ================= MY VOUCHERS =================
async def my_vouchers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_verified(update, context):
        return
    user_id = update.effective_user.id
    vouchers = supabase.table("coupons").select("code, used_at").eq("used_by", user_id).order("used_at", desc=True).execute()
    if not vouchers.data:
        await update.message.reply_text("<b>📜 MY VOUCHERS</b>\n\n━━━━━━━━━━━━━━━━━━━━\nNo vouchers yet.\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: 0", parse_mode=ParseMode.HTML)
        return
    lines = [f"🎫 <code>{v['code']}</code>" for v in vouchers.data]
    total = len(vouchers.data)
    text = "<b>📜 MY VOUCHERS</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + "\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: " + str(total)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================= STOCK =================
async def stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_verified(update, context):
        return
    count = supabase.table("coupons").select("code", count="exact").eq("used", False).execute().count
    await update.message.reply_text(f"<b>📦 STOCK</b>\n\nSHEIN COUPON - {count}", parse_mode=ParseMode.HTML)

# ================= LEADERBOARD =================
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_verified(update, context):
        return
    user_id = update.effective_user.id
    top = supabase.table("users").select("username, referrals").order("referrals", desc=True).limit(10).execute().data
    lines = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    for i, u in enumerate(top):
        name = u["username"] or f"user_{u['user_id']}"
        lines.append(f"{medals[i]} {name}\n     └ {u['referrals']} referrals")
    all_users = supabase.table("users").select("user_id, referrals").order("referrals", desc=True).execute().data
    rank = 1
    for u in all_users:
        if u["user_id"] == user_id:
            break
        rank += 1
    user_ref = supabase.table("users").select("referrals").eq("user_id", user_id).execute().data[0]["referrals"]
    text = "<b>🏆 Top 10 Leaderboard</b>\n━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines) + f"\n━━━━━━━━━━━━━━━━━━━━\n📍 Your Rank: {rank} | {user_ref} referrals"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ================= REFERRAL BONUS HANDLER =================
async def grant_referral_bonus(referrer_id: int, referred_id: int, bot):
    referrer = supabase.table("users").select("points, referrals").eq("user_id", referrer_id).execute().data
    if not referrer:
        return
    referrer = referrer[0]
    new_points = referrer["points"] + 1
    new_refs = referrer["referrals"] + 1
    supabase.table("users").update({"points": new_points, "referrals": new_refs}).eq("user_id", referrer_id).execute()
    try:
        await bot.send_message(
            chat_id=referrer_id,
            text="<b>🎉 Referral Bonus!</b>\n\n💰 Earned +1 pt(s)\n✅ Full reward credited!\n\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

async def deduct_referral_bonus(referrer_id: int, referred_id: int, bot):
    referrer = supabase.table("users").select("points, referrals").eq("user_id", referrer_id).execute().data
    if not referrer:
        return
    referrer = referrer[0]
    new_points = max(referrer["points"] - 1, 0)
    new_refs = max(referrer["referrals"] - 1, 0)
    supabase.table("users").update({"points": new_points, "referrals": new_refs}).eq("user_id", referrer_id).execute()
    try:
        await bot.send_message(
            chat_id=referrer_id,
            text="<b>🎉 Referral Leaved Channels!</b>\n\n💰 Earned -1 pt(s)\n✅ Full reward deducted!\n\n⚠️ Note: If this user leaves any channel, your point will be deducted automatically.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Failed to notify referrer {referrer_id}: {e}")

# ================= ADMIN PANEL =================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    keyboard = [
        [KeyboardButton("📢 BROADCAST"), KeyboardButton("➕ ADD COUPON")],
        [KeyboardButton("➖ REMOVE COUPON"), KeyboardButton("➕ ADD CHANNEL")],
        [KeyboardButton("➖ REMOVE CHANNEL"), KeyboardButton("🎟️ GET A FREE CODE")],
        [KeyboardButton("💰 CHANGE WITHDRAW POINTS")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("<b>👑 Admin Panel</b>", reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("📢 Send the message you want to broadcast to all users:")
    context.user_data["awaiting_broadcast"] = True

async def add_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("📤 Send the coupons line by line (one code per line):")
    context.user_data["awaiting_coupon_add"] = True

async def remove_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔢 Send the number of coupons to remove (will delete oldest unused coupons):")
    context.user_data["awaiting_coupon_remove"] = True

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔗 Send the channel link (e.g., https://t.me/username):")
    context.user_data["awaiting_channel_add"] = True

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔗 Send the channel link to remove:")
    context.user_data["awaiting_channel_remove"] = True

async def get_free_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("🔢 How many coupons do you need?")
    context.user_data["awaiting_free_code"] = True

async def change_withdraw_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text("💰 Send the new number of points required to withdraw:")
    context.user_data["awaiting_withdraw_points"] = True

async def handle_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return

    text = update.message.text

    if context.user_data.get("awaiting_broadcast"):
        users = supabase.table("users").select("user_id").execute().data
        success = 0
        failed = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u["user_id"], text=text, parse_mode=ParseMode.HTML)
                success += 1
            except:
                failed += 1
        await update.message.reply_text(f"✅ Broadcast sent.\nSuccess: {success}\nFailed: {failed}")
        context.user_data.pop("awaiting_broadcast")
        return

    if context.user_data.get("awaiting_coupon_add"):
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
        return

    if context.user_data.get("awaiting_coupon_remove"):
        try:
            num = int(text)
        except:
            await update.message.reply_text("❌ Invalid number.")
            return
        coupons = supabase.table("coupons").select("id").eq("used", False).order("id").limit(num).execute().data
        ids = [c["id"] for c in coupons]
        if ids:
            supabase.table("coupons").delete().in_("id", ids).execute()
            await update.message.reply_text(f"✅ Removed {len(ids)} coupons.")
        else:
            await update.message.reply_text("❌ No unused coupons found.")
        context.user_data.pop("awaiting_coupon_remove")
        return

    if context.user_data.get("awaiting_channel_add"):
        link = text.strip()
        try:
            supabase.table("channels").insert({"channel_link": link}).execute()
            await update.message.reply_text("✅ Channel added.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        context.user_data.pop("awaiting_channel_add")
        return

    if context.user_data.get("awaiting_channel_remove"):
        link = text.strip()
        try:
            supabase.table("channels").delete().eq("channel_link", link).execute()
            await update.message.reply_text("✅ Channel removed.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {e}")
        context.user_data.pop("awaiting_channel_remove")
        return

    if context.user_data.get("awaiting_free_code"):
        try:
            num = int(text)
        except:
            await update.message.reply_text("❌ Invalid number.")
            return
        coupons = supabase.table("coupons").select("code").eq("used", False).limit(num).execute().data
        codes = [c["code"] for c in coupons]
        if not codes:
            await update.message.reply_text("❌ No unused coupons.")
            return
        for code in codes:
            supabase.table("coupons").update({"used": True, "used_by": user_id, "used_at": datetime.utcnow().isoformat()}).eq("code", code).execute()
        await update.message.reply_text(f"✅ Here are your {len(codes)} codes:\n" + "\n".join(codes))
        context.user_data.pop("awaiting_free_code")
        return

    if context.user_data.get("awaiting_withdraw_points"):
        try:
            points = int(text)
        except:
            await update.message.reply_text("❌ Invalid number.")
            return
        set_withdraw_points(points)
        await update.message.reply_text(f"✅ Withdraw points updated to {points}.")
        context.user_data.pop("awaiting_withdraw_points")
        return

# ================= VERIFICATION CALLBACK =================
async def verification_handler(request):
    # Handle OPTIONS preflight for CORS
    if request.method == 'OPTIONS':
        return web.Response(status=200, headers={
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })

    if request.method != 'POST':
        return web.json_response({"status": "error", "message": "Method not allowed"}, status=405)

    data = await request.json()
    user_id = data.get("user_id")
    device_id = data.get("device_id")
    if not user_id or not device_id:
        return web.json_response({"status": "error", "message": "Missing data"}, status=400,
                                 headers={'Access-Control-Allow-Origin': '*'})

    # Check if device already used
    existing = supabase.table("user_verifications").select("user_id").eq("device_id", device_id).execute().data
    if existing:
        return web.json_response({"status": "error", "message": "Authorized Declined: Device already used"},
                                 headers={'Access-Control-Allow-Origin': '*'})

    user = supabase.table("users").select("user_id, referred_by, verified").eq("user_id", user_id).execute().data
    if not user:
        return web.json_response({"status": "error", "message": "User not found"},
                                 headers={'Access-Control-Allow-Origin': '*'})
    if user[0].get("verified", False):
        return web.json_response({"status": "error", "message": "Already verified"},
                                 headers={'Access-Control-Allow-Origin': '*'})

    supabase.table("users").update({"verified": True}).eq("user_id", user_id).execute()
    supabase.table("user_verifications").insert({"user_id": user_id, "device_id": device_id, "verified_at": datetime.utcnow().isoformat()}).execute()

    referred_by = user[0].get("referred_by")
    if referred_by:
        bot = request.app['bot']
        await grant_referral_bonus(referred_by, user_id, bot)

    bot = request.app['bot']
    await bot.send_message(chat_id=user_id, text="✅ You are verified! Welcome to the bot.")

    return web.json_response({"status": "success", "message": "Verified"},
                             headers={'Access-Control-Allow-Origin': '*'})

# ================= MAIN =================
async def run_bot():
    application = Application.builder().token(TOKEN).build()

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
    application.add_handler(MessageHandler(filters.Regex("^📢 BROADCAST$"), broadcast))
    application.add_handler(MessageHandler(filters.Regex("^➕ ADD COUPON$"), add_coupon))
    application.add_handler(MessageHandler(filters.Regex("^➖ REMOVE COUPON$"), remove_coupon))
    application.add_handler(MessageHandler(filters.Regex("^➕ ADD CHANNEL$"), add_channel))
    application.add_handler(MessageHandler(filters.Regex("^➖ REMOVE CHANNEL$"), remove_channel))
    application.add_handler(MessageHandler(filters.Regex("^🎟️ GET A FREE CODE$"), get_free_code))
    application.add_handler(MessageHandler(filters.Regex("^💰 CHANGE WITHDRAW POINTS$"), change_withdraw_points))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_admin_input))

    app = web.Application()
    app['bot'] = application.bot
    app.router.add_post('/verify', verification_handler)

    async def telegram_webhook(request):
        update = await request.json()
        await application.process_update(Update.de_json(update, application.bot))
        return web.Response(status=200)

    app.router.add_post(f'/{TOKEN}', telegram_webhook)
    app.router.add_post('/webhook', telegram_webhook)

    await application.initialize()
    await application.start()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()
    print("Bot started with webhook and verification endpoint at /verify")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass
