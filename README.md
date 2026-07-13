# Telegram → WordPress Publisher Bot

Бот для публикации статей на [инвестиционно.рф](https://инвестиционно.рф) из Telegram.

## Что делает

1. Принимает ссылку или текст
2. Парсит и рерайтит через LLM (Moonshot)
3. Автоматически подбирает категории из 13 таксономий WordPress
4. Генерирует featured image
5. Показывает превью с возможностью редактировать текст и категории
6. Публикует на сайт через WordPress REST API

## Стек

- Python 3.11 + aiogram 3.x
- SQLite (черновики, кэш таксономии)
- Moonshot API (рерайт + категоризация)
- Railway (деплой)

## Деплой на Railway

1. Залейте код в GitHub-репозиторий
2. [Railway](https://railway.app) → New Project → Deploy from GitHub repo
3. Variables → добавьте все из `.env.example`
4. Volume → Name: `bot-data`, Mount: `/app/data`
5. Готово — деплой автоматический

## Переменные окружения

| Переменная | Описание |
|------------|----------|
| `TG_BOT_TOKEN` | Токен от @BotFather |
| `ALLOWED_USER_IDS` | Telegram user ID (через @userinfobot) |
| `WP_BASE_URL` | URL WordPress-сайта |
| `WP_LOGIN` | Логин администратора WP |
| `WP_PASSWORD` | Application Password из WP |
| `MOONSHOT_API_KEY` | API ключ Moonshot |
| `MOONSHOT_BASE_URL` | `https://api.moonshot.ai/v1` |
| `MOONSHOT_MODEL` | `moonshot-v1-128k` |
| `KIMI_API_KEY` | API ключ Kimi для картинок |

## Команды бота

- `/start` — начать
- `/cancel` — отменить
- `/sync` — обновить кэш таксономий с сайта
