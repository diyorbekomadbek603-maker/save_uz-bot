import asyncio
import glob
import logging
import os
import re
import shutil
import tempfile

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from yt_dlp import YoutubeDL

# ====================== SOZLAMALAR ======================
TOKEN = "9008725798:AAFa2ZOR4tKkaeZpCALYYRRObmRYTWgqnKg"  # <-- o'z tokeningizni shu yerga qo'ying
MAX_SIZE = 50 * 1024 * 1024  # Telegram bot limiti
MIN_DUR, MAX_DUR = 40, 720  # qo'shiq davomiyligi (soniya): 40s - 12 daqiqa
RESULTS = 5
PARALLEL_DOWNLOADS = 3

BTN_TREND_WORLD = "🔥 Trend qo'shiqlar"
BTN_TREND_UZ = "🇺🇿 O'zbek trend"
BTN_HELP = "ℹ️ Yordam"
TREND_QUERIES = {
    "w": "top hits 2026 official audio",
    "u": "o'zbek qo'shiqlari 2026 yangi hit",
}

IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[^\s]+")
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}

dp = Dispatcher()
SEM = asyncio.Semaphore(PARALLEL_DOWNLOADS)
AUDIO_CACHE: dict[str, str] = {}  # video_id -> telegram file_id (tezlik uchun)
BUSY: set[tuple] = set()  # ikki marta bosilishdan himoya

MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_TREND_WORLD), KeyboardButton(text=BTN_TREND_UZ)],
        [KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
)

HELP_TEXT = (
    "Salom! 👋\n\n"
    "🎬 Instagram havolasini yuboring, videoni yuklab beraman.\n"
    "🎵 Qo'shiq yoki ijrochi nomini yozing, 5 ta variant chiqadi.\n"
    "🔥 Trend qo'shiqlar uchun pastdagi tugmalardan foydalaning."
)


# ====================== YORDAMCHI FUNKSIYALAR ======================
def fmt_time(sec) -> str:
    if not sec:
        return "?"
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def clean_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", text or "")[:60].strip() or "musiqa"


async def with_retry(func, *args, tries=3, **kwargs):
    """Internet uzilib qolsa, qayta urinadi."""
    for attempt in range(tries):
        try:
            return await func(*args, **kwargs)
        except (TelegramNetworkError, asyncio.TimeoutError):
            if attempt == tries - 1:
                raise
            await asyncio.sleep(2 * (attempt + 1))


async def safe_text(m: Message, text: str, **kwargs):
    try:
        return await m.answer(text, **kwargs)
    except Exception:
        logging.exception("Xabar yuborib bo'lmadi")


async def safe_delete(m: Message | None):
    if m:
        try:
            await m.delete()
        except Exception:
            pass


async def run_blocking(func, *args):
    async with SEM:
        return await asyncio.to_thread(func, *args)


