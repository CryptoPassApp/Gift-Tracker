# ⚡ GiftTracker — Telegram Gifts Sniper, Floor Tracker & Web3 Arbitrage Terminal

Полностью готовый к продаже turnkey-продукт: Telegram Mini App + бот с монетизацией через Telegram Stars. Ловит лоты подарков Telegram с дисконтом от флора, считает чистую прибыль после комиссий, пушит сигналы (PRO — мгновенно, FREE — с задержкой 45 секунд).

---

## 📦 Что внутри

| Компонент | Описание |
|---|---|
| `static/index.html` | Монолитный Mini App (~1200 строк): экраны ⚡ Sniper, 📊 Floors (+flip-калькулятор), 🔔 Alerts, 👑 VIP Pass. **Автономный Demo Mode**: файл работает без бэкенда (GitHub Pages / file://) |
| `bot/` | aiogram 3: команды `/start`, `/pro` (Stars), `/alerts`, `/admin`, `/grant`, AuthMiddleware, SQLite WAL, hourly-джоба истечения PRO |
| `bot/services/` | `market_api.py` (рынок + mock + TonAPI fallback), `sniper_engine.py` (формула профита), `notifier.py` (пуши 0с PRO / 45с FREE), `heartbeat.py`, `maintenance.py`, `pro_lifecycle.py`, `admin_stats.py` |
| `api/` | FastAPI: `/api/gifts/*`, `/api/alerts`, `/api/user/*`, `/api/demo/feed`, валидация initData (HMAC-SHA256), раздача статики |

**Экраны Mini App:**
- **⚡ SNIPER** — live-лента сделок (badge `🔥 −34% BELOW FLOOR`, модель/фон/символ, buy now / floor модели / profit net, «только что», кнопка Snipe) + фильтры по коллекции и диапазону цен;
- **📊 FLOORS** — матрица всех коллекций (TON + USD при $5.20/TON, медианный флор, ликвидность), 24ч-изменение по собственной истории `floor_snapshots` (прочерк, пока истории меньше 20 часов) и flip-калькулятор: подарок выбирается списком с поиском → затем модели этой коллекции (как на Tonnel/Portals), расчёт идёт от **флора выбранной модели**, а не от общего флора коллекции (`/api/gifts/models`, `/api/demo/models`);
- **🔔 ALERTS** — конструктор триггеров (подарок, цена, дисконт), FREE-лимит 1 алерт;
- **👑 VIP PASS** — сравнение FREE/PRO, тарифы ★150/мес и ★999 lifetime, `openInvoice`;
- **👤 PROFILE** — карточка аккаунта, язык интерфейса RU/EN (меню, фильтры, профиль), статус подписки, поддержка и настройки уведомлений в боте, «О нас», проверка связи с бэкендом.

**Математика арбитража:**
```
Net Profit = (Floor × 0.95) − Deal − 0.08 TON
Discount   = (Floor − Deal) / Floor × 100
≥ 15%  → Snipe Signal      ≥ 30% → 🔥 Hot Liquidation (пуш всем)
```

---

## 🚀 Быстрый старт (5 минут)

### 0. Требования
Docker + Docker Compose. Домен с HTTPS не обязателен (бот работает на long polling), но для Mini App нужен публичный HTTPS-URL витрины.

### 1. Создайте бота и получите токен
1. Откройте [@BotFather](https://t.me/BotFather) → `/newbot` → скопируйте токен.
2. Узнайте свой Telegram ID у [@userinfobot](https://t.me/userinfobot).

### 2. Настройте окружение
Файл `.env` уже создан рядом с проектом. Откройте `gift_tracker/.env` любым редактором и заполните:

| Переменная | Что вписать |
|---|---|
| `BOT_TOKEN` | Токен из @BotFather: `/newbot` → имя → username `*_bot` → строка вида `123456789:AAH...`. Замените ею заглушку `123456789:AAF-PLACEHOLDER-REPLACE-ME` |
| `ADMIN_IDS` | Ваш Telegram ID (узнать у @userinfobot) в формате **JSON-массива**: `[123456789]`. Несколько: `[123456789,987654321]` |
| `MINI_APP_URL` | Публичный HTTPS-адрес витрины (шаг 4). Для локальной проверки команд бота можно оставить заглушку |

Важно: списки в `.env` пишутся как JSON (`ADMIN_IDS=[1,2]`, `API_ALLOWED_ORIGINS=["*"]`) — pydantic-settings парсит их именно так.

Проверка, что токен подхватился:
```bash
python -c "from bot.config import get_settings; s=get_settings(); print(s.BOT_TOKEN[:12] + '...', s.ADMIN_IDS)"
```

> ⚠️ Не отправляйте токен в чаты и не коммитьте `.env` — он уже в `.gitignore`. Если токен утёк: BotFather → `/mybots` → ваш бот → API Token → Revoke.

### 3. Запустите
```bash
docker compose up -d --build
```
Поднимутся два сервиса:
- `api` — FastAPI на `:8000`, раздает Mini App и REST;
- `bot` — long polling + фоновый сканер рынка (SniperEngine).

Проверка: `curl http://localhost:8000/api/health` → `{"status":"ok",...}`

**Production с автоматическим HTTPS** — готовый overlay (Caddy + Let's Encrypt):

```bash
# в .env добавьте DOMAIN=gift.example.com
docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml up -d --build
```

Пошаговая инструкция (VPS, DNS, бэкапы, обновление/откат, чек-лист приёмки) — `deploy/README.md`.

### 4. Подключите Mini App
1. Разверните `api` за HTTPS (reverse-прокси Caddy/Nginx/Cloudflare Tunnel — любой вариант с валидным сертификатом).
2. В BotFather: `/newapp` → укажите тот же URL (`https://ваш-домен/`) → имя `GiftTracker`.
3. В `.env` пропишите `MINI_APP_URL=https://ваш-домен/` и перезапустите: `docker compose up -d`.
4. Напишите боту `/start` → кнопка «⚡ Открыть Sniper Terminal».

### 5. Включите платежи Stars
BotFather → `/mybots` → ваш бот → **Payments** → включите **Telegram Stars** (провайдер не нужен, валюта XTR). Инвойсы создаются ботом автоматически (`send_invoice`) и через Bot API `createInvoiceLink` для Mini App.

---

## 🖥️ Demo Mode (без бэкенда)

`static/index.html` можно просто закинуть на GitHub Pages — при открытии вне Telegram автоматически включается **DEMO MODE**: каждые 7 секунд генерируется новый случайный лот с анимацией вспышки, работают все экраны, калькулятор и конструктор алертов (хранение в localStorage). Бейдж `⚡ DEMO MODE` горит вверху. Идеально для презентации покупателю.

---

## ✅ Тесты (128 шт, pytest)

```bash
pip install -r requirements.txt
python -m pytest          # 111 passed
```

Покрытие (`tests/`):

| Файл | Что проверяет |
|---|---|
| `test_sniper_math.py` | Формула `Net = Floor×0.95 − Deal − 0.08`, пороги 15%/30%, кастомные комиссии, edge-cases (ноль, лот выше флора, отрицательный профит) |
| `test_market_api.py` | 8 пресетов с реальными NFT-медиа, TTL-кэш флоров, границы random-walk ±25%, потокобезопасность, медиа в лотах |
| `test_init_data.py` | HMAC-SHA256 подпись Telegram: валидная/битая/чужой токен/мусор |
| `test_monetization.py` | Лимит FREE=1, стекование PRO-месяцев, lifetime, задержка пуша FREE (3600с — не отправлен), блокировки, админ-рассылка |
| `test_sniper_engine.py` | `scan_once`: персист сигнала с медиа, дедупликация лотов, отсечение <15%, HOT-пуш всем, матчинг персональных алертов, чистая отмена цикла |
| `test_api_flows.py` | Сквозные через TestClient: 401 без initData, флоры с NFT-ассетами, CRUD алертов + 403 по FREE-лимиту, лента с `image_url`/`item_address`/`tonkeeper_url` |
| `test_reliability.py` | Persistent-дедупликация после «рестарта», WAL checkpoint, очистка `seen_lots`, JSON/уровни логов, heartbeat + `/api/health` + CLI healthcheck, бэкап с ротацией |
| `test_payments_admin.py` | pre_checkout (сумма/валюта/payload), запись платежа и активация PRO, дубль charge_id без второго платежа и продления, Lifetime + месяц поверх Lifetime, hourly-джоба истечения PRO, потеря мгновенных пушей, доход по дням/тарифам, последние платежи, `/grant` и отказ не-админу |

Каждый тест работает на изолированной SQLite-базе во временном каталоге (`.env` разработчика не читается).

## 🖼️ Реальные NFT-медиа (Fragment / TonAPI)

Каталог подарков подкреплен живыми ассетами, а не эмодзи:

- **Превью коллекций** — WebP-рендеры официальных подарков Telegram с CDN Fragment (`https://nft.fragment.com/collection/{slug}.webp`). Все URL проверены (HTTP 200, `image/webp`): `plushpepe`, `iongem`, `trappedheart`, `astralshard`, `heroichelmet`, `moussecake`, `perfumebottle` + item-рендер `gift/genielamp-1.webp`.
- **Адреса контрактов TON** — из метаданных TonAPI (`GET /v2/nfts/collections`, официальные коллекции с описанием "exclusive NFT collection by Telegram"). По адресу строятся живые диплинки Getgems: `https://getgems.io/collection/{address}` (лот: `/nft/{item_address}`) и Tonkeeper: `https://app.tonkeeper.com/nft/{item_address}`.
- **Схема данных**: сигналы несут `image_url`, `item_address` и атрибуты конкретного NFT (`model`, `backdrop`, `symbol`, `floor_basis`) от `market_api.py` через `sniper_engine.py` в БД (`snipe_signals`) и в API (`/api/gifts/floors|feed`, `/api/demo/feed`). Mini App рендерит NFT-блок с подсветкой редкости (ember ≥30% / изумруд 15–30%), именем модели и кнопками «Snipe on Getgems» + Tonkeeper (PRO).
- **Имена подарков** — канонические из Telegram Gift Dataset (`GIFT_PRESETS`, `GIFTS`): Plush Pepe, Genie Lamp, Ion Gem, Trapped Heart, Astral Shard, Heroic Helmet, Mousse Cake, Perfume Bottle. Выдуманных алиасов (`Magic Hat`, `Diamond Box`, …) больше нет; старые записи в БД мигрируются в `init_db`.
- **Флор по модели** — `models/{short_name}/prices.json` из датасета: у Plush Pepe 45 моделей (Hue Jester 5304 … Leonardo 25500 TON). Лот сравнивается с флором СВОЕЙ модели, а не с общим флором коллекции; фон и символ показываются в карточке как атрибуты. Защита от мусора в датасете — `TRAIT_FLOOR_MAX_RATIO` (8×).

## 🎯 Что попадает в ленту снайпера

Лента — не список всех листингов маркета. В неё (и в `/api/gifts/feed`, `/api/demo/feed`) попадают только лоты, у которых одновременно:

1. цена ниже эталона (`discount_pct > 0`), и
2. чистая прибыль после комиссии и газа — не меньше `FEED_MIN_PROFIT_TON` (0.2 TON): лоты вида «+0.03 TON» комиссия маркетплейса съедает.

Всё, что дороже флора, отсекается до записи в БД: зелёная кнопка «Snipe» и прочерк вместо профита на наценках больше не появляются.

**Фильтры ленты** (панель под чипами источников): поиск по коллекции или модели (datalist по всем 119 подаркам каталога) и диапазон цен в TON — `q`, `min_price`, `max_price` считаются в SQL, поэтому поиск видит сделки за пределами последних 50 записей. Быстрые диапазоны: `Любая`, `до 10`, `10–80`, `80–250`, `250–1k`, `1k+`; ценового потолка по умолчанию нет.

**Чтобы лента не была стеной из Plush Pepe:**
- на одну коллекцию — не больше `FEED_MAX_PER_GIFT` (5) лотов в выдаче; остальное достаётся другим подаркам;
- сканер обходит коллекции **от дешёвых к дорогим** по флору, поэтому при ограниченном лимите TonAPI первыми приходят лоты ширпотреба (5–80 TON), а не дорогие модели.

## 🌐 Адреса коллекций (лоты по всем подаркам)

Реальные sale-лоты читаются по адресу TON-коллекции. Адреса берутся из публичного реестра Tonkeeper `ton-assets/accounts.json` (`GIFT_COLLECTIONS_URL`, ключ не нужен), сопоставляются по имени (подарок в ед. числе ↔ коллекция во мн.: `Plush Pepe` → `Plush Pepes`) и кэшируются в `data/gift_collections.json` на неделю (`COLLECTION_INDEX_TTL_SEC`). Сейчас резолвится **106 из 119** коллекций; остальные 13 отсутствуют в реестре (в т.ч. `Durov’s Glasses`, `Liberty Figure`, `Algorithm Cup` — появятся в следующих обновлениях реестра).

Сканер обходит коллекции **по кругу**, а не все сразу: `TONAPI_COLLECTIONS_PER_SCAN` (3 по умолчанию) коллекций × `TONAPI_SCAN_PAGES` страниц за проход, курсор двигается дальше. Полный обход 106 коллекций занимает ~35 минут.

**Лимит TonAPI.** Анонимный тариф — примерно 1 запрос/сек и небольшой дневной лимит; при 429 сканер встаёт на паузу `TONAPI_COOLDOWN_ON_429_SEC` (10 минут), а в `/api/health` появляется `market.tonapi_cooldown_sec`. Для стабильного потока по всем коллекциям нужен бесплатный ключ [tonapi.io](https://tonapi.io): `TONAPI_KEY=...` в `.env`. С ключом можно поднять `TONAPI_COLLECTIONS_PER_SCAN` до 6–12.

## 🔌 REST API

| Метод | Путь | Авторизация | Описание |
|---|---|---|---|
| GET | `/api/health` | — | Healthcheck |
| GET | `/api/gifts/floors` | initData | Флоры коллекций (TON/USD, 24h, ликвидность) |
| GET | `/api/gifts/models?name=` | initData | Модели коллекции и их флоры (калькулятор) |
| GET | `/api/gifts/feed` | initData | Лента сделок; параметры `q`, `min_price`, `max_price`, `limit` |
| GET | `/api/demo/feed` | — | Те же сделки без авторизации (те же фильтры, `count`) |
| GET | `/api/demo/models?name=` | — | Модели коллекции без авторизации |
| GET | `/api/demo/floors` | — | Флоры без авторизации (для Mini App вне Telegram) |
| GET/POST/DELETE | `/api/alerts` | initData | Персональные алерты (FREE: лимит 1) |
| PATCH | `/api/alerts/{id}` | initData | Редактирование алерта (цена, дисконт, подарок) |
| GET | `/api/user/me` | initData | Профиль, PRO-статус, порог дисконта |
| POST | `/api/user/settings` | initData | Сохранить PRO-фильтр `min_discount` (0–90%) |
| POST | `/api/user/invoice` | initData | invoice-link Telegram Stars |

Авторизация: заголовок `X-Init-Data` с «сырым» `Telegram.WebApp.initData`; подпись проверяется HMAC-SHA256 по токену бота.

---

## 🗄️ База данных (SQLite WAL)

Таблицы: `users`, `alerts`, `snipe_signals`, `payments`, `seen_lots` — создаются автоматически при первом старте. Файл лежит в `./data/` (volume), WAL-режим позволяет боту и API работать с базой одновременно.

`seen_lots(url PRIMARY KEY, seen_at)` — персистентная дедупликация лотов: отметка ставится атомарно (`INSERT ... ON CONFLICT DO NOTHING`) до пуша, поэтому после рестарта бот не повторяет старые сигналы. Отметки старше `SEEN_LOTS_RETENTION_DAYS` (по умолчанию 7 дней) удаляются фоновым обслуживанием.

Каждые `WAL_CHECKPOINT_INTERVAL_SEC` (300 с) бот и API делают `PRAGMA wal_checkpoint(TRUNCATE)` — WAL не растет бесконечно. На старте и при остановке выполняется дополнительный checkpoint.

---

## 👑 Платежи и жизненный цикл PRO

- **Оплата Stars (XTR)**: `/pro` → `send_invoice`, Mini App → `createInvoiceLink` + `openInvoice`; `pre_checkout_query` проверяет payload, сумму и валюту; `successful_payment` пишет `payments` и активирует PRO.
- **Идемпотентность**: `payments.telegram_charge_id` UNIQUE — повторная доставка апдейта не создаёт второй платёж и не продлевает подписку.
- **Истечение**: бот раз в `PRO_EXPIRY_CHECK_INTERVAL_SEC` (3600 с) снимает `is_pro` у истёкших; на старте джоба проходит сразу. Lifetime (9999-12-31) не затрагивается.
- **Ручная выдача**: `/grant <telegram_id> month|lifetime` (только `ADMIN_IDS`); создаёт пользователя, если он ещё не писал боту. Покупка месяца поверх Lifetime не ломает дату.
- **Админка**: `/admin` → «Статистика» показывает доход по дням за 7 дней, разбивку по тарифам и последние 5 платежей; «Выдать PRO» напоминает формат команды.

Acceptance реального платежа требует включить Telegram Stars в BotFather → Payments и провести оплату вручную. В pytest проверены pre-checkout, запись, повторная доставка, Lifetime и истечение — без сетевых вызовов.

---

## 🧰 Эксплуатация

### Логи

| Переменная | Значение | Что делает |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`; пустое/неизвестное → `INFO` |
| `LOG_FORMAT` | `text` | `text` — человекочитаемый, `json` — одна JSON-строка на запись |

JSON-режим: `LOG_FORMAT=json` → `{"ts":"2026-10-27T12:00:00.123Z","level":"INFO","logger":"gifttracker.bot","msg":"..."}`. Поля `extra=` попадают в JSON автоматически.

```bash
docker compose logs -f --tail=200 bot api
```

Docker сам ротирует логи (`max-size: 10m`, `max-file: 5`), диск не забьется.

### Healthcheck

- API: `GET /api/health` → `{"status":"ok",...,"bot":{"alive":true,"age_sec":3.1,"last_seen":"...","pid":123}}`.
- Бот отдельно: `python -m bot.healthcheck` (exit 0 — жив). Именно этой командой проверяется `bot`-сервис в `docker-compose.yml`.
- Бот пишет `data/bot_heartbeat.json` каждые `BOT_HEARTBEAT_INTERVAL_SEC` (30 с). Если файл старше `BOT_HEARTBEAT_STALE_SEC` (120 с) или содержит `"status":"stopped"` — бот считается мертвым.
- `docker compose ps` показывает `healthy`/`unhealthy` для обоих сервисов.

Если бот «unhealthy», но процесс жив: проверьте, что `BOT_HEARTBEAT_FILE` доступен на запись и что файл не перетирается вторым экземпляром бота.

### Бэкап SQLite (с ротацией)

```bash
python scripts/backup_db.py                 # -> data/backups/gift_tracker-YYYYMMDD-HHMMSS.db, хранит 7 последних
python scripts/backup_db.py --keep 14       # хранить 14
python scripts/backup_db.py --db data/gift_tracker.db --out D:/backups
```

Скрипт использует `sqlite3.backup()` — безопасно снимает копию с живой базы, не блокируя бота надолго.

Автозапуск:

- **Linux/cron** (каждый день в 03:15):
  ```cron
  15 3 * * * cd /opt/gift_tracker && python scripts/backup_db.py --keep 7 >> data/backups/backup.log 2>&1
  ```
- **Windows Task Scheduler**: действие `python`, аргументы `scripts\backup_db.py --keep 7`, рабочая папка `C:\path\to\gift_tracker`.

Восстановление: остановите бота и API → сохраните текущий `data/gift_tracker.db` → скопируйте нужный бэкап в `data/gift_tracker.db` → удалите `gift_tracker.db-wal` и `gift_tracker.db-shm` → запустите сервисы.

### Обновление и откат

```bash
git pull
docker compose up -d --build          # пересборка и перезапуск без простоя БД
docker compose ps                     # оба сервиса healthy
```

Откат: `git checkout <предыдущий-коммит> && docker compose up -d --build`, при необходимости восстановите БД из бэкапа (см. выше). Схема БД мигрируется автоматически (`create_all` + ALTER TABLE), откат кода на старую версию новые таблицы не ломает.

### Локальный запуск на своём ПК (без VPS)

Проект полностью работает на домашней машине: боту не нужен белый IP (long polling), а Mini App требует HTTPS-URL.

**Туннель (любой из вариантов):**

```bash
# Serveo — без регистрации, HTTPS-URL на время SSH-сессии
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:127.0.0.1:8020 serveo.net

# localhost.run — аналогично, но сервис бывает нестабилен
ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:127.0.0.1:8020 nokey@localhost.run
```

Команда печатает HTTPS-URL (`https://...serveousercontent.com`). Порядок при смене URL:

1. впишите URL в `.env` → `MINI_APP_URL=https://.../`;
2. запустите reconcile-скрипт (он перезапустит бота, если в heartbeat старый URL): `powershell -ExecutionPolicy Bypass -File .\start_local.ps1`;
3. обновите Mini App в BotFather (`/mybots` → бот → Bot Settings → Mini App, если `/newapp` использовался);
4. проверьте: `curl https://<url>/api/health` → `"status":"ok"`, `"bot":{"alive":true}`.

**Управление локальным рантаймом — только через идемпотентный скрипт** (правила в `RUNTIME.md`):

```powershell
powershell -ExecutionPolicy Bypass -File .\start_local.ps1
```

Скрипт проверяет API, туннель и бота и лечит только сломанное; `-Force` — полный рестарт. Не убивайте процессы по PID вручную: параллельные сессии уже устраивали конфликт `getUpdates` и «бесконечные» рестарты.

> ⚠️ Бесплатные туннели дают новый URL после каждого перезапуска и не гарантируют uptime. Для продажи клиенту используйте Cloudflare Tunnel с постоянным доменом или VPS — тогда `MINI_APP_URL` перестанет меняться.

Ручной запуск (если не используете скрипт):

```bash
python -m bot.main
uvicorn api.main:app --host 127.0.0.1 --port 8020
```

Для автозапуска на Windows: Task Scheduler с триггером «При входе в систему» (две задачи — бот и API) или [NSSM](https://nssm.cc/). Проверка после перезагрузки: `curl http://127.0.0.1:8020/api/health` → `status: ok` и `bot.alive: true`.

**Graceful shutdown**: по Ctrl+C / `docker compose stop` бот отменяет сканер и обслуживание, дожидается отправок notifier (до 45 с задержки FREE-пуша), пишет финальный heartbeat `stopped`, делает checkpoint WAL и закрывает БД. Платежи и сигналы не теряются.

---

## 📊 Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветствие + кнопка запуска Mini App |
| `/alerts` | Список алертов с удалением (создание и редактирование — в Mini App) |
| `/pro` | Тарифы PRO и оплата Stars (★150 мес / ★999 навсегда) |
| `/settings` | PRO-фильтр дисконта: слать только сигналы от −N% |
| `/top` | Топ-5 самых жирных сигналов |
| `/admin` | Панель админа: статистика (пользователи, PRO, доход по дням/тарифам, последние платежи), ручная выдача PRO, рассылка |
| `/grant <id> month\|lifetime` | Ручная выдача/продление PRO по Telegram ID (только админы) |

---

## ⚙️ Настройка под реальный рынок

`MARKET_SOURCE=mock` — автономный генератор (цены блуждают вокруг базовых флоров пресетов). Рабочий режим — реальные данные без ключей:

1. `MARKET_SOURCE=giftdataset` — флоры TGMrkt/Getgems/Portals из публичного датасета TelegramGifts (`Gifts_Details.json`), флоры по моделям — из `models/{short_name}/prices.json`;
2. `OFFER_SOURCES=getgems,portals` — листинги Getgems читаются через TonAPI (`/v2/nfts/collections/{address}/items`, только айтемы с `sale`), плюс **Portals** (`https://app.portals.tg/api`, ключ не нужен): `/nfts/search` отдаёт до 50 свежих лотов по всем коллекциям с ценой, моделью/фоном/символом и `floor_price`, а `/collections/floors` — 123 живых флора; курсор страниц TonAPI двигается по кругу (`TONAPI_SCAN_PAGES`);
3. `TONAPI_KEY=...` — **настоятельно рекомендуется**: бесплатный ключ tonapi.io (вход через Telegram/GitHub). Без него анонимный дневной лимит выгорает за часы (сканы встают на паузу `TONAPI_COOLDOWN_ON_429_SEC`, в `/api/health` видно `market.tonapi_cooldown_sec`), а полный обход 106 коллекций идёт слишком медленно.

Fallback на mock в рабочем режиме не включается: при сбое внешнего API остаётся прошлый снимок флоров, а не выдуманные цены. Публичный API Getgems (`https://api.getgems.io/public-api`) требует Authorization-ключ; поддержка `GETGEMS_API_KEY` не реализована — при появлении ключа сюда можно перейти на официальные флоры по атрибутам.

---

## 📁 Структура проекта

```
gift_tracker/
├── .env.example          # шаблон конфигурации
├── docker-compose.yml    # сервисы api + bot
├── Dockerfile
├── requirements.txt
├── README.md
├── api/
│   ├── main.py           # FastAPI: CORS, роутеры, статика
│   ├── dependencies.py   # валидация initData (HMAC-SHA256)
│   └── routes/           # gifts, alerts, user, demo
├── bot/
│   ├── config.py         # Pydantic Settings
│   ├── database.py       # SQLAlchemy async + WAL + дедупликация + CRUD
│   ├── models.py         # User, Alert, SnipeSignal, Payment, SeenLot
│   ├── logging_setup.py  # LOG_LEVEL / JSON-логи
│   ├── healthcheck.py    # Docker healthcheck бота (heartbeat)
│   ├── keyboards.py      # инлайн-клавиатуры
│   ├── middleware.py     # AuthMiddleware (авторегистрация)
│   ├── main.py           # точка входа бота
│   ├── handlers/         # start, subscription (Stars), alerts, admin
│   └── services/         # market_api, sniper_engine, notifier,
│                         # heartbeat, maintenance (WAL + seen_lots)
├── scripts/
│   └── backup_db.py      # бэкап SQLite с ротацией
└── static/
    └── index.html        # Mini App (монолит, Demo Mode)
```

---

## 🛠️ Локальная разработка без Docker

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # заполните BOT_TOKEN

python -m bot.main                                        # бот + сканер
uvicorn api.main:app --reload --port 8000                 # API + витрина
```

---

## ❓ FAQ

**Оплата не проходит, бот пишет «Тариф не найден».** В BotFather включите Payments → Telegram Stars; сумма и payload проверяются на `pre_checkout_query`.

**Mini App показывает DEMO MODE внутри Telegram.** Либо `initData` не прошел валидацию, либо API недоступен — проверьте `https://ваш-домен/api/health` и что `BOT_TOKEN` в `.env` совпадает с токеном бота, открывшего Mini App.

**Как поменять цены PRO?** `.env`: `PRO_MONTH_STARS`, `PRO_LIFETIME_STARS` (суммы автоматически проверяются на pre-checkout).

**Как подключить живые офферы Tonnel / MRKT?** Обе площадки закрыты Cloudflare и требуют токен залогиненной WebApp-сессии:
1. Откройте их бота (`@tonnel_network_bot` / MRKT) → WebApp → DevTools → Network;
2. найдите запрос к `api.tonnel.network` (или API MRKT) → заголовок `Authorization: Bearer ...`;
3. впишите значение в `.env` (`TONNEL_JWT` / `MRKT_AUTH_TOKEN`);
4. включите источники: `OFFER_SOURCES=getgems,tonnel,mrkt`.

Без токенов источники честно отключаются с записью в лог, а поток продолжает идти с Getgems-генератора. Токены сессии живут ограниченное время — обновляйте их при истечении.

**Как добавить подарок?** Список пресетов — `bot/services/market_api.py` (`GIFT_PRESETS`) и `static/index.html` (массив `GIFTS`).

---

## 📄 Лицензия

Продукт распространяется «как есть» для конечного владельца. Все торговые марки Telegram принадлежат их правообладателям.
