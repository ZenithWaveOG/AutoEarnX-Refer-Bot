import os, asyncio
from datetime import datetime
from flask import Flask, request, render_template_string
from aiogram import Bot, Dispatcher, types
from aiogram.types import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
app = Flask(__name__)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

WEBHOOK_PATH = "/webhook"
WEBHOOK_FULL_URL = WEBHOOK_URL + WEBHOOK_PATH

admin_state = {}

# ================= KEYBOARDS =================

def user_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Stats", "🔗 Referral Link")
    kb.add("💰 Withdraw", "📦 Stock")
    return kb

def admin_menu():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Add Coupon", "➖ Remove Coupon")
    kb.add("➕ Add Channel", "➖ Remove Channel")
    kb.add("⚙ Change Withdraw Points")
    kb.add("📜 Redeems Log", "📦 Stock")
    kb.add("⬅ Back")
    return kb

def join_keyboard(channels):
    kb = InlineKeyboardMarkup()
    for c in channels:
        kb.add(InlineKeyboardButton("Join Channel", url=c["link"]))
    kb.add(InlineKeyboardButton("✅ Joined All", callback_data="check_join"))
    return kb

def verify_keyboard(uid):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🌐 Verify Now", url=f"{WEBHOOK_URL}/verify/{uid}"))
    kb.add(InlineKeyboardButton("✅ Complete Verification", callback_data="complete_verify"))
    return kb

# ================= START =================

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    user = supabase.table("users").select("*").eq("id", message.from_user.id).execute().data
    ref = None
    if len(message.text.split()) > 1:
        ref = int(message.text.split()[1])

    if not user:
        supabase.table("users").insert({
            "id": message.from_user.id,
            "ref_by": ref,
            "points": 0,
            "verified": False
        }).execute()

    channels = supabase.table("channels").select("*").execute().data
    if channels:
        await message.answer("Join all channels:", reply_markup=join_keyboard(channels))
    else:
        await message.answer("Verify:", reply_markup=verify_keyboard(message.from_user.id))

# ================= FORCE JOIN =================

@dp.callback_query_handler(lambda c: c.data == "check_join")
async def check_join(call: types.CallbackQuery):
    channels = supabase.table("channels").select("*").execute().data
    for c in channels:
        member = await bot.get_chat_member(c["link"], call.from_user.id)
        if member.status == "left":
            await call.message.answer("❌ You didn't join all channels.")
            return

    await call.message.answer("✅ Channels verified", reply_markup=verify_keyboard(call.from_user.id))

# ================= VERIFICATION =================

@app.route("/verify/<int:user_id>")
def verify_page(user_id):
    return render_template_string("""
<html>
<head>
<style>
body{background:#0f172a;color:white;font-family:sans-serif;text-align:center;padding-top:120px;}
button{padding:20px 50px;font-size:22px;border-radius:12px;background:#22c55e;color:white;border:none;}
</style>
</head>
<body>
<h1>Verification</h1>
<form action="/verify_done/{{uid}}">
<button>Verify Now</button>
</form>
</body>
</html>
""", uid=user_id)

@app.route("/verify_done/<int:user_id>")
def verify_done(user_id):
    user = supabase.table("users").select("*").eq("id", user_id).execute().data[0]

    if user["verified"]:
        return "Already verified"

    supabase.table("users").update({"verified": True}).eq("id", user_id).execute()

    if user["ref_by"]:
        ref_user = supabase.table("users").select("*").eq("id", user["ref_by"]).execute().data[0]
        supabase.table("users").update({"points": ref_user["points"] + 1}).eq("id", user["ref_by"]).execute()
        asyncio.run(bot.send_message(user["ref_by"], "🎉 New referral joined!"))

    return "✅ Verified! Go back to bot."