# ====================== YUKLASH (yt-dlp) ======================
def _download_instagram(url: str, folder: str) -> list[str]:
    opts = {
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "format": "best[ext=mp4][filesize<?48M]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    if os.path.exists("cookies.txt"):
        opts["cookiefile"] = "cookies.txt"
    with YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    files = [
        f
        for f in sorted(glob.glob(os.path.join(folder, "*")))
        if not f.endswith((".part", ".ytdl"))
    ]
    if not files:
        raise RuntimeError("Instagramdan fayl topilmadi")
    return files


def _search(query: str) -> list[dict]:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "socket_timeout": 30,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch15:{query}", download=False)
    results = []
    for e in (info or {}).get("entries") or []:
        if not e or not e.get("id"):
            continue
        dur = e.get("duration")
        if dur and not (MIN_DUR <= dur <= MAX_DUR):
            continue
        results.append(
            {
                "id": e["id"],
                "title": e.get("title") or "Noma'lum",
                "duration": dur,
            }
        )
        if len(results) >= RESULTS:
            break
    return results


def _download_audio(video_id: str, folder: str):
    opts = {
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(
            f"https://www.youtube.com/watch?v={video_id}", download=True
        )
    files = [
        f
        for f in glob.glob(os.path.join(folder, "*"))
        if not f.endswith((".part", ".ytdl"))
    ]
    if not files:
        raise RuntimeError("Audio yuklanmadi")
    return (
        files[0],
        info.get("title") or "Musiqa",
        info.get("uploader") or info.get("channel") or "",
        info.get("duration"),
    )


# ====================== YUBORISH ======================
async def send_media(m: Message, files: list[str]) -> int:
    sent = 0
    for path in files:
        if os.path.getsize(path) > MAX_SIZE:
            continue
        ext = os.path.splitext(path)[1].lower()
        caption = "✅ Tayyor!" if sent == 0 else None
        if ext in VIDEO_EXT:
            await with_retry(
                m.answer_video,
                FSInputFile(path),
                supports_streaming=True,
                caption=caption,
            )
        elif ext in PHOTO_EXT:
            await with_retry(m.answer_photo, FSInputFile(path), caption=caption)
        else:
            continue
        sent += 1
    return sent


def results_keyboard(results: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(results, 1):
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{i}. {r['title'][:48]} • {fmt_time(r['duration'])}",
                    callback_data=f"a:{r['id']}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="🔥 Trend", callback_data="t:w"),
            InlineKeyboardButton(text="🇺🇿 O'zbek trend", callback_data="t:u"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_results(m: Message, query: str, header: str):
    wait = await safe_text(m, "🔎 Qidirilmoqda...")
    try:
        results = await asyncio.to_thread(_search, query)
        if not results:
            await safe_text(m, "❌ Hech narsa topilmadi. Boshqacha yozib ko'ring.")
            return
        await with_retry(m.answer, header, reply_markup=results_keyboard(results))
    except Exception:
        logging.exception("Qidiruvda xato")
        await safe_text(m, "❌ Qidirib bo'lmadi. Birozdan keyin qayta urinib ko'ring.")
    finally:
        await safe_delete(wait)


async def handle_instagram(m: Message, url: str):
    status = await safe_text(m, "⏳ Video yuklanmoqda...")
    folder = tempfile.mkdtemp()
    try:
        files = await run_blocking(_download_instagram, url, folder)
        sent = await send_media(m, files)
        if sent == 0:
            await safe_text(m, "❌ Fayl 50 MB dan katta, yubora olmayman.")
    except Exception:
        logging.exception("Instagramda xato")
        await safe_text(
            m,
            "❌ Yuklab bo'lmadi. Havola to'g'riligini va akkaunt yopiq emasligini tekshiring.",
        )
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        await safe_delete(status)


# ====================== HANDLERLAR ======================
@dp.message(CommandStart())
@dp.message(Command("help"))
@dp.message(F.text == BTN_HELP)
async def cmd_start(m: Message):
    await safe_text(m, HELP_TEXT, reply_markup=MENU)


@dp.message(F.text == BTN_TREND_WORLD)
async def trend_world(m: Message):
    await show_results(m, TREND_QUERIES["w"], "🔥 Trend qo'shiqlar:")


@dp.message(F.text == BTN_TREND_UZ)
async def trend_uz(m: Message):
    await show_results(m, TREND_QUERIES["u"], "🇺🇿 O'zbek trend qo'shiqlar:")


@dp.callback_query(F.data.startswith("t:"))
async def cb_trend(call: CallbackQuery):
    try:
        await call.answer()
    except Exception:
        pass
    key = call.data[2:]
    header = "🔥 Trend qo'shiqlar:" if key == "w" else "🇺🇿 O'zbek trend qo'shiqlar:"
    await show_results(call.message, TREND_QUERIES.get(key, TREND_QUERIES["w"]), header)


@dp.callback_query(F.data.startswith("a:"))
async def cb_audio(call: CallbackQuery):
    video_id = call.data[2:]
    chat_id = call.message.chat.id
    try:
        await call.answer("⏳ Yuklanmoqda...")
    except Exception:
        pass

    key = (chat_id, video_id)
    if key in BUSY:
        return
    BUSY.add(key)

    status = None
    folder = tempfile.mkdtemp()
    try:
        cached = AUDIO_CACHE.get(video_id)
        if cached:
            await with_retry(call.message.answer_audio, cached)
            return

        status = await safe_text(call.message, "⏳ Musiqa yuklanmoqda...")
        path, title, artist, duration = await run_blocking(
            _download_audio, video_id, folder
        )
        if os.path.getsize(path) > MAX_SIZE:
            await safe_text(call.message, "❌ Fayl 50 MB dan katta, boshqa variantni tanlang.")
            return

        ext = os.path.splitext(path)[1]
        sent = await with_retry(
            call.message.answer_audio,
            FSInputFile(path, filename=f"{clean_name(title)}{ext}"),
            title=title,
            performer=artist,
            duration=int(duration) if duration else None,
        )
        if sent and sent.audio:
            AUDIO_CACHE[video_id] = sent.audio.file_id
    except Exception:
        logging.exception("Musiqa yuklashda xato")
        await safe_text(call.message, "❌ Yuklab bo'lmadi, boshqa variantni tanlang.")
    finally:
        BUSY.discard(key)
        shutil.rmtree(folder, ignore_errors=True)
        await safe_delete(status)


@dp.message()
async def handle_text(m: Message):
    text = (m.text or "").strip()
    if not text:
        await safe_text(m, "Iltimos, Instagram havolasi yoki qo'shiq nomini yozing.")
        return

    match = IG_RE.search(text)
    if match:
        await handle_instagram(m, match.group(0))
    elif len(text) < 2:
        await safe_text(m, "Qo'shiq yoki ijrochi nomini to'liqroq yozing.")
    else:
        await show_results(m, text, f"🎵 «{text[:40]}» bo'yicha natijalar:")


# ====================== ISHGA TUSHIRISH ======================
async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=TOKEN, session=AiohttpSession(timeout=300))
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
            break
        except TelegramNetworkError as e:
            print("Internet xatosi, 5 soniyadan keyin qayta uriniladi:", e)
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
