import logging
from flask import Flask, request
from threading import Thread
from telegram import *
from telegram.ext import *
from supabase import create_client

# ================= CONFIG =================
BOT_TOKEN = "8160133246:AAFDhth4g5hsSumyXWony2j81hJVHkA3zwk"
SUPABASE_URL = "https://unlmerrawfgybmkytebu.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVubG1lcnJhd2ZneWJta3l0ZWJ1Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MjEzMTg5MiwiZXhwIjoyMDg3NzA3ODkyfQ.ezV9b3KHpFLkdNXBw_yq8tjsmntlPmEgdSEkGTh-8j8"
ADMIN_IDS = [8537079657]  # your telegram id

# ================= INIT =================
app = Flask(__name__)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

updater = Updater(BOT_TOKEN, use_context=True)
bot = updater.bot
dp = updater.dispatcher

logging.basicConfig(level=logging.INFO)

# ================= DB FUNCTIONS =================
def get_user(uid):
    res = supabase.table("users").select("*").eq("id", uid).execute()
    return res.data[0] if res.data else None

def create_user(uid, ref=None):
    supabase.table("users").insert({
        "id": uid,
        "ref_by": ref,
        "points": 0,
        "verified": False,
        "device_id": None
    }).execute()

def update_user(uid, data):
    supabase.table("users").update(data).eq("id", uid).execute()

def get_channels():
    return supabase.table("channels").select("*").execute().data

def get_setting(key):
    res = supabase.table("settings").select("*").eq("key", key).execute().data
    return int(res[0]["value"]) if res else 5

