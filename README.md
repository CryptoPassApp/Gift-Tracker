# ⚡ GiftTracker — Telegram Gifts Sniper, Floor Tracker & Web3 Arbitrage Terminal

Полностью готовый к продаже turnkey-продукт: Telegram Mini App + бот с монетизацией через Telegram Stars. Ловит лоты подарков Telegram с дисконтом от флора, считает чистую прибыль после комиссий, пушит сигналы (PRO — мгновенно, FREE — с задержкой 45 секунд).

---

## 📦 Что внутри

| Компонент | Описание |
|---|---|
| `static/index.html` | Монолитный Mini App (~1200 строк): экраны ⚡ Sniper, 📊 Floors (+flip-калькулятор), 🔔 Alerts, 👑 VIP Pass. **Автономный Demo Mode**: файл работает без бэкенда (GitHub Pages / file://) |
| `bot/` | aiogram 3: команды `/start`, `/pro` (Stars), `/alerts`, `/admin`, AuthMiddleware, SQLite WAL |
| `bot/services/` | `market_api.py` (рынок + mock + TonAPI fallback), `sniper_engine.py` (формула профита), `notifier.py` (пуши 0с PRO / 45с FREE) |
| `api/` | FastAPI: `/api/gifts/*`, `/api/alerts`, `/api/user/*`, `/api/demo/feed`, валидация initData (HMAC-SHA256), раздача статики |

**Экраны Mini App:**
- **⚡ SNIPER** — live-лента лотов (badge `🔥 -34% BELOW FLOOR`, buy now / floor / profit net, «только что», кнопка Snipe);
- **📊 FLOORS** — матрица 8 подарков (TON + USD при $5.20/TON, ±24h, ликвидность) и интерактивный flip-калькулятор (комиссия Getgems 5% + газ 0.08 TON);
- **🔔 ALERTS** — конструктор триггеров (подарок, цена, дисконт), FREE-лимит 1 алерт;
- **👑 VIP PASS** — сравнение FREE/PRO, тарифы ★150/мес и ★999 lifetime, `openInvoice`.

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

## ✅ Тесты (55 шт, pytest)

```bash
pip install -r requirements.txt
python -m pytest          # 55 passed
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

Каждый тест работает на изолированной SQLite-базе во временном каталоге (`.env` разработчика не читается).

## 🖼️ Реальные NFT-медиа (Fragment / TonAPI)

Каталог подарков подкреплен живыми ассетами, а не эмодзи:

- **Превью коллекций** — WebP-рендеры официальных подарков Telegram с CDN Fragment (`https://nft.fragment.com/collection/{slug}.webp`). Все URL проверены (HTTP 200, `image/webp`): `plushpepe`, `iongem`, `trappedheart`, `astralshard`, `heroichelmet`, `moussecake`, `perfumebottle` + item-рендер `gift/genielamp-1.webp`.
- **Адреса контрактов TON** — из метаданных TonAPI (`GET /v2/nfts/collections`, официальные коллекции с описанием "exclusive NFT collection by Telegram"). По адресу строятся живые диплинки Getgems: `https://getgems.io/collection/{address}` (лот: `/nft/{item_address}`) и Tonkeeper: `https://app.tonkeeper.com/nft/{item_address}`.
- **Схема данных**: сигналы несут `image_url` и `item_address` от `market_api.py` через `sniper_engine.py` в БД (`snipe_signals`) и в API (`/api/gifts/floors|feed`, `/api/demo/feed`). Mini App рендерит NFT-блок с подсветкой редкости (ember ≥30% / изумруд 15–30%) и кнопками «Snipe on Getgems» + Tonkeeper (PRO).
- Маппинг пресетов ТЗ на реальные коллекции задан в `GIFT_PRESETS` (`bot/services/market_api.py`) и `GIFTS` (`static/index.html`): Plush Pepe→Plush Pepes, Magic Hat→Genie Lamp, Diamond Box→Ion Gems, Heart Box→Trapped Heart, Golden Star→Astral Shards, Spaceman→Heroic Helmets, Birthday Cake→Mousse Cakes, Champagne→Perfume Bottles.

## 🔌 REST API

| Метод | Путь | Авторизация | Описание |
|---|---|---|---|
| GET | `/api/health` | — | Healthcheck |
| GET | `/api/gifts/floors` | initData | Флоры 8 коллекций (TON/USD, 24h, ликвидность) |
| GET | `/api/gifts/feed` | initData | Лента последних сигналов снайпера |
| GET | `/api/demo/feed` | — | Демо-сигналы без авторизации |
| GET/POST/DELETE | `/api/alerts` | initData | Персональные алерты (FREE: лимит 1) |
| GET | `/api/user/me` | initData | Профиль + PRO-статус |
| POST | `/api/user/invoice` | initData | invoice-link Telegram Stars |

Авторизация: заголовок `X-Init-Data` с «сырым» `Telegram.WebApp.initData`; подпись проверяется HMAC-SHA256 по токену бота.

---

## 🗄️ База данных (SQLite WAL)

Таблицы: `users`, `alerts`, `snipe_signals`, `payments` — схема соответствует ТЗ, создаются автоматически при первом старте. Файл лежит в `./data/` (volume), WAL-режим позволяет боту и API работать с базой одновременно.

---

## 📊 Команды бота

| Команда | Описание |
|---|---|
| `/start` | Приветствие + кнопка запуска Mini App |
| `/alerts` | Список алертов с удалением (создание — в Mini App) |
| `/pro` | Тарифы PRO и оплата Stars (★150 мес / ★999 навсегда) |
| `/top` | Топ-5 самых жирных сигналов |
| `/admin` | Панель админа: статистика (юзеры, PRO, доход ★), рассылка |

---

## ⚙️ Настройка под реальный рынок

По умолчанию `MARKET_SOURCE=mock` — автономный генератор (цены блуждают вокруг базовых флоров пресетов). Для подключения реальных данных:

1. `MARKET_SOURCE=tonapi`
2. `TONAPI_COLLECTIONS={"Plush Pepe":"EQ...","Magic Hat":"EQ..."}` — адреса TON-коллекций подарков;
3. (опционально) `TONAPI_KEY=...` — ключ tonapi.io для больших лимитов.

При любой ошибке внешнего API происходит автоматический fallback на mock — сервис не падает. Поток новых листингов в этой сборке симулируется генератором: публичного free-API листингов Getgems не существует, интеграцию реального потока выполняйте по запросу клиента (webhook/платный API маркетплейса).

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
│   ├── database.py       # SQLAlchemy async + WAL + CRUD-хелперы
│   ├── models.py         # User, Alert, SnipeSignal, Payment
│   ├── keyboards.py      # инлайн-клавиатуры
│   ├── middleware.py     # AuthMiddleware (авторегистрация)
│   ├── main.py           # точка входа бота
│   ├── handlers/         # start, subscription (Stars), alerts, admin
│   └── services/         # market_api, sniper_engine, notifier
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
