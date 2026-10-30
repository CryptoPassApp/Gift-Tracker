# GiftTracker — production deploy на VPS

Пошаговая инструкция для этапа 1 ТЗ: постоянный HTTPS-домен вместо временного туннеля.

## 0. Что нужно

- VPS с Ubuntu 22.04/24.04, 1–2 GB RAM, Docker + Compose plugin.
- Домен, A-запись которого указывает на IP сервера.
- Токен бота от @BotFather.

## 1. Подготовка сервера

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker "$USER"   # перелогиниться
```

## 2. Код и конфигурация

```bash
git clone https://github.com/CryptoPassApp/Gift-Tracker.git /opt/gift_tracker
cd /opt/gift_tracker
cp .env.example .env
nano .env
```

В `.env` обязательно:

```env
BOT_TOKEN=<токен BotFather>
ADMIN_IDS=[<ваш telegram id>]
DOMAIN=gift.example.com
MINI_APP_URL=https://gift.example.com/
API_ALLOWED_ORIGINS=["https://gift.example.com"]
DATABASE_URL=sqlite+aiosqlite:///./data/gift_tracker.db
```

Права на файл: `chmod 600 .env` (там токен).

## 3. Запуск с HTTPS

```bash
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
docker compose ps
curl -sS https://gift.example.com/api/health
```

Caddy сам получит сертификат Let's Encrypt (нужны открытые 80/443).
Проверка логов: `docker compose logs -f api caddy bot`.

## 4. Подключение Mini App в Telegram

1. BotFather → `/mybots` → бот → **Bot Settings → Menu Button** (или `/newapp`).
2. URL: `https://gift.example.com/` — тот же, что `MINI_APP_URL`.
3. Откройте бота → `/start` → кнопка должна открыть витрину.

## 5. Бэкапы

Скрипт использует sqlite3 backup API (безопасно при WAL):

```bash
python scripts/backup_db.py --db data/gift_tracker.db --out /opt/backups --keep 7
```

Cron (ежедневно в 03:30):

```cron
30 3 * * * cd /opt/gift_tracker && /usr/bin/python3 scripts/backup_db.py --keep 7 >> /var/log/gifttracker-backup.log 2>&1
```

Восстановление: остановить бота, заменить `data/gift_tracker.db` файлом бэкапа, запустить снова.

## 6. Обновление и откат

```bash
cd /opt/gift_tracker
git pull
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

Откат: `git checkout <предыдущий коммит>` и повторить сборку. База в `./data` не затрагивается.

## 7. Мониторинг

- Healthcheck: `GET /api/health` (Docker healthcheck уже настроен для `api`).
- Heartbeat бота: `data/bot_heartbeat.json` (обновляется каждые 30с).
- Логи: `docker compose logs --tail=200 api bot`.

## 8. Чек-лист приёмки

- [ ] `https://<домен>/api/health` → `{"status":"ok"}`
- [ ] сертификат валиден (зелёный замок)
- [ ] кнопка Mini App в боте открывает витрину
- [ ] `docker compose restart` не ломает стек
- [ ] после `reboot` контейнеры поднимаются сами
- [ ] бэкап создаётся по cron
- [ ] `git grep -i "AAFr\|github_pat"` в репозитории пуст
