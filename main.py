from flask import Flask,request
from telegram import *
from telegram.ext import *

from config import *
from database import *

app=Flask(__name__)

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

async def start(update,context):

    user=update.effective_user
    args=context.args

    ref=None

    if args:
        ref=int(args[0])

    if not get_user(user.id):
        create_user(user.id,user.username,ref)

    await update.message.reply_text(
        "Welcome to Refer Bot",
        reply_markup=user_menu(user.id)
    )

async def text(update,context):

    msg=update.message.text
    user=update.effective_user
    u=get_user(user.id)

    if msg=="💰 BALANCE":

        await update.message.reply_text(
f"""💰 Your Points

⭐ Points: {u['points']}
👥 Referrals: {u['referrals']}

🎁 Voucher Cost: 3"""
        )

    elif msg=="🤝 REFER":

        bot=await context.bot.get_me()

        link=f"https://t.me/{bot.username}?start={user.id}"

        await update.message.reply_text(
f"""🤝 Refer & Earn

Invite friends using your link

{link}

Each verified user gives +1 point"""
        )

    elif msg=="📦 STOCK":

        c=supabase.table("coupons").select("*").eq("used",False).execute()

        await update.message.reply_text(
            f"SHEIN COUPON STOCK: {len(c.data)}"
        )

    elif msg=="🏆 LEADERBOARD":

        users=supabase.table("users").select("*").order("referrals",desc=True).limit(10).execute().data

        text="🏆 Top 10 Leaderboard\n\n"

        i=1

        for x in users:
            text+=f"{i}. {x['username']} - {x['referrals']} refs\n"
            i+=1

        await update.message.reply_text(text)

    elif msg=="⚙ ADMIN PANEL" and user.id in ADMINS:

        await update.message.reply_text(
            "Admin Panel",
            reply_markup=admin_menu()
        )

    elif msg=="📢 BROADCAST" and user.id in ADMINS:

        context.user_data["broadcast"]=True
        await update.message.reply_text("Send message to broadcast")

    elif context.user_data.get("broadcast"):

        users=supabase.table("users").select("id").execute().data

        sent=0

        for u in users:

            try:
                await context.bot.send_message(u["id"],msg)
                sent+=1
            except:
                pass

        await update.message.reply_text(f"Broadcast sent to {sent} users")

        context.user_data["broadcast"]=False

    elif msg=="➕ ADD COUPON" and user.id in ADMINS:

        context.user_data["addcoupon"]=True
        await update.message.reply_text("Send coupons line by line")

    elif context.user_data.get("addcoupon"):

        codes=msg.split("\n")

        for c in codes:
            supabase.table("coupons").insert({"code":c}).execute()

        await update.message.reply_text("Coupons added")

        context.user_data["addcoupon"]=False

    elif msg=="➖ REMOVE COUPON" and user.id in ADMINS:

        context.user_data["removecoupon"]=True
        await update.message.reply_text("Send number")

    elif context.user_data.get("removecoupon"):

        n=int(msg)

        coupons=supabase.table("coupons").select("*").eq("used",False).limit(n).execute()

        for c in coupons.data:
            supabase.table("coupons").delete().eq("id",c["id"]).execute()

        await update.message.reply_text("Coupons removed")

        context.user_data["removecoupon"]=False

async def track_leave(update,context):

    user=update.chat_member.from_user.id

    u=get_user(user)

    if not u:
        return

    if update.chat_member.new_chat_member.status=="left":

        ref=u["referred_by"]

        if ref:

            r=get_user(ref)

            if r["points"]>0:

                supabase.table("users").update({
                    "points":r["points"]-1
                }).eq("id",ref).execute()

                await context.bot.send_message(
                    ref,
                    "⚠ Referral left channel\n-1 point deducted"
                )

application=Application.builder().token(BOT_TOKEN).build()

application.add_handler(CommandHandler("start",start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text))
application.add_handler(ChatMemberHandler(track_leave))

@app.route(f"/{BOT_TOKEN}",methods=["POST"])
async def webhook():

    update=Update.de_json(request.get_json(),application.bot)

    await application.process_update(update)

    return "ok"

@app.route("/")
def home():
    return "Bot running"

if __name__=="__main__":

    application.bot.set_webhook(f"{WEBHOOK_URL}/{BOT_TOKEN}")

    app.run(host="0.0.0.0",port=10000)
