import os, hashlib, threading
from aiogram import Bot, Dispatcher, executor, types
from flask import Flask, request
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))
BOT_USERNAME = os.getenv("BOT_USERNAME")
RENDER_URL = os.getenv("RENDER_URL")

bot = Bot(BOT_TOKEN)
dp = Dispatcher(bot)
app = Flask(__name__)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

admin_state = {}

# ------------------ KEYBOARDS ------------------

def user_keyboard(is_admin=False):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Stats", "🔗 Referral Link")
    kb.add("💰 Withdraw", "📦 Stock")
    if is_admin:
        kb.add("⚙ Admin Panel")
    return kb

def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ Add Coupon", "➖ Remove Coupon")
    kb.add("➕ Add Channel", "➖ Remove Channel")
    kb.add("⚙ Change Withdraw Points")
    kb.add("📜 Redeems Log", "📦 Stock")
    kb.add("⬅ Back")
    return kb

# ------------------ WEBSITE ------------------

@app.route("/verify/<int:user_id>")
def verify(user_id):
    return f"""
<!DOCTYPE html>
<html>
<head>
<title>Verification</title>
<style>
body {{
background:linear-gradient(135deg,#ff4d6d,#ffa94d);
height:100vh;
display:flex;
align-items:center;
justify-content:center;
font-family:Arial;
}}
.card {{
background:rgba(255,255,255,0.15);
backdrop-filter:blur(15px);
padding:40px;
border-radius:20px;
text-align:center;
color:white;
box-shadow:0 0 30px rgba(0,0,0,0.3);
animation:fade 1s ease;
}}
@keyframes fade {{
from {{opacity:0; transform:scale(0.8)}}
to {{opacity:1; transform:scale(1)}}
}}
button {{
padding:15px 40px;
border:none;
border-radius:30px;
background:#00f2ff;
color:black;
font-size:18px;
cursor:pointer;
transition:0.3s;
}}
button:hover {{
transform:scale(1.1);
background:#00d0ff;
}}
.loader {{
display:none;
margin-top:20px;
}}
</style>
</head>
<body>

<div class="card">
<h1>Verification</h1>
<p>Click below to verify</p>
<form method="POST" action="/do_verify" onsubmit="load()">
<input type="hidden" name="user_id" value="{user_id}">
<button>Verify Now</button>
</form>
<div class="loader" id="loader">⏳ Verifying...</div>
</div>

<script>
function load(){{
document.getElementById("loader").style.display="block";
}}
</script>

</body>
</html>
"""

@app.route("/do_verify", methods=["POST"])
def do_verify():
    user_id = int(request.form["user_id"])
    device = request.remote_addr
    device_hash = hashlib.sha256(device.encode()).hexdigest()

    exist = supabase.table("users").select("*").eq("device_hash", device_hash).execute()
    if exist.data:
        return "<h2 style='text-align:center;color:red;'>❌ This phone already verified!</h2>"

    supabase.table("users").update({
        "verified": True,
        "device_hash": device_hash
    }).eq("id", user_id).execute()

    return f"""
    <html>
    <body style="background:#111;color:white;text-align:center;padding-top:100px;">
    <h1>✅ Verified Successfully</h1>
    <p>Redirecting to bot...</p>
    <script>
    setTimeout(()=>{{window.location.href="https://t.me/{BOT_USERNAME}"}},2000);
    </script>
    </body>
    </html>
    """

# ------------------ BOT ------------------