# ================= START =================
def start(update, context):
    uid = update.message.from_user.id
    args = context.args

    ref = None
    if args and "ref_" in args[0]:
        ref = int(args[0].split("_")[1])

    if not get_user(uid):
        create_user(uid, ref)

    channels = get_channels()
    keyboard = []

    for ch in channels:
        keyboard.append([InlineKeyboardButton("📢 Join Channel", url=ch["invite_link"])])

    keyboard.append([InlineKeyboardButton("✅ Joined All Channels", callback_data="check_join")])

    update.message.reply_text(
        "📢 Join all channels:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= FORCE JOIN =================
def check_join(update, context):
    query = update.callback_query
    uid = query.from_user.id
    channels = get_channels()

    for ch in channels:
        try:
            username = ch["invite_link"].split("/")[-1]
            member = bot.get_chat_member(username, uid)
            if member.status not in ["member", "administrator", "creator"]:
                query.answer("❌ Join all channels")
                return
        except:
            query.answer("❌ Join all channels")
            return

    keyboard = [
        [InlineKeyboardButton("🔐 Verify Now", url=f"https://YOUR-RENDER.onrender.com/verify?user_id={uid}")],
        [InlineKeyboardButton("✅ Complete Verification", callback_data="done_verify")]
    ]

    query.message.reply_text("🔒 Verify yourself:", reply_markup=InlineKeyboardMarkup(keyboard))

# ================= COMPLETE VERIFY =================
def done_verify(update, context):
    query = update.callback_query
    user = get_user(query.from_user.id)

    if not user or not user["verified"]:
        query.answer("❌ Not verified")
        return

    # referral reward
    if user["ref_by"]:
        ref_user = get_user(user["ref_by"])
        if ref_user:
            update_user(ref_user["id"], {"points": ref_user["points"] + 1})
            bot.send_message(ref_user["id"], "🎉 New referral joined!")

    keyboard = ReplyKeyboardMarkup([
        ["📊 Stats", "🔗 Referral Link"],
        ["💸 Withdraw", "📦 Stock"]
    ], resize_keyboard=True)

    query.message.reply_text("✅ Verified!", reply_markup=keyboard)

# ================= MENU =================
def menu(update, context):
    uid = update.message.from_user.id
    user = get_user(uid)
    text = update.message.text

    if text == "📊 Stats":
        update.message.reply_text(f"Points: {user['points']}")

    elif text == "🔗 Referral Link":
        link = f"https://t.me/YOUR_BOT?start=ref_{uid}"
        update.message.reply_text(link)

    elif text == "💸 Withdraw":
        need = get_setting("withdraw_points")

        if user["points"] < need:
            update.message.reply_text(f"❌ Need {need} points")
            return

        coupons = supabase.table("coupons").select("*").eq("used", False).limit(1).execute().data

        if not coupons:
            update.message.reply_text("❌ No stock")
            return

        code = coupons[0]["code"]

        supabase.table("coupons").update({"used": True}).eq("id", coupons[0]["id"]).execute()
        update_user(uid, {"points": user["points"] - need})

        supabase.table("logs").insert({"user_id": uid, "action": "redeem"}).execute()

        update.message.reply_text(f"🎁 Coupon: {code}")

        for admin in ADMIN_IDS:
            bot.send_message(admin, f"User {uid} redeemed coupon")

    elif text == "📦 Stock":
        total = supabase.table("coupons").select("*").execute().data
        unused = supabase.table("coupons").select("*").eq("used", False).execute().data
        update.message.reply_text(f"Stock: {len(unused)}/{len(total)}")

    # ADMIN PANEL
    elif text == "/admin" and uid in ADMIN_IDS:
        keyboard = ReplyKeyboardMarkup([
            ["➕ Add Coupon", "➖ Remove Coupon"],
            ["📢 Add Channel", "❌ Remove Channel"],
            ["💰 Set Points", "📦 Stock"],
            ["📜 Logs"]
        ], resize_keyboard=True)

        update.message.reply_text("Admin Panel", reply_markup=keyboard)

    elif text == "➕ Add Coupon" and uid in ADMIN_IDS:
        update.message.reply_text("Send coupons line by line")

    elif "\n" in text and uid in ADMIN_IDS:
        for line in text.split("\n"):
            supabase.table("coupons").insert({"code": line, "used": False}).execute()
        update.message.reply_text("✅ Coupons added")

    elif text == "➖ Remove Coupon" and uid in ADMIN_IDS:
        update.message.reply_text("Send number to remove")

    elif text.isdigit() and uid in ADMIN_IDS:
        num = int(text)
        coupons = supabase.table("coupons").select("*").eq("used", False).limit(num).execute().data
        for c in coupons:
            supabase.table("coupons").delete().eq("id", c["id"]).execute()
        update.message.reply_text("❌ Removed")

    elif text.startswith("/addchannel") and uid in ADMIN_IDS:
        link = text.split(" ")[1]
        supabase.table("channels").insert({"invite_link": link}).execute()
        update.message.reply_text("✅ Channel added")

    elif text.startswith("/removechannel") and uid in ADMIN_IDS:
        link = text.split(" ")[1]
        supabase.table("channels").delete().eq("invite_link", link).execute()
        update.message.reply_text("❌ Channel removed")

    elif text == "💰 Set Points" and uid in ADMIN_IDS:
        update.message.reply_text("Send new withdraw points")

    elif text.isdigit() and uid in ADMIN_IDS:
        supabase.table("settings").update({"value": text}).eq("key", "withdraw_points").execute()
        update.message.reply_text("✅ Updated")

    elif text == "📜 Logs" and uid in ADMIN_IDS:
        logs = supabase.table("logs").select("*").limit(10).execute().data
        msg = "\n".join([f"{l['user_id']} - {l['action']}" for l in logs])
        update.message.reply_text(msg or "No logs")

# ================= WEB VERIFY =================
@app.route("/verify", methods=["GET", "POST"])
def verify():
    if request.method == "GET":
        uid = request.args.get("user_id")

        return f"""
        <html>
        <body style="display:flex;justify-content:center;align-items:center;height:100vh;background:linear-gradient(135deg,#667eea,#764ba2);color:white;">
        <div style="background:rgba(255,255,255,0.1);padding:40px;border-radius:20px;text-align:center;">
        <h1>Verify</h1>
        <button onclick="verify()" style="padding:15px;border:none;border-radius:10px;">Verify Now</button>
        </div>
        <script>
        function verify(){{
            fetch('/verify', {{
                method:'POST',
                headers:{{'Content-Type':'application/json'}},
                body:JSON.stringify({{user_id:{uid},device_id:navigator.userAgent}})
            }}).then(r=>r.text()).then(d=>document.body.innerHTML="<h2>"+d+"</h2>")
        }}
        </script>
        </body>
        </html>
        """

    data = request.json
    uid = data["user_id"]
    device = data["device_id"]

    exists = supabase.table("users").select("*").eq("device_id", device).execute().data

    if exists:
        return "❌ Device already used"

    update_user(uid, {"verified": True, "device_id": device})
    return "✅ Verified! Go back to bot"

# ================= RUN =================
def run_bot():
    updater.start_polling()

Thread(target=run_bot).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