@dp.callback_query_handler(lambda c: c.data == "complete_verify")
async def complete_verify(call: types.CallbackQuery):
    user = supabase.table("users").select("*").eq("id", call.from_user.id).execute().data[0]
    if not user["verified"]:
        await call.message.answer("❌ Verification not completed.")
        return
    await call.message.answer("🎉 Verified!", reply_markup=user_menu())

# ================= USER MENU =================

@dp.message_handler(text="📊 Stats")
async def stats(message: types.Message):
    u = supabase.table("users").select("*").eq("id", message.from_user.id).execute().data[0]
    await message.answer(f"Points: {u['points']}")

@dp.message_handler(text="🔗 Referral Link")
async def ref_link(message: types.Message):
    bot_user = await bot.get_me()
    link = f"https://t.me/{bot_user.username}?start={message.from_user.id}"
    await message.answer(link)

@dp.message_handler(text="📦 Stock")
async def stock(message: types.Message):
    count = supabase.table("coupons").select("*").execute().data
    await message.answer(f"Coupons available: {len(count)}")

@dp.message_handler(text="💰 Withdraw")
async def withdraw(message: types.Message):
    user = supabase.table("users").select("*").eq("id", message.from_user.id).execute().data[0]
    need = supabase.table("settings").select("*").eq("id",1).execute().data[0]["withdraw_points"]

    if user["points"] < need:
        await message.answer(f"❌ Need {need} points.")
        return

    coupon = supabase.table("coupons").select("*").limit(1).execute().data
    if not coupon:
        await message.answer("❌ No stock.")
        return

    supabase.table("coupons").delete().eq("id", coupon[0]["id"]).execute()
    supabase.table("users").update({"points": user["points"]-need}).eq("id", message.from_user.id).execute()

    supabase.table("redeems").insert({
        "user_id": message.from_user.id,
        "time": str(datetime.now())
    }).execute()

    await message.answer(f"🎁 Coupon: {coupon[0]['code']}")

    for admin in ADMIN_IDS:
        await bot.send_message(admin, f"User {message.from_user.id} redeemed coupon")

# ================= ADMIN =================

@dp.message_handler(commands=["admin"])
async def admin_panel(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Admin Panel", reply_markup=admin_menu())

@dp.message_handler(text="⬅ Back")
async def back(message: types.Message):
    await message.answer("User Menu", reply_markup=user_menu())

@dp.message_handler(text=["➕ Add Coupon","➖ Remove Coupon","➕ Add Channel","➖ Remove Channel","⚙ Change Withdraw Points"])
async def admin_actions(message: types.Message):
    if message.from_user.id not in ADMIN_IDS: return
    admin_state[message.from_user.id] = message.text
    await message.answer("Send value:")

@dp.message_handler(lambda m: m.from_user.id in admin_state)
async def handle_admin_input(message: types.Message):
    state = admin_state[message.from_user.id]

    if state == "➕ Add Coupon":
        supabase.table("coupons").insert({"code": message.text}).execute()
        msg = "Coupon added"

    elif state == "➖ Remove Coupon":
        supabase.table("coupons").delete().limit(1).execute()
        msg = "Coupon removed"

    elif state == "➕ Add Channel":
        supabase.table("channels").insert({"link": message.text}).execute()
        msg = "Channel added"

    elif state == "➖ Remove Channel":
        supabase.table("channels").delete().eq("link", message.text).execute()
        msg = "Channel removed"

    elif state == "⚙ Change Withdraw Points":
        supabase.table("settings").update({"withdraw_points": int(message.text)}).eq("id",1).execute()
        msg = "Withdraw points updated"

    admin_state.pop(message.from_user.id)
    await message.answer(f"✅ {msg}", reply_markup=admin_menu())

# ================= WEBHOOK =================

@app.route(WEBHOOK_PATH, methods=["POST"])
def webhook():
    update = Update(**request.json)
    dp.process_update(update)
    return "OK"

@app.route("/")
def home():
    return "Bot Running"

async def set_webhook():
    await bot.set_webhook(WEBHOOK_FULL_URL)

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(set_webhook())
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
