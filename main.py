import hashlib
from flask import Flask,request,jsonify
from telegram import *
from telegram.ext import *
from supabase import create_client

BOT_TOKEN="8749953303:AAF9X6jIEh7sm5DZKj5piOlOvpCwlQXpeQA"
WEBHOOK_URL="https://autoearnx-refer-bot.onrender.com"

ADMINS=[8537079657,5351543874]

SUPABASE_URL="https://gayvtqrtmwgsoicxreok.supabase.co"
SUPABASE_KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdheXZ0cXJ0bXdnc29pY3hyZW9rIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3MzYwMDU0NSwiZXhwIjoyMDg5MTc2NTQ1fQ.n0W4jag3XM0yEM2T9WPOTxtvtS81zhN_Zsch2aTuN3k"

supabase=create_client(SUPABASE_URL,SUPABASE_KEY)

app=Flask(__name__)

def get_user(uid):
    r=supabase.table("users").select("*").eq("id",uid).execute()
    return r.data[0] if r.data else None

def create_user(uid,username,ref):
    supabase.table("users").insert({
        "id":uid,
        "username":username,
        "referred_by":ref
    }).execute()

def user_menu(uid):

    kb=[
        ["💰 BALANCE","🤝 REFER"],
        ["🎁 WITHDRAW","📜 MY VOUCHERS"],
        ["📦 STOCK","🏆 LEADERBOARD"]
    ]

    if uid in ADMINS:
        kb.append(["⚙ ADMIN PANEL"])

    return ReplyKeyboardMarkup(kb,resize_keyboard=True)

def admin_menu():

    kb=[
        ["📢 BROADCAST"],
        ["➕ ADD COUPON","➖ REMOVE COUPON"],
        ["➕ ADD CHANNEL","➖ REMOVE CHANNEL"],
        ["🎁 GET FREE CODE"],
        ["⚙ CHANGE WITHDRAW POINTS"],
        ["⬅ BACK"]
    ]

    return ReplyKeyboardMarkup(kb,resize_keyboard=True)

async def check_force_join(bot,uid):

    channels=supabase.table("channels").select("*").execute().data

    not_joined=[]

    for ch in channels:

        member=await bot.get_chat_member(ch["channel_id"],uid)

        if member.status in ["left","kicked"]:
            not_joined.append(ch)

    return not_joined

async def start(update,context):

    user=update.effective_user
    args=context.args

    ref=None

    if args:
        ref=int(args[0])

    if not get_user(user.id):
        create_user(user.id,user.username,ref)

    not_joined=await check_force_join(context.bot,user.id)

    if not_joined:

        buttons=[]

        for ch in not_joined:
            buttons.append([InlineKeyboardButton("Join Channel",url=ch["link"])])

        buttons.append([
            InlineKeyboardButton(
                "I Joined All Channels",
                web_app=WebAppInfo(url=f"{WEBHOOK_URL}/verify.html")
            )
        ])

        await update.message.reply_text(
            "Join all channels then verify",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

        return

    await update.message.reply_text(
        "Welcome",
        reply_markup=user_menu(user.id)
    )

@app.route("/verify_device",methods=["POST"])
def verify_device():

    data=request.json

    device_hash=hashlib.sha256(
        data["fingerprint"].encode()
    ).hexdigest()

    uid=data["user_id"]

    existing=supabase.table("devices") \
        .select("*") \
        .eq("device_hash",device_hash) \
        .execute()

    if existing.data:
        return jsonify({"status":"declined"})

    supabase.table("devices").insert({
        "device_hash":device_hash,
        "user_id":uid
    }).execute()

    user=get_user(uid)

    if user and user["referred_by"]:

        ref=user["referred_by"]

        supabase.table("users").update({
            "points":user["points"]+1,
            "referrals":user["referrals"]+1
        }).eq("id",ref).execute()

    supabase.table("users").update({
        "verified":True
    }).eq("id",uid).execute()

    return jsonify({"status":"verified"})

async def text(update,context):

    msg=update.message.text
    user=update.effective_user
    u=get_user(user.id)

    if msg=="💰 BALANCE":

        await update.message.reply_text(
f"""💰 Your Points

⭐ Points: {u['points']}
👥 Referrals: {u['referrals']}"""
        )

    elif msg=="🤝 REFER":

        bot=await context.bot.get_me()

        link=f"https://t.me/{bot.username}?start={user.id}"

        await update.message.reply_text(
f"""Invite friends

{link}

Each verified user gives +1 point"""
        )

    elif msg=="📦 STOCK":

        c=supabase.table("coupons").select("*").eq("used",False).execute()

        await update.message.reply_text(
            f"SHEIN COUPON STOCK: {len(c.data)}"
        )

    elif msg=="🏆 LEADERBOARD":

        users=supabase.table("users") \
        .select("*") \
        .order("referrals",desc=True) \
        .limit(10) \
        .execute().data

        text="🏆 Leaderboard\n\n"

        i=1

        for x in users:
            text+=f"{i}. {x['username']} - {x['referrals']}\n"
            i+=1

        await update.message.reply_text(text)

    elif msg=="⚙ ADMIN PANEL" and user.id in ADMINS:

        await update.message.reply_text(
            "Admin Panel",
            reply_markup=admin_menu()
        )

application=Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start",start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text))

from flask import Flask, request
from telegram import Update
from telegram.ext import Application

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
async def webhook():
    update = Update.de_json(request.get_json(), application.bot)
    await application.process_update(update)
    return "ok"

@app.route("/")
def home():
    return "Bot running"

if __name__=="__main__":

    application.bot.set_webhook(f"{WEBHOOK_URL}/{BOT_TOKEN}")

    app.run(host="0.0.0.0",port=10000)
