"""Личный бот: фото -> стикер, видео -> видеостикер или гифка (выбор кнопкой)."""

import asyncio
import logging
import re
import shutil
import tempfile
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.types import BotCommand, CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import load_config
from media import ConversionError, Converter
from packs import PackManager

log = logging.getLogger("tg-sticker")

DOWNLOAD_LIMIT = 20 * 1024 * 1024  # больше Bot API скачать не даст
PENDING_LIMIT = 50

HELP = (
    "Пришли фото — сделаю стикер и добавлю в твой пак.\n"
    "Пришли видео или гифку — спрошу, стикер или гифка.\n\n"
    "В подписи можно указать эмодзи и время начала, например: <code>😂 1:05</code>\n"
    "Видеостикер берёт 3 секунды с этого места, гифка — до {gif} с.\n\n"
    "/pack — ссылки на паки\n"
    "/del — ответь этим на стикер, чтобы удалить его из пака"
)

cfg = load_config()
bot = Bot(cfg.bot_token)
converter = Converter(cfg.ffmpeg, cfg.gif_max_seconds)
packs = PackManager(bot, cfg.owner_id, cfg.pack_title, cfg.default_emoji, cfg.data_dir / "state.json")

router = Router()
# Бот только для владельца, остальных молча игнорируем
router.message.filter(F.from_user.id == cfg.owner_id)
router.callback_query.filter(F.from_user.id == cfg.owner_id)


# Всё, что не прошло фильтр владельца, попадает сюда: не отвечаем, только пишем в лог
strangers = Router()


@strangers.message()
@strangers.callback_query()
async def on_stranger(event: Message | CallbackQuery) -> None:
    user = event.from_user
    log.warning("Игнорирую апдейт от id=%s (@%s)", user.id if user else None, user.username if user else None)


@dataclass
class PendingVideo:
    file_id: str
    file_size: int | None
    emoji: str
    start: float


class VideoAction(CallbackData, prefix="v"):
    action: str
    key: int


# Видео, ждущие нажатия кнопки. Только в памяти: после перезапуска надо прислать заново
pending: OrderedDict[int, PendingVideo] = OrderedDict()

TIME_RE = re.compile(r"^(?:(\d+):)?(\d+(?:[.,]\d+)?)$")


def parse_caption(caption: str | None) -> tuple[str, float]:
    """Из подписи достаём эмодзи и время начала (секунды или м:сс)."""
    emoji, start = cfg.default_emoji, 0.0
    for token in (caption or "").split():
        if match := TIME_RE.match(token):
            minutes, seconds = match.groups()
            start = int(minutes or 0) * 60 + float(seconds.replace(",", "."))
        elif not any(ch.isalnum() for ch in token):
            emoji = token
    return emoji, start


@asynccontextmanager
async def job(message: Message):
    """Очередь + временная папка, которая удаляется сразу после ответа."""
    queued = converter.lock.locked()
    status = await message.answer("⏳ В очереди…" if queued else "⏳ Обрабатываю…")
    try:
        async with converter.lock:
            if queued:
                await status.edit_text("⏳ Обрабатываю…")
            with tempfile.TemporaryDirectory(dir=cfg.work_dir) as tmp:
                yield Path(tmp)
    except ConversionError as e:
        await message.answer(f"❌ {e}")
    except Exception:
        log.exception("Ошибка обработки")
        await message.answer("❌ Что-то сломалось, подробности в логе")
    finally:
        await status.delete()


async def download(file_id: str, file_size: int | None, work: Path) -> Path:
    if file_size and file_size > DOWNLOAD_LIMIT:
        raise ConversionError("файл больше 20 МБ — Bot API такие не отдаёт")
    src = work / "input"
    try:
        await bot.download(file_id, destination=src)
    except TelegramBadRequest as e:
        raise ConversionError(f"не удалось скачать файл: {e.message}")
    return src


