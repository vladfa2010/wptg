# ТЗ: Telegram-бот для публикации статей на инвестиционно.рф

## 1. Общее описание

Telegram-бот для единого пользователя (администратора), который принимает ссылку на новость или текст, рерайтит её в полноценную статью в человеческом стиле, автоматически определяет таксономию (динамически) и публикует на WordPress-сайт `инвестиционно.рф` через REST API с поддержкой Custom Post Types.

---

## 2. Технический стек

| Компонент | Технология |
|-----------|------------|
| Бот | Python 3.11+, aiogram 3.x |
| LLM-рерайт | Moonshot API (`api.moonshot.ai`) |
| Генерация картинок | Moonshot API (fallback — OG-изображение) |
| БД бота | SQLite (через aiosqlite) |
| WordPress API | REST API v2 (Application Password) |
| Парсинг | requests + BeautifulSoup4 |
| Деплой | Railway (Docker) |

---

## 3. Архитектура потока

```
[Telegram] → [Бот] → [Парсер] → [LLM Рерайт (человеческий стиль)]
                                              ↓
[WordPress CPT] ← [API POST] ← [Превью: полный текст + таксономия + кнопки] ← [Категоризация LLM]
```

---

## 4. Функциональные требования

### 4.1 Получение контента

Бот принимает:
- **URL** — ссылка на новость
- **Текст** — если пользователь прислал текстом

Команды:
- `/start` — приветствие
- `/cancel` — отмена
- `/sync` — обновить кэш таксономий

### 4.2 Рерайт через LLM

**Промпт включает 8 техник человеческого стиля:**
1. **Burstiness** — варьирование длины предложений
2. **Эмоциональные маркеры** — риторические вопросы, ирония, личная позиция
3. **Разговорный стиль** — замена канцеляризмов
4. **Структурные неровности** — тире, скобки, эллипсис
5. **Идиомы** — "что, согласитесь, немало", "мягко говоря"
6. **Текстовая шероховатость** — естественная пунктуация
7. **Конкретика** — аналогии, сравнения вместо абстракций
8. **Контекст** — исторические параллели, прогнозы с оговорками

Объем: 800–1500 слов. Структура: ввод → контекст → суть → влияние → выводы.

### 4.3 Категоризация через LLM

Динамически подставляются все таксономии из кэша. LLM возвращает JSON с ID терминов только из существующих вариантов.

### 4.4 Превью и подтверждение

После рерайта бот отправляет:

```
📊 Объем текста: {N} символов
🤖 Таксономия (LLM): {категории}
📝 Полный текст статьи (без обрезки)
```

Кнопки:
- ✅ **Опубликовать** — сразу на сайт
- 📝 **Черновик** — сохранить как `draft` в WP
- ✏️ **Редактировать текст** — заголовок / контент / excerpt
- 🏷 **Редактировать категории** — чекбоксы с пагинацией
- 🔄 **Заново** — перезапуск пайплайна
- ❌ **Отмена**

### 4.5 Inline-коррекция

В состоянии `preview` пользователь может написать текстом: "сделай заголовок короче", "добавь абзац про риски" — LLM применит правку автоматически.

### 4.6 Custom Post Types

| Категория | CPT | Endpoint |
|-----------|-----|----------|
| Новости | `novost` | `/wp/v2/novost` |
| Событие | `sobytie` | `/wp/v2/sobytie` |
| Аналитика | `analitika` | `/wp/v2/analitika` |
| Интересное | `interesnoe` | `/wp/v2/interesnoe` |
| Компания | `kompaniya-p` | `/wp/v2/kompaniya-p` |
| Тренды | `trends` | `/wp/v2/trends` |
| (fallback) | `novost` | `/wp/v2/novost` |

CPT определяется по первой известной категории в момент публикации.

---

## 5. FSM-состояния

```
idle → processing → preview → editing_text_field / editing_title / editing_content / editing_excerpt / editing_categories → preview → publishing → idle
```

Контекст FSM:
```python
{
    "draft_id": int,
    "source_url": str,
    "title": str,
    "content": str,
    "excerpt": str,
    "taxonomies": dict[str, list[int]],
    "featured_media_id": int | None
}
```

