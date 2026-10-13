import asyncio
import os
import re
import tempfile
from pathlib import Path

import yt_dlp
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# ==========================================
# SOZLAMALAR
# ==========================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi! .env fayliga bot tokeningizni yozing."
    )

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher()

MAX_FILE_SIZE = 49 * 1024 * 1024

# Har bir foydalanuvchining qidiruv natijalari
search_results = {}


# ==========================================
# ASOSIY MENYU
# ==========================================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🎵 Musiqa qidirish"),
                KeyboardButton(text="📸 Instagram video"),
            ],
            [
                KeyboardButton(text="▶️ YouTube qidirish"),
                KeyboardButton(text="ℹ️ Yordam"),
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Qo‘shiq nomi yoki havola yuboring..."
    )


def results_keyboard(results):
    rows = []

    for index, item in enumerate(results):
        rows.append([
            InlineKeyboardButton(
                text=f"🎧 MP3 • {index + 1}",
                callback_data=f"dl:a:{index}"
            ),
            InlineKeyboardButton(
                text=f"🎬 Video • {index + 1}",
                callback_data=f"dl:v:{index}"
            ),
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================================
# YORDAMCHI FUNKSIYALAR
# ==========================================

def is_media_url(text):
    if not text:
        return False

    return bool(
        re.search(
            r"https?://(?:www\.)?"
            r"(?:instagram\.com|instagr\.am|youtube\.com|youtu\.be)/",
            text,
            re.IGNORECASE
        )
    )


def search_music(query):
    """YouTube'dan musiqa va videolarni qidiradi."""

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        data = ydl.extract_info(
            f"ytsearch5:{query}",
            download=False
        )

    return data.get("entries", []) if data else []


def download_media(url, folder, kind):
    """Videoni yoki MP3 audioni yuklab oladi."""

    options = {
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 2,
        "max_filesize": MAX_FILE_SIZE,
    }

    if kind == "audio":
        options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    else:
        options.update({
            "format": "best[ext=mp4]/best",
        })

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    folder_path = Path(folder)

    if kind == "audio":
        extensions = {".mp3"}
    else:
        extensions = {
            ".mp4", ".webm", ".mkv", ".mov", ".m4v"
        }

    files = [
        path for path in folder_path.iterdir()
        if path.is_file() and path.suffix.lower() in extensions
    ]

    if not files:
        raise RuntimeError(
            "Yuklangan fayl topilmadi. Havola ishlamasligi mumkin."
        )

    files.sort(key=lambda path: path.stat().st_size, reverse=True)
    result = files[0]

    if result.stat().st_size > MAX_FILE_SIZE:
        raise RuntimeError(
            "Fayl juda katta. 49 MB dan kichikroq fayl kerak."
        )

    return str(result)


async def send_download(message, url, kind, title=""):
    """Yuklash jarayonini foydalanuvchiga ko'rsatadi."""

    if kind == "audio":
        status = await message.answer(
            "🎵 <b>Musiqa tayyorlanmoqda...</b>\n"
            "⏳ Iltimos, biroz kuting..."
        )
    else:
        status = await message.answer(
            "📥 <b>Video yuklanmoqda...</b>\n"
            "⚙️ Havola tekshirilmoqda..."
        )

    try:
        with tempfile.TemporaryDirectory(prefix="telegram_bot_") as folder:
            file_path = await asyncio.to_thread(
                download_media,
                url,
                folder,
                kind
            )

            upload = FSInputFile(file_path)

            if kind == "audio":
                await message.answer_audio(
                    audio=upload,
                    title=(title or "Musiqa")[:64],
                    caption="🎧 Musiqa tayyor!"
                )
            elif Path(file_path).suffix.lower() in {
                ".mp4", ".m4v", ".mov"
            }:
                await message.answer_video(
                    video=upload,
                    caption="🎬 Video tayyor!"
                )
            else:
                await message.answer_document(
                    document=upload,
                    caption="🎬 Video fayli tayyor!"
                )

        await status.delete()

    except Exception as error:
        print("Yuklash xatosi:", repr(error))

        try:
            await status.edit_text(
                "❌ <b>Yuklab bo‘lmadi.</b>\n\n"
                "Sabablari:\n"
                "• Havola noto‘g‘ri yoki yopiq bo‘lishi mumkin.\n"
                "• Video platforma tomonidan cheklangan bo‘lishi mumkin.\n"
                "• FFmpeg o‘rnatilmagan bo‘lishi mumkin.\n"
                "• Fayl hajmi juda katta bo‘lishi mumkin.\n\n"
                "🔄 Boshqa havolani sinab ko‘ring."
            )
        except Exception:
            await message.answer(
                "❌ Yuklab bo‘lmadi. Boshqa havolani sinab ko‘ring."
            )


# ==========================================
# /START
# ==========================================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "✨ <b>ASSALOMU ALAYKUM!</b>\n\n"
        "🤖 <b>Media Bot</b> xizmatiga xush kelibsiz!\n\n"
        "🎵 Qo‘shiq yoki xonanda nomini yozing.\n"
        "📸 Instagram havolasini yuboring.\n"
        "▶️ YouTube videosini qidiring.\n\n"
        "👇 Kerakli bo‘limni tanlang:",
        reply_markup=main_menu()
    )


# ==========================================
# MENYU TUGMALARI
# ==========================================

@dp.message(F.text == "🎵 Musiqa qidirish")
async def music_menu(message: Message):
    await message.answer(
        "🎵 <b>Musiqa qidirish</b>\n\n"
        "Xonanda yoki qo‘shiq nomini yozing.\n\n"
        "Masalan:\n"
        "• Konsta\n"
        "• Yomg‘ir\n"
        "• Sevara Nazarkhan",
        reply_markup=main_menu()
    )


@dp.message(F.text == "📸 Instagram video")
async def instagram_menu(message: Message):
    await message.answer(
        "📸 <b>Instagram video</b>\n\n"
        "Instagram'dagi ochiq video yoki Reel havolasini "
        "shu yerga yuboring.\n\n"
        "⏳ Bot videoni yuklashga harakat qiladi.",
        reply_markup=main_menu()
    )


@dp.message(F.text == "▶️ YouTube qidirish")
async def youtube_menu(message: Message):
    await message.answer(
        "▶️ <b>YouTube qidirish</b>\n\n"
        "Video yoki musiqa nomini yozing.\n"
        "Men mos natijalarni chiqaraman.",
        reply_markup=main_menu()
    )


@dp.message(F.text == "ℹ️ Yordam")
async def help_menu(message: Message):
    await message.answer(
        "ℹ️ <b>Botdan foydalanish</b>\n\n"
        "1. 🎵 Musiqa uchun qo‘shiq nomini yozing.\n"
        "2. 📸 Instagram uchun havola yuboring.\n"
        "3. ▶️ Qidiruv natijasidan kerakli tugmani bosing.\n\n"
        "⚠️ Faqat o‘zingizga tegishli yoki yuklashga "
        "ruxsatingiz bo‘lgan kontentdan foydalaning.",
        reply_markup=main_menu()
    )


# ==========================================
# INSTAGRAM / YOUTUBE HAVOLASI
# ==========================================

@dp.message(F.text)
async def handle_text(message: Message):
    text = message.text.strip()

    if text.startswith("/"):
        return

    if is_media_url(text):
        await send_download(
            message=message,
            url=text,
            kind="video"
        )
        return

    query = text

    status = await message.answer(
        f"🔎 <b>Qidirilmoqda:</b> {query}\n"
        "🎧 Natijalar tayyorlanmoqda..."
    )

    try:
        entries = await asyncio.to_thread(search_music, query)

        results = []

        for item in entries:
            if not item:
                continue

            url = item.get("url")
            title = item.get("title") or "Nomsiz natija"

            if url and not url.startswith("http"):
                video_id = item.get("id")
                if video_id:
                    url = f"https://www.youtube.com/watch?v={video_id}"

            if url:
                results.append({
                    "url": url,
                    "title": title
                })

        if not results:
            await status.edit_text(
                "😕 Afsuski, mos natija topilmadi.\n"
                "Boshqa nom bilan qidirib ko‘ring."
            )
            return

        search_results[message.from_user.id] = results

        response = "🎵 <b>Qidiruv natijalari:</b>\n\n"

        for index, item in enumerate(results, start=1):
            response += (
                f"{index}. {item['title']}\n"
                f"🔗 {item['url']}\n\n"
            )

        response += (
            "👇 Kerakli natija uchun MP3 yoki Video tugmasini bosing."
        )

        await status.edit_text(
            response,
            reply_markup=results_keyboard(results),
            disable_web_page_preview=True
        )

    except Exception as error:
        print("Qidiruv xatosi:", repr(error))

        await status.edit_text(
            "❌ Qidiruv amalga oshmadi.\n\n"
            "Internetni tekshiring va yana urinib ko‘ring."
        )


# ==========================================
# NATIJADAN MP3 YOKI VIDEO YUKLASH
# ==========================================

@dp.callback_query(F.data.startswith("dl:"))
async def download_result(callback: CallbackQuery):
    await callback.answer("⏳ So‘rovingiz qabul qilindi...")

    try:
        _, kind_code, index_text = callback.data.split(":")
        index = int(index_text)

        results = search_results.get(callback.from_user.id, [])

        if index < 0 or index >= len(results):
            await callback.message.answer(
                "⚠️ Natijalar eskirgan. Qaytadan qidiring."
            )
            return

        item = results[index]

        kind = "audio" if kind_code == "a" else "video"

        await send_download(
            message=callback.message,
            url=item["url"],
            kind=kind,
            title=item["title"]
        )

    except Exception as error:
        print("Tugma xatosi:", repr(error))
        await callback.message.answer(
            "❌ So‘rovni bajarib bo‘lmadi. Qayta urinib ko‘ring."
        )


# ==========================================
# BOTNI ISHGA TUSHIRISH
# ==========================================

async def main():
    print("🚀 Bot ishga tushdi!")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        pass