async def add_to_pack(message: Message, path: Path, sticker_format: str, emoji: str) -> None:
    file_id, created = await packs.add(path, sticker_format, emoji)
    await message.answer_sticker(file_id)
    if created:
        await message.answer(f"Создал новый пак: {packs.link(packs.index)}")


@router.message(CommandStart())
@router.message(Command("help"))
async def on_start(message: Message) -> None:
    await message.answer(HELP.format(gif=cfg.gif_max_seconds), parse_mode="HTML")


@router.message(Command("pack"))
async def on_pack(message: Message) -> None:
    links = "\n".join(packs.link(i) for i in range(1, packs.index + 1))
    await message.answer(f"Твои паки:\n{links}\n\n(пак появится после первого стикера)")


@router.message(Command("del"))
async def on_delete(message: Message) -> None:
    reply = message.reply_to_message
    sticker = reply.sticker if reply else None
    if not sticker or not packs.owns(sticker.set_name):
        await message.answer("Ответь /del на стикер из своего пака")
        return
    await bot.delete_sticker_from_set(sticker.file_id)
    await message.answer("🗑 Удалил из пака")


IMAGE_DOC = F.document.mime_type.startswith("image/") & (F.document.mime_type != "image/gif")
VIDEO_DOC = F.document.mime_type.startswith("video/") | (F.document.mime_type == "image/gif")


@router.message(F.photo | IMAGE_DOC)
async def on_photo(message: Message) -> None:
    file = message.photo[-1] if message.photo else message.document
    emoji, _ = parse_caption(message.caption)
    async with job(message) as work:
        src = await download(file.file_id, file.file_size, work)
        sticker = await converter.photo_to_sticker(src, work)
        await add_to_pack(message, sticker, "static", emoji)


@router.message(F.video | F.animation | F.video_note | VIDEO_DOC)
async def on_video(message: Message) -> None:
    # У гифок заполнены и animation, и document — animation проверяем раньше
    file = message.video or message.animation or message.video_note or message.document
    emoji, start = parse_caption(message.caption)
    pending[message.message_id] = PendingVideo(file.file_id, file.file_size, emoji, start)
    while len(pending) > PENDING_LIMIT:
        pending.popitem(last=False)

    kb = InlineKeyboardBuilder()
    kb.button(text="🎭 Стикер", callback_data=VideoAction(action="sticker", key=message.message_id))
    kb.button(text="🎞 Гифка", callback_data=VideoAction(action="gif", key=message.message_id))
    await message.reply("Что сделать?", reply_markup=kb.as_markup())


@router.callback_query(VideoAction.filter())
async def on_video_action(query: CallbackQuery, callback_data: VideoAction) -> None:
    item = pending.get(callback_data.key)
    if item is None:
        await query.answer("Не помню это видео — пришли его заново", show_alert=True)
        return
    await query.answer()
    message = query.message
    async with job(message) as work:
        src = await download(item.file_id, item.file_size, work)
        if callback_data.action == "sticker":
            sticker = await converter.video_to_sticker(src, work, item.start)
            await add_to_pack(message, sticker, "video", item.emoji)
        else:
            animation = await converter.video_to_gif(src, work, item.start)
            await message.answer_animation(FSInputFile(animation))


@router.message()
async def on_other(message: Message) -> None:
    await message.answer("Пришли фото или видео. /help — подсказка")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    # Хвосты от прошлого запуска, если он упал посреди обработки
    for leftover in cfg.work_dir.iterdir():
        shutil.rmtree(leftover, ignore_errors=True)

    await converter.check()
    await packs.setup()
    await bot.set_my_commands([
        BotCommand(command="pack", description="Ссылки на паки"),
        BotCommand(command="del", description="Удалить стикер (ответом)"),
        BotCommand(command="help", description="Подсказка"),
    ])

    dp = Dispatcher()
    dp.include_routers(router, strangers)
    log.info("Запущен как @%s", packs.bot_username)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
