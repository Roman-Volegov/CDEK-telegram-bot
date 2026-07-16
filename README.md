# Бот СДЭК для Telegram

Telegram-бот для расчёта стоимости доставки СДЭК и создания заказа с печатью **накладной** и **штрихкодов** (PDF A4).

## Что умеет

- Распознать адрес в свободной форме (DaData Clean)
- Посчитать тарифы СДЭК (`/calculator/tarifflist`)
- Создать заказ с номером своего нумератора `YYYY-NNNNNN` (например `2026-000001`)
- Отгрузка только с вашего ПВЗ (`CDEK_SHIPMENT_POINT`)
- Вес/габариты и название товара — из настроек; **стоимость товара** вводится в боте
- Наложенный платёж всегда `0`
- После создания заказа присылает два PDF: накладная + штрихкоды

## Стек

- Python 3.12, aiogram 3
- PostgreSQL (заказы + нумератор)
- Redis (FSM-состояния)
- Docker Compose

## Быстрый старт (Docker)

### 1. Подготовка ключей

1. Создайте бота в [@BotFather](https://t.me/BotFather) → получите `BOT_TOKEN`
2. Зарегистрируйте интеграцию на [api.cdek.ru](https://api.cdek.ru/account) → `CDEK_CLIENT_ID`, `CDEK_CLIENT_SECRET`
3. Зарегистрируйтесь на [dadata.ru](https://dadata.ru) → API-ключ и секретный ключ (для Clean)
4. Узнайте код вашего ПВЗ сдачи в СДЭК (например `MSK90`)

### 2. Конфиг

```bash
cd cdek-bot
cp .env.example .env
```

Отредактируйте `.env`:

```env
BOT_TOKEN=...
CDEK_CLIENT_ID=...
CDEK_CLIENT_SECRET=...
CDEK_TEST_MODE=true
CDEK_SHIPMENT_POINT=MSK90

DADATA_API_KEY=...
DADATA_SECRET_KEY=...

DEFAULT_WEIGHT_G=100
DEFAULT_LENGTH_CM=10
DEFAULT_WIDTH_CM=10
DEFAULT_HEIGHT_CM=5
DEFAULT_ITEM_NAME=Товар
DEFAULT_ITEM_WARE_KEY=ITEM-1
```

Для боевого контура СДЭК поставьте `CDEK_TEST_MODE=false`.

### 3. Сборка и запуск

```bash
docker compose up --build -d
docker compose logs -f bot
```

Бот работает в режиме **long polling**. После старта напишите ему `/start` в Telegram.

### 4. Остановка

```bash
docker compose down
```

Данные PostgreSQL сохраняются в volume `pgdata`.

---

## Запуск без Docker (локально)

Нужны Python 3.12+, PostgreSQL и Redis.

```bash
cd cdek-bot
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# заполните .env и поправьте URL:
# DATABASE_URL=postgresql+asyncpg://cdek:cdek@localhost:5432/cdek_bot
# REDIS_URL=redis://localhost:6379/0
```

Поднимите БД (если Docker только для инфраструктуры):

```bash
docker compose up -d db redis
```

Создайте БД/пользователя при необходимости, затем:

```bash
python -m app.main
```

Проверка импортов:

```bash
python scripts/check_imports.py
```

---

## Ограничение доступа (whitelist)

В `.env` укажите Telegram ID пользователей через запятую:

```env
ALLOWED_TELEGRAM_IDS=123456789,987654321
```

Как узнать ID:

1. Напишите боту `/id` (пока список пустой — бот открыт всем)
2. Или откройте [@userinfobot](https://t.me/userinfobot)

После изменения `.env`:

```bash
docker compose up -d --force-recreate bot
```

Если `ALLOWED_TELEGRAM_IDS` пустой — бот доступен всем.

## Как пользоваться ботом

| Действие | Команда / кнопка |
|----------|------------------|
| Старт | `/start` |
| Только расчёт | `📦 Рассчитать` или `/calc` |
| Создать заказ + PDF | `🚚 Создать заказ` или `/order` |
| Список заказов | `📋 Мои заказы` или `/orders` |
| Повторно скачать PDF | отправить номер `2026-000001` |
| Справка | `/help` |
| Отмена сценария | `/cancel` |

### Сценарий заказа

1. Адрес → подтверждение распознанного варианта  
2. Выбор тарифа  
3. Если тариф до ПВЗ — код ПВЗ получателя  
4. ФИО и телефон  
5. Стоимость товара (для декларации; НП = 0)  
6. Подтверждение → номер `2026-000001` → заказ в СДЭК → два PDF  

PDF сохраняются в `storage/pdfs/`:

- `nakladnaya_2026-000001.pdf`
- `shtrihkody_2026-000001.pdf`

---

## Структура проекта

```text
cdek-bot/
├── app/
│   ├── main.py              # точка входа
│   ├── config.py            # настройки из .env
│   ├── bot/                 # хендлеры, FSM, клавиатуры
│   ├── db/                  # модели, нумератор
│   └── services/            # CDEK API, DaData
├── storage/pdfs/            # сгенерированные PDF
├── scripts/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Важные настройки под ваш договор СДЭК

- `CDEK_SHIPMENT_POINT` — код ПВЗ, **с которого** сдаёте посылки (обязательно)
- Габариты и вес — реальные средние для вашей тары
- На тестовом контуре (`CDEK_TEST_MODE=true`) используйте тестовые ключи и адреса из документации СДЭК

## Стоимость внешних сервисов

- **DaData Clean** (стандартизация адреса): ~0,20 ₽ за адрес; первые 100 бесплатно. Подписка на «Подсказки» для MVP не обязательна  
- **СДЭК API**: по вашему договору интернет-магазина  
- **Хостинг**: VPS с Docker (1–2 GB RAM достаточно для MVP)

## Типичные проблемы

| Симптом | Что проверить |
|---------|----------------|
| Бот не отвечает | `BOT_TOKEN`, логи `docker compose logs bot` |
| Ошибка DaData 401/403 | ключи, баланс Clean |
| Ошибка OAuth СДЭК | `CDEK_CLIENT_ID/SECRET`, режим test/prod |
| Тарифы пустые | код города, габариты, тип договора ИМ |
| PDF не приходят | заказ принят СДЭК? смотрите логи и статус в `/orders` |
| Дубликат номера | номер уникален в рамках договора; нумератор не откатывается при ошибке |

## Дальнейшее развитие

- Webhook вместо polling  
- Выбор ПВЗ из списка/карты  
- Админ-команды для смены ПВЗ и габаритов без правки `.env`  
- Заявка на забор курьером (`/intakes`)
