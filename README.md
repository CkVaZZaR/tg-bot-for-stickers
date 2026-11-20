# tg-to-sticker

Личный Telegram-бот: фото → стикер, видео → видеостикер или гифка (выбор кнопкой).
Стикеры сами складываются в твой пак `pack1_by_<бот>`; когда пак заполнится, создаётся `pack2_…`.
Входные файлы не хранятся: каждая задача идёт во временной папке, которая удаляется сразу после ответа.

## 1. Telegram

1. [@BotFather](https://t.me/BotFather) → `/newbot` → сохрани токен.
2. [@userinfobot](https://t.me/userinfobot) → узнай свой числовой ID.

## 2. Роутер (OpenWrt)

### Swap на 1 ГБ (страховка от нехватки памяти)

`/mnt/data` должен быть на ext4 (на exFAT/NTFS swap-файл не заработает):

```sh
opkg install swap-utils
dd if=/dev/zero of=/mnt/data/swapfile bs=1M count=1024
chmod 600 /mnt/data/swapfile
mkswap /mnt/data/swapfile
swapon /mnt/data/swapfile
# включать при загрузке: строку добавить в /etc/rc.local перед "exit 0"
sed -i 's|^exit 0|swapon /mnt/data/swapfile\nexit 0|' /etc/rc.local
free
```

### ffmpeg

Нужна статическая сборка из `/opt/ffmpeg` (в ffmpeg из opkg нет VP9). Проверь кодировщики:

```sh
/opt/ffmpeg/ffmpeg -hide_banner -encoders | grep -E 'libwebp |libvpx-vp9|libx264 '
```

Должны быть все три. Стоковый ffmpeg можно удалить: `opkg remove ffmpeg libffmpeg-full`.

## 3. Копирование на роутер

С мака (в dropbear нет sftp, поэтому `scp -O`):

```sh
ssh root@192.168.1.1 'mkdir -p /opt/tg-sticker'
scp -O bot.py config.py media.py packs.py requirements.txt config.env.example root@192.168.1.1:/opt/tg-sticker/
scp -O deploy/tg-sticker.init root@192.168.1.1:/etc/init.d/tg-sticker
```

## 4. Установка на роутере

```sh
cd /opt/tg-sticker
python3 -m venv venv            # если нет модуля venv: opkg install python3-venv
venv/bin/pip install -r requirements.txt

cp config.env.example config.env
vi config.env                   # BOT_TOKEN и OWNER_ID

# пробный запуск в консоли (Ctrl+C для выхода)
venv/bin/python bot.py
```

Если в боте всё работает, ставим автозапуск:

```sh
chmod +x /etc/init.d/tg-sticker
/etc/init.d/tg-sticker enable
/etc/init.d/tg-sticker start
logread -f -e tg-sticker         # логи
```

## Использование

- Фото (или картинка файлом) → стикер в паке.
- Видео / гифка / кружок → кнопки «Стикер» / «Гифка».
- Подпись: эмодзи и время начала, например `😂 1:05` или `🔥 12.5`.
- `/pack`: ссылки на паки; `/del` ответом на стикер удаляет его из пака.

## Обновление

```sh
scp -O bot.py config.py media.py packs.py root@192.168.1.1:/opt/tg-sticker/
ssh root@192.168.1.1 /etc/init.d/tg-sticker restart
```
