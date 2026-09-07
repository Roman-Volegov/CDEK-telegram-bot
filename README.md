# Бот СДЭК для Telegram

Telegram-бот для расчёта стоимости доставки СДЭК и создания заказа с печатью **накладной** и **штрихкодов** (PDF A4).

## Что умеет

- Заявки на доступ: админ approve/reject, повтор через 24ч (`ADMIN_TELEGRAM_IDS`)
- Мастер начальной настройки (`/setup`): каждый пользователь вводит свои секреты СДЭК/DaData и параметры отправки
- Секреты хранятся в БД **зашифрованно** (Fernet, ключ `ENCRYPTION_KEY`) с привязкой к Telegram user id
- Распознать адрес в свободной форме (DaData Clean)
- Посчитать тарифы СДЭК (`/calculator/tarifflist`) от города вашего ПВЗ отгрузки
- Создать заказ с номером своего нумератора `YYYY-NNNNNN` (например `2026-000001`)
- Перед созданием можно изменить все поля текущего заказа
- Наложенный платёж всегда `0`
- После создания заказа присылает два PDF: накладная + штрихкоды
- «Мои заказы»: статус из СДЭК, пагинация по 3; edit/cancel/delete только для локальных заказов (без `cdek_uuid`)
- Повторная выгрузка PDF: номер заказа, `/pdf …` или кнопка в истории (докачивает из СДЭК при необходимости)

## Стек

- Python 3.12, aiogram 3
- PostgreSQL (заказы + нумератор + профили)
- Redis (FSM-состояния)
- Docker Compose

## Быстрый старт (Docker)

### 1. Подготовка

1. Создайте бота в [@BotFather](https://t.me/BotFather) → получите `BOT_TOKEN`
2. Сгенерируйте ключ шифрования (любая длинная случайная строка) → `ENCRYPTION_KEY`
3. Узнайте свой Telegram ID (напишите боту `/id` или [@userinfobot](https://t.me/userinfobot)) → `ADMIN_TELEGRAM_IDS`
4. Ключи СДЭК и DaData пользователи вводят сами в боте через `/setup`

### 2. Конфиг

```bash
cp .env.example .env
```

Отредактируйте `.env`:

```env
BOT_TOKEN=...
ENCRYPTION_KEY=длинная-случайная-строка
ADMIN_TELEGRAM_IDS=123456789
DATABASE_URL=postgresql+asyncpg://cdek:cdek@db:5432/cdek_bot
REDIS_URL=redis://redis:6379/0
```

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

Затем:

```bash
python -m app.main
```

Проверка импортов:

```bash
python scripts/check_imports.py
```

---

## Ограничение доступа

Доступ выдаётся через заявки администратору.

```env
ADMIN_TELEGRAM_IDS=123456789,@admin_username
```

Опционально — старый whitelist: числовые ID сразу получают `approved` при старте бота:

```env
ALLOWED_TELEGRAM_IDS=123456789
```

Как узнать ID:

1. Напишите боту `/id`
2. Или откройте [@userinfobot](https://t.me/userinfobot)

После изменения `.env`:

```bash
docker compose up -d --force-recreate bot
```

Если `ADMIN_TELEGRAM_IDS` и `ALLOWED_TELEGRAM_IDS` пусты — заявки некому отправлять (бот пишет warning в лог).

## Как пользоваться ботом

| Действие | Команда / кнопка |
|----------|------------------|
| Старт | `/start` |
| Запросить доступ | `🔑 Запросить доступ` или `/request_access` |
| Настройки (секреты СДЭК/DaData) | `⚙️ Настройки` или `/setup` |
| Только расчёт | `📦 Рассчитать` или `/calc` |
| Создать заказ + PDF | `🚚 Создать заказ` или `/order` |
| Список заказов | `📋 Мои заказы` или `/orders` |
| Повторно скачать PDF | номер `2026-000001` или `/pdf 2026-000001` |
| Справка | `/help` |
| Отмена сценария | `/cancel` |

### Сценарий заказа

1. Адрес → подтверждение распознанного варианта  
2. Выбор тарифа  
3. Если тариф до ПВЗ — код ПВЗ получателя  
4. ФИО и телефон  
5. Стоимость товара (для декларации; НП = 0)  
6. При необходимости — правка любых полей  
7. Подтверждение → номер `2026-000001` → заказ в СДЭК → два PDF  

PDF сохраняются в `storage/pdfs/`:

- `nakladnaya_2026-000001.pdf`
- `shtrihkody_2026-000001.pdf`

Если заказ создан в СДЭК, а PDF ещё не готовы (`created_no_pdf`), повторный запрос номера докачает формы из API.

---

## Структура проекта

```text
├── app/
│   ├── main.py              # точка входа
│   ├── config.py            # настройки из .env
│   ├── bot/                 # хендлеры, FSM, клавиатуры
│   ├── db/                  # модели, нумератор
│   └── services/            # CDEK API, DaData, профили, доступ
├── storage/pdfs/            # сгенерированные PDF
├── scripts/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Важно

- Глобальные `CDEK_*` / `DADATA_*` в `.env` **не нужны** — каждый пользователь вводит их в `/setup`
- Редактировать / отменять / удалять из истории можно только заказы **без** `cdek_uuid`
- Тарифы считаются от города ПВЗ отгрузки из профиля пользователя
- На тестовом контуре в `/setup` выберите режим «Тест» и используйте тестовые ключи СДЭК

## Стоимость внешних сервисов

- **DaData Clean** (стандартизация адреса): ~0,20 ₽ за адрес; первые 100 бесплатно  
- **СДЭК API**: по вашему договору интернет-магазина  
- **Хостинг**: VPS с Docker (1–2 GB RAM достаточно для MVP)

## Очистка диска на VPS

Docker build cache и журналы systemd могут забить диск — тогда PostgreSQL уходит в recovery, и бот перестаёт отвечать.

Один раз на сервере:

```bash
sudo bash scripts/install-disk-cleanup.sh
```

Это включает:
- лимит journald **100 МБ**
- cron **каждый день в 04:00 UTC**: `docker builder prune`, висячие образы, вакуум журналов
- ротацию логов контейнеров (**10 МБ × 3** файла) в `docker-compose.yml`

Ручной прогон: `make cleanup-disk` или `sudo bash scripts/cleanup-disk.sh`.

Тома БД и запущенные контейнеры скрипт не удаляет.

---

## Типичные проблемы

| Симптом | Что проверить |
|---------|----------------|
| Бот не отвечает | `BOT_TOKEN`, логи `docker compose logs bot` |
| Заявки не приходят админу | числовой ID в `ADMIN_TELEGRAM_IDS`, админ хотя раз писал боту |
| Ошибка DaData 401/403 | ключи в `/setup`, баланс Clean |
| Ошибка OAuth СДЭК | client id/secret и режим test/prod в `/setup` |
| Тарифы пустые / странные | код ПВЗ отгрузки, габариты, тип договора ИМ |
| PDF не приходят | заказ принят СДЭК? повторите номер заказа или `/pdf` |
| Дубликат номера | номер уникален в рамках договора; нумератор не откатывается при ошибке |

## Дальнейшее развитие

- Webhook вместо polling  
- Выбор ПВЗ из списка/карты  
- Точечное редактирование настроек без полного `/setup`  
- Заявка на забор курьером (`/intakes`)  
- Alembic-миграции вместо `create_all`
