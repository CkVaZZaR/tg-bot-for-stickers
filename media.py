"""Конвертация через ffmpeg: фото -> статичный стикер, видео -> видеостикер или гифка."""

import asyncio
import logging
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

STATIC_MAX_BYTES = 512 * 1024
VIDEO_STICKER_MAX_BYTES = 256 * 1024
VIDEO_STICKER_SECONDS = 2.95  # лимит Telegram — 3 с, оставляем запас
ANIMATION_MAX_BYTES = 50 * 1024 * 1024  # лимит Bot API на отправку
FFMPEG_TIMEOUT = 300

# Вписываем в квадрат 512x512: большая сторона ровно 512, размеры чётные для yuv420p
FIT_512 = "scale=512:512:force_original_aspect_ratio=decrease:force_divisible_by=2"
# Для гифок только уменьшаем, маленькие видео не растягиваем
FIT_720 = "scale=w='min(720,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"

REQUIRED_ENCODERS = ("libwebp", "libvpx-vp9", "libx264")


class ConversionError(Exception):
    pass


class Converter:
    def __init__(self, ffmpeg: str, gif_max_seconds: int):
        self.ffmpeg = ffmpeg
        self.gif_max_seconds = gif_max_seconds
        nice = shutil.which("nice")
        # Пониженный приоритет, чтобы роутер не тормозил во время кодирования
        self._prefix = [nice, "-n", "10"] if nice else []
        # Одна конвертация за раз — на двух ядрах параллелить нечего
        self.lock = asyncio.Lock()

    async def check(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            self.ffmpeg, "-hide_banner", "-encoders",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        missing = [name for name in REQUIRED_ENCODERS if name not in out.decode()]
        if missing:
            raise SystemExit(
                f"В ffmpeg ({self.ffmpeg}) нет кодировщиков: {', '.join(missing)}. "
                "Проверь FFMPEG в config.env"
            )
        log.info("ffmpeg: %s", self.ffmpeg)

    async def _run(self, *args: str) -> None:
        cmd = [*self._prefix, self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise ConversionError("ffmpeg не уложился в 5 минут")
        if proc.returncode:
            log.error("ffmpeg: %s", err.decode(errors="replace"))
            raise ConversionError("ffmpeg не смог обработать файл")

    @staticmethod
    def _check_output(path: Path) -> int:
        size = path.stat().st_size if path.exists() else 0
        if not size:
            raise ConversionError("получился пустой файл — может, время начала больше длины видео?")
        return size

    async def photo_to_sticker(self, src: Path, work: Path) -> Path:
        dst = work / "sticker.webp"
        for quality in (90, 75, 60, 45):
            await self._run(
                "-i", str(src), "-vf", FIT_512, "-frames:v", "1",
                "-c:v", "libwebp", "-quality", str(quality), str(dst),
            )
            if self._check_output(dst) <= STATIC_MAX_BYTES:
                return dst
        raise ConversionError("не удалось ужать картинку до 512 КБ")

    async def video_to_sticker(self, src: Path, work: Path, start: float) -> Path:
        dst = work / "sticker.webm"
        seek = ["-ss", f"{start:.2f}"] if start else []
        for bitrate in ("500k", "380k", "280k", "200k", "140k"):
            await self._run(
                *seek, "-i", str(src), "-t", str(VIDEO_STICKER_SECONDS), "-an",
                "-vf", f"fps=30,{FIT_512}",
                "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                "-row-mt", "1", "-threads", "2", "-b:v", bitrate, "-pix_fmt", "yuv420p",
                str(dst),
            )
            size = self._check_output(dst)
            if size <= VIDEO_STICKER_MAX_BYTES:
                return dst
            log.info("Видеостикер %d КБ при %s, ужимаем сильнее", size // 1024, bitrate)
        raise ConversionError("не удалось ужать видео до 256 КБ")

    async def video_to_gif(self, src: Path, work: Path, start: float) -> Path:
        # Telegram показывает MP4 без звука как гифку
        dst = work / "animation.mp4"
        seek = ["-ss", f"{start:.2f}"] if start else []
        await self._run(
            *seek, "-i", str(src), "-t", str(self.gif_max_seconds), "-an",
            "-vf", FIT_720, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(dst),
        )
        if self._check_output(dst) > ANIMATION_MAX_BYTES:
            raise ConversionError("гифка вышла больше 50 МБ — уменьши GIF_MAX_SECONDS")
        return dst