@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    user_id = message.from_user.id
    ref = message.get_args()

    user = supabase.table("users").select("*").eq("id", user_id).execute().data
    if not user:
        supabase.table("users").insert({
            "id": user_id,
            "referrer_id": int(ref) if ref else None
        }).execute()

    channels = supabase.table("channels").select("*").execute().data
    kb = types.InlineKeyboardMarkup()

    for ch in channels:
        kb.add(types.InlineKeyboardButton("Join Channel", url=ch["link"]))

    kb.add(types.InlineKeyboardButton("✅ Joined All Channels", callback_data="check"))
    await message.answer("Join all channels then click button:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data=="check")
async def check_join(call: types.CallbackQuery):
    user_id = call.from_user.id
    channels = supabase.table("channels").select("*").execute().data

    for ch in channels:
        username = ch["link"].split("/")[-1]
        member = await bot.get_chat_member(username, user_id)
        if member.status=="left":
            await call.message.answer("❌ You didn't join all channels")
            return

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Verify Now", url=f"{RENDER_URL}/verify/{user_id}"))
    kb.add(types.InlineKeyboardButton("☑ Complete Verification", callback_data="complete"))
    await call.message.answer("Now verify:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data=="complete")
async def complete(call: types.CallbackQuery):
    user = supabase.table("users").select("*").eq("id", call.from_user.id).execute().data[0]

    if not user["verified"]:
        await call.message.answer("❌ Verification not completed")
        return

    if user["referrer_id"]:
        supabase.table("users").update({
            "points": user["points"]+1
        }).eq("id", user["referrer_id"]).execute()
        await bot.send_message(user["referrer_id"], "🎉 New referral joined!")

    await call.message.answer("Welcome!", reply_markup=user_keyboard(call.from_user.id in ADMIN_IDS))

# ------------------ USER MENU ------------------

@dp.message_handler(text="📊 Stats")
async def stats(msg: types.Message):
    user = supabase.table("users").select("*").eq("id", msg.from_user.id).execute().data[0]
    await msg.answer(f"📊 Points: {user['points']}")

@dp.message_handler(text="🔗 Referral Link")
async def referral(msg: types.Message):
    link = f"https://t.me/{BOT_USERNAME}?start={msg.from_user.id}"
    await msg.answer(f"Your referral link:\n{link}")

@dp.message_handler(text="💰 Withdraw")
async def withdraw(msg: types.Message):
    user = supabase.table("users").select("*").eq("id", msg.from_user.id).execute().data[0]
    setting = supabase.table("settings").select("*").eq("id",1).execute().data[0]

    if user["points"] < setting["withdraw_points"]:
        await msg.answer(f"❌ Need {setting['withdraw_points']} points")
        return

    coupon = supabase.table("coupons").select("*").eq("used",False).limit(1).execute().data
    if not coupon:
        await msg.answer("❌ No stock available")
        return

    code = coupon[0]["code"]
    supabase.table("coupons").update({"used":True}).eq("id",coupon[0]["id"]).execute()
    supabase.table("redeem_logs").insert({"user_id":msg.from_user.id,"coupon_code":code}).execute()

    await msg.answer(f"🎁 Your coupon: {code}")

    for admin in ADMIN_IDS:
        await bot.send_message(admin,f"User {msg.from_user.id} redeemed coupon {code}")

@dp.message_handler(text="📦 Stock")
async def stock(msg: types.Message):
    stock = supabase.table("coupons").select("*").eq("used",False).execute().data
    await msg.answer(f"📦 Available coupons: {len(stock)}")

# ------------------ ADMIN PANEL ------------------

@dp.message_handler(text="⚙ Admin Panel")
async def admin_panel(msg: types.Message):
    if msg.from_user.id not in ADMIN_IDS: return
    await msg.answer("⚙ Admin Menu:", reply_markup=admin_keyboard())

# ---- ADD COUPON
@dp.message_handler(text="➕ Add Coupon")
async def add_coupon_prompt(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    admin_state[msg.from_user.id] = "add_coupon"
    await msg.answer("Send coupons (one per line):")

@dp.message_handler(lambda m: admin_state.get(m.from_user.id)=="add_coupon")
async def save_coupon(msg):
    for code in msg.text.split("\n"):
        supabase.table("coupons").insert({"code":code.strip()}).execute()
    admin_state.pop(msg.from_user.id)
    await msg.answer("✅ Coupons added", reply_markup=admin_keyboard())

# ---- REMOVE COUPON
@dp.message_handler(text="➖ Remove Coupon")
async def remove_coupon_prompt(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    admin_state[msg.from_user.id] = "remove_coupon"
    await msg.answer("Send number of coupons to remove:")

@dp.message_handler(lambda m: admin_state.get(m.from_user.id)=="remove_coupon")
async def remove_coupon(msg):
    num = int(msg.text)
    coupons = supabase.table("coupons").select("*").eq("used",False).limit(num).execute().data
    for c in coupons:
        supabase.table("coupons").delete().eq("id",c["id"]).execute()
    admin_state.pop(msg.from_user.id)
    await msg.answer(f"✅ Removed {len(coupons)} coupons", reply_markup=admin_keyboard())

# ---- ADD CHANNEL
@dp.message_handler(text="➕ Add Channel")
async def add_channel_prompt(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    admin_state[msg.from_user.id] = "add_channel"
    await msg.answer("Send channel link:")

@dp.message_handler(lambda m: admin_state.get(m.from_user.id)=="add_channel")
async def save_channel(msg):
    supabase.table("channels").insert({"link":msg.text}).execute()
    admin_state.pop(msg.from_user.id)
    await msg.answer("✅ Channel added", reply_markup=admin_keyboard())

# ---- REMOVE CHANNEL
@dp.message_handler(text="➖ Remove Channel")
async def remove_channel_prompt(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    admin_state[msg.from_user.id] = "remove_channel"
    await msg.answer("Send channel link to remove:")

@dp.message_handler(lambda m: admin_state.get(m.from_user.id)=="remove_channel")
async def delete_channel(msg):
    supabase.table("channels").delete().eq("link",msg.text).execute()
    admin_state.pop(msg.from_user.id)
    await msg.answer("✅ Channel removed", reply_markup=admin_keyboard())

# ---- CHANGE WITHDRAW POINTS
@dp.message_handler(text="⚙ Change Withdraw Points")
async def change_points_prompt(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    admin_state[msg.from_user.id] = "change_points"
    await msg.answer("Send new withdraw points:")

@dp.message_handler(lambda m: admin_state.get(m.from_user.id)=="change_points")
async def change_points(msg):
    points = int(msg.text)
    supabase.table("settings").update({"withdraw_points":points}).eq("id",1).execute()
    admin_state.pop(msg.from_user.id)
    await msg.answer("✅ Withdraw points updated", reply_markup=admin_keyboard())

# ---- LOGS
@dp.message_handler(text="📜 Redeems Log")
async def logs(msg):
    if msg.from_user.id not in ADMIN_IDS: return
    logs = supabase.table("redeem_logs").select("*").order("time",desc=True).limit(10).execute().data
    text="📜 Last 10 Redeems:\n\n"
    for l in logs:
        text+=f"User {l['user_id']} | {l['coupon_code']} | {l['time']}\n"
    await msg.answer(text)

# ---- BACK
@dp.message_handler(text="⬅ Back")
async def back(msg):
    await msg.answer("Main menu:", reply_markup=user_keyboard(msg.from_user.id in ADMIN_IDS))

# ------------------ RUN ------------------

def run_flask():
    app.run(host="0.0.0.0", port=5000)

threading.Thread(target=run_flask).start()
executor.start_polling(dp)