---

## 6. Структура проекта

```
wptg/
├── bot.py                  # FSM, хендлеры, inline-клавиатуры
├── config.py               # ENV-переменные
├── database.py             # SQLite: drafts, publications, taxonomy_cache
├── services/
│   ├── llm.py              # Рерайт + категоризация
│   ├── wordpress.py        # REST API клиент, CPT-маппинг
│   ├── parser.py           # Парсинг URL
│   └── image_generator.py  # Генерация картинок
├── data/                   # Volume для SQLite
├── Dockerfile
├── railway.json
├── requirements.txt
└── README.md
```

---

## 7. Деплой на Railway

1. GitHub-репозиторий → Railway (auto-deploy)
2. Variables: `TG_BOT_TOKEN`, `ALLOWED_USER_IDS`, `WP_BASE_URL`, `WP_LOGIN`, `WP_PASSWORD`, `MOONSHOT_API_KEY`, `MOONSHOT_BASE_URL`, `MOONSHOT_MODEL`, `RAILWAY_RUN_UID=0`
3. Volume: `/app/data`

---

## 8. ENV-переменные

```env
TG_BOT_TOKEN=...
ALLOWED_USER_IDS=133219563
WP_BASE_URL=https://xn--b1aghca0aeahc1am7c.xn--p1ai
WP_LOGIN=...
WP_PASSWORD=...
MOONSHOT_API_KEY=sk-...
MOONSHOT_BASE_URL=https://api.moonshot.ai/v1
MOONSHOT_MODEL=moonshot-v1-128k
RAILWAY_RUN_UID=0
```

---

## 9. SQLite-схема

```sql
CREATE TABLE drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_user_id INTEGER NOT NULL,
    source_url TEXT,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    excerpt TEXT,
    featured_media_id INTEGER,
    taxonomies_json TEXT,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id INTEGER REFERENCES drafts(id),
    tg_message_id INTEGER,
    wp_post_id INTEGER,
    wp_post_url TEXT,
    source_url TEXT,
    title TEXT,
    taxonomies_json TEXT,
    status TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at TIMESTAMP
);

CREATE TABLE taxonomy_cache (
    term_id INTEGER PRIMARY KEY,
    taxonomy TEXT,
    name TEXT,
    slug TEXT,
    count INTEGER,
    updated_at TIMESTAMP
);
```

---

## 10. Безопасность

| Угроза | Защита |
|--------|--------|
| Несанкционированный доступ | Белый список `ALLOWED_USER_IDS` |
| Утечка WP-пароля | ENV-переменные, не в git |
| Дубли публикаций | Проверка `source_url` в `publications` |

---

## 11. Обработка ошибок

| Сценарий | Действие |
|----------|----------|
| WP API недоступен | Сообщить пользователю, retry |
| Ошибка авторизации WP | "Проверьте Application Password" |
| LLM API недоступен | 2 retry, fallback без рерайта |
| Картинка не сгенерировалась | OG-изображение или без картинки |
| TransferEncodingError | Только непустые таксономии, 60s timeout |

---

## 12. Реализовано

- [x] Парсинг URL + рерайт через Moonshot
- [x] Человеческий стиль (8 техник)
- [x] Динамическое обнаружение таксономий (`/sync`)
- [x] Категоризация LLM с валидацией ID
- [x] Полный текст в превью (без обрезки)
- [x] Объем текста отдельным сообщением
- [x] Таксономия LLM отдельным сообщением
- [x] Редактор текста (заголовок/контент/excerpt) с кнопкой "Назад"
- [x] Редактор категорий (чекбоксы, пагинация)
- [x] Inline-коррекция через текст
- [x] Режим "Черновик" (WP `draft`)
- [x] Custom Post Types (6 типов + fallback)
- [x] Fallback на OG-изображение
- [x] Healthcheck endpoint
- [x] Деплой на Railway

---

## 13. Известные ограничения

- **Генерация картинок через Kimi API** — возвращает 403 (API не открыт для этого ключа). Fallback: OG-изображение из исходной статьи.
- **SSH-ключи для деплоя** — хранятся в `.deploy_key` (в `.gitignore`), персистентны между сессиями.
