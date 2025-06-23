# main.py — Telegram Affiliate Bot  (עם DEBUG לקבוצה הפרטית)
# =========================================================
# Python 3.11 • python-telegram-bot v20.7

from __future__ import annotations
import logging, os, random, re
from collections import defaultdict
from datetime import datetime, timedelta, time as dtime, timezone
from pathlib import Path
from typing import Final

import aiosqlite
from dotenv import load_dotenv
from telegram import InputMediaPhoto, Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, Application,
    CommandHandler, ContextTypes, MessageHandler, filters,
)

# ---------- ENV ----------
load_dotenv()
BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "")
SOURCE_CHAT_ID: Final[int] = int(os.getenv("SOURCE_CHAT_ID", "0"))
DESTINATION_CHAT_ID: Final[int] = int(os.getenv("DESTINATION_CHAT_ID", "0"))
SHARE_LINK: Final[str] = os.getenv("SHARE_LINK", "")
TZ = timezone(timedelta(hours=3))          # Asia/Jerusalem

if not all((BOT_TOKEN, SOURCE_CHAT_ID, DESTINATION_CHAT_ID, SHARE_LINK)):
    raise SystemExit("❌ ‎.env חסר ערכים - BOT_TOKEN / SOURCE_CHAT_ID / DESTINATION_CHAT_ID / SHARE_LINK")

# ---------- CONST ----------
WINDOW = (dtime(9, 0), dtime(23, 30))
DELAY_MIN, DELAY_MAX = 20 * 60, 120 * 60
KEYWORDS = {"✅","⭐","🚚","🛒","mAh","Bluetooth","דירוג","קונים","משלוח","קיבולת","עמיד","USB-C","Type-C","LiFePO4","IP-"}

DB_PATH = Path("data") / "bot.db"; DB_PATH.parent.mkdir(exist_ok=True)

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ---------- HELPERS ----------
def in_window(ts: datetime) -> bool:
    a, b = WINDOW; return a <= ts.time() <= b

def next_time() -> datetime:
    base = datetime.now(TZ)
    while True:
        cand = base + timedelta(seconds=random.randint(DELAY_MIN, DELAY_MAX))
        if in_window(cand): return cand
        base = datetime.combine((cand+timedelta(days=1)).date(), WINDOW[0], tzinfo=TZ)

# ---------- BOT ----------
class AffiliateBot:
    def __init__(self):
        self.app: Application = ApplicationBuilder().token(BOT_TOKEN).post_init(self._init_db).build()
        self.pending: dict[int,list[Message]] = defaultdict(list)

        self.app.add_handler(CommandHandler("ping", self.ping))
        self.app.add_handler(CommandHandler("help", self.help))
        self.app.add_handler(CommandHandler("stats", self.stats))
        self.app.add_handler(MessageHandler(filters.Regex(r"^/פקודות"), self.help))
        self.app.add_handler(MessageHandler(filters.Chat(SOURCE_CHAT_ID) & (filters.TEXT|filters.PHOTO), self.on_msg))

    async def _init_db(self, _: Application):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""CREATE TABLE IF NOT EXISTS posts(
                id INTEGER PRIMARY KEY, user_id INTEGER, link TEXT, date_publish TEXT)""")
            await db.commit()

    # ---- COMMANDS ----
    async def ping(self,u:Update,ctx): await u.message.reply_text("pong 🏓")
    async def help(self,u:Update,ctx): await u.message.reply_text("/ping /help /פקודות /stats")
    async def stats(self,u:Update,ctx):
        uid=u.effective_user.id
        async with aiosqlite.connect(DB_PATH) as db:
            t=(await(db.execute("SELECT COUNT(*) FROM posts WHERE user_id=? AND date_publish>=date('now','start of day','localtime')",(uid,))).fetchone())[0]
            m=(await(db.execute("SELECT COUNT(*) FROM posts WHERE user_id=? AND strftime('%Y-%m',date_publish)=strftime('%Y-%m','now','localtime')",(uid,))).fetchone())[0]
            y=(await(db.execute("SELECT COUNT(*) FROM posts WHERE user_id=? AND strftime('%Y',date_publish)=strftime('%Y','now','localtime')",(uid,))).fetchone())[0]
        await u.message.reply_text(f"📊 סטטיסטיקה:\nהיום: {t}\nחודש: {m}\nשנה: {y}")

    # ---- MESSAGE FLOW ----
    async def on_msg(self, u:Update, ctx:ContextTypes.DEFAULT_TYPE):
        msg=u.effective_message; uid=msg.from_user.id
        preview=(msg.text or '').replace('\n',' ')[:40]
        await ctx.bot.send_message(SOURCE_CHAT_ID,f"🔹 DEBUG: קיבלתי: {preview or 'צילום'}")
        if msg.text and msg.text.strip()=="סיימתי":
            await self._process(uid,ctx); return
        self.pending[uid].append(msg)

    async def _process(self, uid:int, ctx:ContextTypes.DEFAULT_TYPE):
        batch=self.pending.pop(uid,[])
        if not batch:
            await ctx.bot.send_message(SOURCE_CHAT_ID,"🔹 DEBUG: אין הודעות ממתינות"); return

        texts,photos=[],[]
        for m in batch:
            if m.text: texts.append(m.text.strip())
            if m.photo: photos.append(m.photo[-1].file_id)

        full="\n".join(texts)
        link=re.search(r"https?://\S+",full)
        link=link.group(0) if link else ""
        title=texts[0].splitlines()[0] if texts else "מוצר חדש"
        body="\n".join({l.strip() for l in full.splitlines() if any(k.lower() in l.lower() for k in KEYWORDS)})
        caption=f"🔥 {title} 🔥\n{body}\n\n👉 להזמנה:\n{link}\n\n🔗 לשיתוף: {SHARE_LINK}"

        when=next_time(); delay=(when-datetime.now(TZ)).total_seconds()
        ctx.job_queue.run_once(self._publish,delay,data=(caption,photos[:4]))
        await ctx.bot.send_message(SOURCE_CHAT_ID,f"📌 DEBUG: יתפרסם ב-{when.strftime('%H:%M')}")

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO posts(user_id,link,date_publish) VALUES(?,?,?)",(uid,link,when.isoformat(sep=' '))); await db.commit()

    async def _publish(self, ctx:ContextTypes.DEFAULT_TYPE):
        caption,photos=ctx.job.data
        try:
            if photos:
                media=[InputMediaPhoto(p) for p in photos]
                media[0].caption,media[0].parse_mode=caption,ParseMode.HTML
                await ctx.bot.send_media_group(DESTINATION_CHAT_ID,media)
            else:
                await ctx.bot.send_message(DESTINATION_CHAT_ID,caption,parse_mode=ParseMode.HTML)
            await ctx.bot.send_message(SOURCE_CHAT_ID,"✅ DEBUG: פוסט פורסם")
        except Exception as e:
            log.exception("Publish failed: %s",e)
            await ctx.bot.send_message(SOURCE_CHAT_ID,f"❌ DEBUG: שגיאת פרסום {e}")

    # ---- RUN ----
    def run(self):
        log.info("Bot starting…")
        self.app.run_polling(close_loop=False)

# ---------- MAIN ----------
if __name__=="__main__":
    AffiliateBot().run()
