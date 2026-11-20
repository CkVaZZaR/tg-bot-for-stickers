"""Стикерпаки владельца: pack1_by_<бот>, pack2_by_<бот>, ... Новый создаётся, когда текущий заполнен."""

import json
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputSticker


class PackManager:
    def __init__(self, bot: Bot, owner_id: int, title: str, default_emoji: str, state_path: Path):
        self.bot = bot
        self.owner_id = owner_id
        self.title = title
        self.default_emoji = default_emoji
        self.state_path = state_path
        self.bot_username = ""
        self.index = 1

    async def setup(self) -> None:
        self.bot_username = (await self.bot.me()).username
        if self.state_path.exists():
            self.index = json.loads(self.state_path.read_text())["index"]

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"index": self.index}))

    def name(self, index: int) -> str:
        return f"pack{index}_by_{self.bot_username}"

    def link(self, index: int) -> str:
        return f"https://t.me/addstickers/{self.name(index)}"

    def owns(self, set_name: str | None) -> bool:
        return bool(set_name) and set_name.startswith("pack") and set_name.endswith(f"_by_{self.bot_username}")

    async def add(self, path: Path, sticker_format: str, emoji: str) -> tuple[str, bool]:
        """Добавляет стикер в текущий пак. Возвращает file_id стикера и флаг «создан новый пак»."""
        uploaded = await self.bot.upload_sticker_file(self.owner_id, FSInputFile(path), sticker_format)
        while True:
            name = self.name(self.index)
            sticker = InputSticker(sticker=uploaded.file_id, format=sticker_format, emoji_list=[emoji])
            created = False
            try:
                try:
                    await self.bot.add_sticker_to_set(self.owner_id, name, sticker)
                except TelegramBadRequest as e:
                    if "STICKERSET_INVALID" not in e.message:
                        raise
                    title = self.title if self.index == 1 else f"{self.title} {self.index}"
                    await self.bot.create_new_sticker_set(self.owner_id, name, title, [sticker])
                    created = True
            except TelegramBadRequest as e:
                if "STICKERS_TOO_MUCH" in e.message:
                    self.index += 1
                    self._save()
                    continue
                if "EMOJI" in e.message and emoji != self.default_emoji:
                    emoji = self.default_emoji
                    continue
                raise
            sticker_set = await self.bot.get_sticker_set(name)
            return sticker_set.stickers[-1].file_id, created
