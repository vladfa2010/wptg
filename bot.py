"""
Telegram bot for publishing rewritten articles to инвестиционно.рф
Aiogram 3.x + FSM + inline category editor.
"""
import asyncio
import logging
from typing import Any

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

import config
import database
from services import image_generator, llm, parser, wordpress

# ─── Logging ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Bot & Dispatcher ─────────────────────────────────────
config.validate()
storage = MemoryStorage()
bot = Bot(token=config.TG_BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ─── FSM States ───────────────────────────────────────────
class Form(StatesGroup):
    idle = State()               # Waiting for URL or text
    processing = State()         # Parse + rewrite + categorize
    preview = State()            # Showing preview with buttons
    editing_text_field = State() # Choose which field to edit
    editing_title = State()      # Edit title
    editing_content = State()    # Edit content
    editing_excerpt = State()    # Edit excerpt
    editing_categories = State() # Choose taxonomy to edit
    editing_category_terms = State()  # Toggle terms in a taxonomy

# ─── Whitelist Middleware ─────────────────────────────────
class WhitelistMiddleware:
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and user.id not in config.ALLOWED_USER_IDS:
            if isinstance(event, types.Message):
                await event.answer("⛔ Доступ запрещён.")
            return None
        return await handler(event, data)

dp.message.middleware(WhitelistMiddleware())
dp.callback_query.middleware(WhitelistMiddleware())

# ─── Callback Data ────────────────────────────────────────
from aiogram.filters.callback_data import CallbackData

class PreviewAction(CallbackData, prefix="preview"):
    action: str  # publish, edit_text, edit_categories, regenerate, cancel

class TextFieldAction(CallbackData, prefix="textfield"):
    field: str  # title, content, excerpt, back

class TaxonomySelect(CallbackData, prefix="tax"):
    taxonomy: str

class TermToggle(CallbackData, prefix="toggle"):
    taxonomy: str
    term_id: int

class TermPage(CallbackData, prefix="tpage"):
    taxonomy: str
    page: int

class CatBack(CallbackData, prefix="catback"):
    action: str  # to_taxonomies, to_preview

# ─── Helpers ──────────────────────────────────────────────
def _preview_text(title: str, excerpt: str, taxonomies: dict[str, list[int]], all_terms: dict[str, list[dict[str, Any]]]) -> str:
    lines = [f"📋 <b>ПРЕВЬЮ ПУБЛИКАЦИИ</b>\n", f"📰 {title}", f"\n📝 {excerpt or '(без описания)'}"]

    # Build human-readable taxonomy summary
    tax_labels = {
        "categories": "Рубрика", "industriya": "Индустрия", "kompaniya": "Компания",
        "tiker": "Тикер", "trend": "Тренд", "strategiya_investirovaniya": "Стратегия",
        "stadiya_sdelki": "Стадия сделки", "stadiya_proekta": "Стадия проекта",
        "etapy_sdelki": "Этап сделки", "klassifikaciya_po_rynkam": "Рынок",
        "obuchenie": "Обучение", "partnyor": "Партнёр", "tags": "Метка",
    }

    lines.append("\n🏷 <b>Категории:</b>")
    for key, label in tax_labels.items():
        ids = taxonomies.get(key, [])
        if ids:
            names = []
            for tid in ids:
                for term in all_terms.get(key, []):
                    if term["term_id"] == tid or term["id"] == tid:
                        names.append(term["name"])
                        break
            if names:
                lines.append(f"   <i>{label}:</i> {', '.join(names)}")

    return "\n".join(lines)

def _shorten(text: str, max_len: int = 3500) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n... <i>(текст обрезан для превью)</i>"

# ─── /start ───────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(Form.idle)
    await message.answer(
        "👋 Бот для публикации на <b>инвестиционно.рф</b>\n\n"
        "Пришлите мне <b>ссылку</b> на новость или <b>текст</b> — я рерайтну, "
        "подберу категории и опубликую.\n\n"
        "Команды:\n"
        "/cancel — отменить текущую операцию\n"
        "/sync — обновить кэш таксономий с сайта",
        parse_mode="HTML",
    )

# ─── /cancel ──────────────────────────────────────────────
@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    data = await state.get_data()
    draft_id = data.get("draft_id")
    if draft_id:
        await database.delete_draft(draft_id)
    await state.clear()
    await state.set_state(Form.idle)
    await message.answer("❌ Отменено. Пришлите новую ссылку или текст.")

# ─── /sync ────────────────────────────────────────────────
@dp.message(Command("sync"))
async def cmd_sync(message: types.Message, state: FSMContext):
    status = await message.answer("🔄 Синхронизация таксономий...")
    try:
        taxonomies = await wordpress.sync_taxonomies()
        await database.clear_taxonomy_cache()
        for tax_name, terms in taxonomies.items():
            await database.upsert_taxonomy_terms(tax_name, terms)
        total = sum(len(v) for v in taxonomies.values())
        await status.edit_text(f"✅ Синхронизировано {total} терминов из {len(taxonomies)} таксономий.")
    except Exception as exc:
        logger.exception("Sync failed")
        await status.edit_text(f"❌ Ошибка синхронизации: {exc}")

# ─── URL/Text input (idle state) ──────────────────────────
@dp.message(Form.idle, F.text)
async def on_input(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if not text:
        return

    # Check for duplicate by source URL
    if text.startswith("http://") or text.startswith("https://"):
        existing = await database.get_publication_by_source(text)
        if existing:
            await message.answer(f"⚠️ Эта ссылка уже публиковалась:\n{existing['wp_post_url']}")
            return

    await state.set_state(Form.processing)
    status_msg = await message.answer("⏳ Обработка...")

    try:
        # Step 1: Parse
        logger.info("Step 1: Parsing input...")
        await status_msg.edit_text("⏳ Шаг 1/4: Парсинг...")
        if text.startswith("http://") or text.startswith("https://"):
            parsed = await parser.parse_url(text)
            source_url = text
            original_title = parsed["title"]
            content_text = parsed["text"]
            og_image_url = parsed["og_image"]
            logger.info("Parsed URL: title=%s, text_len=%d", original_title, len(content_text))
        else:
            parsed = None
            source_url = ""
            original_title = ""
            content_text = text
            og_image_url = ""

        if not content_text or len(content_text) < 100:
            await status_msg.edit_text("❌ Текст слишком короткий для рерайта. Пришлите другой материал.")
            await state.set_state(Form.idle)
            return

        # Step 2: Rewrite
        logger.info("Step 2: Rewriting...")
        await status_msg.edit_text("⏳ Шаг 2/4: Рерайт через LLM...")
        rewritten = await llm.rewrite(content_text, original_title)
        logger.info("Rewritten: title=%s", rewritten["title"])

        # Step 3: Load taxonomy cache & categorize
        logger.info("Step 3: Categorizing...")
        await status_msg.edit_text("⏳ Шаг 3/4: Категоризация...")
        all_terms = await database.get_all_active_taxonomies()
        if not all_terms:
            logger.info("Taxonomy cache empty, auto-syncing...")
            await status_msg.edit_text("⏳ Шаг 3/4: Кэш пуст, синхронизация...")
            wp_taxonomies = await wordpress.sync_taxonomies()
            for tax_name, terms in wp_taxonomies.items():
                await database.upsert_taxonomy_terms(tax_name, terms)
            all_terms = await database.get_all_active_taxonomies()

        taxonomies = await llm.categorize(rewritten["content"], all_terms)
        logger.info("Categories assigned: %s", taxonomies)

        # Step 4: Generate image
        logger.info("Step 4: Generating image...")
        await status_msg.edit_text("⏳ Шаг 4/4: Генерация картинки...")
        image_data = await image_generator.generate_image(
            rewritten["title"], rewritten["excerpt"]
        )
        featured_media_id = 0
        if image_data:
            try:
                featured_media_id = await wordpress.upload_media(
                    image_data, filename="featured.jpg"
                )
                logger.info("Generated image uploaded: media_id=%d", featured_media_id)
            except Exception as img_exc:
                logger.warning("Image upload failed: %s", img_exc)
                if og_image_url:
                    try:
                        og_data = await parser.download_image(og_image_url)
                        featured_media_id = await wordpress.upload_media(
                            og_data, filename="og_image.jpg"
                        )
                    except Exception:
                        pass
        elif og_image_url:
            try:
                og_data = await parser.download_image(og_image_url)
                featured_media_id = await wordpress.upload_media(
                    og_data, filename="og_image.jpg"
                )
            except Exception:
                pass

        # Save draft
        draft_id = await database.create_draft(
            tg_user_id=message.from_user.id,
            title=rewritten["title"],
            content=rewritten["content"],
            excerpt=rewritten["excerpt"],
            source_url=source_url,
            taxonomies=taxonomies,
            featured_media_id=featured_media_id,
        )
        logger.info("Draft saved: id=%d", draft_id)

        await state.update_data(
            draft_id=draft_id,
            taxonomies=taxonomies,
        )
        await state.set_state(Form.preview)

        # Show preview
        preview = _preview_text(rewritten["title"], rewritten["excerpt"], taxonomies, all_terms)
        kb = types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="✅ Опубликовать", callback_data=PreviewAction(action="publish").pack()),
                types.InlineKeyboardButton(text="✏️ Текст", callback_data=PreviewAction(action="edit_text").pack()),
            ],
            [
                types.InlineKeyboardButton(text="🏷 Категории", callback_data=PreviewAction(action="edit_categories").pack()),
                types.InlineKeyboardButton(text="🔄 Заново", callback_data=PreviewAction(action="regenerate").pack()),
            ],
            [
                types.InlineKeyboardButton(text="❌ Отмена", callback_data=PreviewAction(action="cancel").pack()),
            ],
        ])
        await status_msg.edit_text(preview, parse_mode="HTML", reply_markup=kb)

    except Exception as exc:
        logger.exception("Processing failed: %s", exc)
        error_msg = f"❌ Ошибка: {str(exc)[:300]}\n\nПопробуйте /cancel и пришлите другой материал."
        try:
            await status_msg.edit_text(error_msg)
        except Exception:
            await message.answer(error_msg)
        await state.set_state(Form.idle)

# ─── Preview callbacks ────────────────────────────────────
@dp.callback_query(Form.preview, PreviewAction.filter(F.action == "publish"))
async def cb_publish(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Публикация...")
    data = await state.get_data()
    draft_id = data["draft_id"]
    draft = await database.get_draft(draft_id)
    if not draft:
        await callback.message.edit_text("❌ Черновик не найден. Начните заново.")
        await state.set_state(Form.idle)
        return

    try:
        result = await wordpress.create_post({
            "title": draft["title"],
            "content": draft["content"],
            "excerpt": draft["excerpt"] or "",
            "status": "publish",
            "featured_media": draft.get("featured_media_id", 0) or 0,
            **draft["taxonomies"],
        })

        await database.log_publication(
            draft_id=draft_id,
            wp_post_id=result["id"],
            wp_post_url=result["url"],
            title=draft["title"],
            taxonomies=draft["taxonomies"],
            source_url=draft.get("source_url", ""),
        )
        await database.delete_draft(draft_id)

        await callback.message.edit_text(
            f"✅ <b>Опубликовано!</b>\n\n"
            f"🔗 <a href='{result['url']}'>{result['url']}</a>\n"
            f"📊 ID поста: {result['id']}\n\n"
            f"Пришлите новую ссылку или текст.",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await state.clear()
        await state.set_state(Form.idle)

    except Exception as exc:
        logger.exception("Publish failed")
        await callback.message.edit_text(f"❌ Ошибка публикации: {exc}")

@dp.callback_query(Form.preview, PreviewAction.filter(F.action == "cancel"))
async def cb_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Отменено")
    data = await state.get_data()
    if data.get("draft_id"):
        await database.delete_draft(data["draft_id"])
    await state.clear()
    await state.set_state(Form.idle)
    await callback.message.edit_text("❌ Отменено. Пришлите новую ссылку или текст.")

@dp.callback_query(Form.preview, PreviewAction.filter(F.action == "regenerate"))
async def cb_regenerate(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Перегенерация...")
    data = await state.get_data()
    if data.get("draft_id"):
        await database.delete_draft(data["draft_id"])

    # Get original text back... we need to store it
    # For now just go back to idle
    await state.clear()
    await state.set_state(Form.idle)
    await callback.message.edit_text(
        "🔄 Готово к новой генерации. Пришлите ссылку или текст."
    )

# ─── Edit Text flow ───────────────────────────────────────
@dp.callback_query(Form.preview, PreviewAction.filter(F.action == "edit_text"))
async def cb_edit_text(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Редактирование текста")
    await state.set_state(Form.editing_text_field)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📰 Заголовок", callback_data=TextFieldAction(field="title").pack()),
            types.InlineKeyboardButton(text="📝 Контент", callback_data=TextFieldAction(field="content").pack()),
        ],
        [
            types.InlineKeyboardButton(text="📄 Excerpt", callback_data=TextFieldAction(field="excerpt").pack()),
            types.InlineKeyboardButton(text="↩️ Назад", callback_data=TextFieldAction(field="back").pack()),
        ],
    ])
    await callback.message.edit_text("✏️ <b>Редактирование текста</b>\n\nЧто изменить?", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(Form.editing_text_field, TextFieldAction.filter(F.field == "back"))
async def cb_text_back(callback: types.CallbackQuery, state: FSMContext):
    await _return_to_preview(callback, state)

@dp.callback_query(Form.editing_text_field, TextFieldAction.filter(F.field == "title"))
async def cb_edit_title(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    draft = await database.get_draft(data["draft_id"])
    await state.set_state(Form.editing_title)
    await callback.message.edit_text(
        f"📰 <b>Текущий заголовок:</b>\n{draft['title']}\n\n"
        f"Пришлите новый заголовок (или /skip):",
        parse_mode="HTML",
    )

@dp.message(Form.editing_title)
async def on_edit_title(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "/skip":
        pass
    elif len(text) > 200:
        await message.answer("⚠️ Заголовок слишком длинный (макс 200 символов). Попробуйте снова.")
        return
    elif not text:
        await message.answer("⚠️ Заголовок не может быть пустым.")
        return
    else:
        data = await state.get_data()
        await database.update_draft(data["draft_id"], title=text)

    await state.set_state(Form.editing_text_field)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📰 Заголовок", callback_data=TextFieldAction(field="title").pack()),
            types.InlineKeyboardButton(text="📝 Контент", callback_data=TextFieldAction(field="content").pack()),
        ],
        [
            types.InlineKeyboardButton(text="📄 Excerpt", callback_data=TextFieldAction(field="excerpt").pack()),
            types.InlineKeyboardButton(text="↩️ Назад", callback_data=TextFieldAction(field="back").pack()),
        ],
    ])
    await message.answer("✏️ <b>Редактирование текста</b>\n\nЧто ещё изменить?", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(Form.editing_text_field, TextFieldAction.filter(F.field == "content"))
async def cb_edit_content(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    draft = await database.get_draft(data["draft_id"])
    preview = _shorten(draft["content"])
    await state.set_state(Form.editing_content)
    await callback.message.edit_text(
        f"📝 <b>Текущий контент</b> ({len(draft['content'])} символов):\n\n"
        f"<pre>{preview}</pre>\n\n"
        f"Пришлите новый текст (поддерживается HTML):",
        parse_mode="HTML",
    )

@dp.message(Form.editing_content)
async def on_edit_content(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "/skip":
        pass
    elif not text:
        await message.answer("⚠️ Контент не может быть пустым.")
        return
    else:
        # Auto-wrap in <p> if no HTML tags
        if "<" not in text:
            text = "\n\n".join(f"<p>{p}</p>" for p in text.split("\n\n") if p.strip())
        data = await state.get_data()
        await database.update_draft(data["draft_id"], content=text)

    await state.set_state(Form.editing_text_field)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📰 Заголовок", callback_data=TextFieldAction(field="title").pack()),
            types.InlineKeyboardButton(text="📝 Контент", callback_data=TextFieldAction(field="content").pack()),
        ],
        [
            types.InlineKeyboardButton(text="📄 Excerpt", callback_data=TextFieldAction(field="excerpt").pack()),
            types.InlineKeyboardButton(text="↩️ Назад", callback_data=TextFieldAction(field="back").pack()),
        ],
    ])
    await message.answer("✏️ <b>Редактирование текста</b>\n\nЧто ещё изменить?", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(Form.editing_text_field, TextFieldAction.filter(F.field == "excerpt"))
async def cb_edit_excerpt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    data = await state.get_data()
    draft = await database.get_draft(data["draft_id"])
    await state.set_state(Form.editing_excerpt)
    await callback.message.edit_text(
        f"📄 <b>Текущий excerpt:</b>\n{draft['excerpt'] or '(пусто)'}\n\n"
        f"Пришлите новый excerpt (или /skip):",
        parse_mode="HTML",
    )

@dp.message(Form.editing_excerpt)
async def on_edit_excerpt(message: types.Message, state: FSMContext):
    text = message.text.strip()
    if text == "/skip":
        pass
    else:
        data = await state.get_data()
        await database.update_draft(data["draft_id"], excerpt=text)

    await state.set_state(Form.editing_text_field)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="📰 Заголовок", callback_data=TextFieldAction(field="title").pack()),
            types.InlineKeyboardButton(text="📝 Контент", callback_data=TextFieldAction(field="content").pack()),
        ],
        [
            types.InlineKeyboardButton(text="📄 Excerpt", callback_data=TextFieldAction(field="excerpt").pack()),
            types.InlineKeyboardButton(text="↩️ Назад", callback_data=TextFieldAction(field="back").pack()),
        ],
    ])
    await message.answer("✏️ <b>Редактирование текста</b>\n\nЧто ещё изменить?", parse_mode="HTML", reply_markup=kb)

# ─── Edit Categories flow ─────────────────────────────────
@dp.callback_query(Form.preview, PreviewAction.filter(F.action == "edit_categories"))
async def cb_edit_categories(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Редактирование категорий")
    await state.set_state(Form.editing_categories)

    # Build taxonomy selection keyboard
    tax_labels = {
        "categories": "Рубрики", "industriya": "Индустрии", "kompaniya": "Компании",
        "tiker": "Тикеры", "trend": "Тренды", "strategiya-investirovaniya": "Стратегии",
        "stadiya-sdelki": "Стадии сделки", "stadiya-proekta": "Стадии проекта",
        "etapy-sdelki": "Этапы сделки", "klassifikaciya-po-rynkam": "Рынки",
        "obuchenie": "Обучение", "partnyor": "Партнёры", "tags": "Метки",
    }

    buttons = []
    row = []
    for tax_key, label in tax_labels.items():
        row.append(types.InlineKeyboardButton(
            text=label, callback_data=TaxonomySelect(taxonomy=tax_key).pack()
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        types.InlineKeyboardButton(text="↩️ Назад к превью", callback_data=CatBack(action="to_preview").pack()),
    ])

    await callback.message.edit_text(
        "🏷 <b>Редактирование категорий</b>\n\nВыберите таксономию:",
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )

@dp.callback_query(Form.editing_categories, CatBack.filter(F.action == "to_preview"))
async def cb_cat_to_preview(callback: types.CallbackQuery, state: FSMContext):
    await _return_to_preview(callback, state)

async def _return_to_preview(callback: types.CallbackQuery, state: FSMContext):
    """Go back to preview state, re-rendering the preview message."""
    data = await state.get_data()
    draft = await database.get_draft(data["draft_id"])
    all_terms = await database.get_all_active_taxonomies()
    preview = _preview_text(draft["title"], draft["excerpt"], draft["taxonomies"], all_terms)
    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="✅ Опубликовать", callback_data=PreviewAction(action="publish").pack()),
            types.InlineKeyboardButton(text="✏️ Текст", callback_data=PreviewAction(action="edit_text").pack()),
        ],
        [
            types.InlineKeyboardButton(text="🏷 Категории", callback_data=PreviewAction(action="edit_categories").pack()),
            types.InlineKeyboardButton(text="🔄 Заново", callback_data=PreviewAction(action="regenerate").pack()),
        ],
        [
            types.InlineKeyboardButton(text="❌ Отмена", callback_data=PreviewAction(action="cancel").pack()),
        ],
    ])
    await state.set_state(Form.preview)
    await callback.message.edit_text(preview, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(Form.editing_categories, TaxonomySelect.filter())
async def cb_select_taxonomy(callback: types.CallbackQuery, state: FSMContext, callback_data: TaxonomySelect):
    await callback.answer()
    taxonomy = callback_data.taxonomy
    await state.update_data(editing_taxonomy=taxonomy)
    await _show_term_page(callback, state, taxonomy, page=0)

async def _show_term_page(callback: types.CallbackQuery, state: FSMContext, taxonomy: str, page: int):
    """Show a paginated page of terms for a taxonomy."""
    await state.set_state(Form.editing_category_terms)
    data = await state.get_data()
    draft = await database.get_draft(data["draft_id"])
    selected_ids = set(draft["taxonomies"].get(taxonomy, []))

    terms = await database.get_taxonomy_terms(taxonomy)
    if not terms:
        await callback.message.edit_text(f"🏷 Нет доступных терминов для {taxonomy}.")
        return

    total_pages = (len(terms) + config.PAGINATION_SIZE - 1) // config.PAGINATION_SIZE
    page = max(0, min(page, total_pages - 1))
    start = page * config.PAGINATION_SIZE
    end = start + config.PAGINATION_SIZE
    page_terms = terms[start:end]

    buttons = []
    for term in page_terms:
        icon = "✅" if term["term_id"] in selected_ids else "⬜"
        label = f"{icon} {term['name']} ({term['count']})"
        buttons.append([types.InlineKeyboardButton(
            text=label,
            callback_data=TermToggle(taxonomy=taxonomy, term_id=term["term_id"]).pack(),
        )])

    # Pagination row
    nav = []
    if page > 0:
        nav.append(types.InlineKeyboardButton(
            text="⬅️", callback_data=TermPage(taxonomy=taxonomy, page=page - 1).pack()
        ))
    nav.append(types.InlineKeyboardButton(
        text=f"{page + 1}/{total_pages}", callback_data="noop"
    ))
    if page < total_pages - 1:
        nav.append(types.InlineKeyboardButton(
            text="➡️", callback_data=TermPage(taxonomy=taxonomy, page=page + 1).pack()
        ))
    buttons.append(nav)

    buttons.append([
        types.InlineKeyboardButton(
            text="↩️ К таксономиям", callback_data=CatBack(action="to_taxonomies").pack()
        ),
    ])

    tax_labels = {
        "categories": "Рубрики", "industriya": "Индустрии", "kompaniya": "Компании",
        "tiker": "Тикеры", "trend": "Тренды", "strategiya-investirovaniya": "Стратегии",
        "stadiya-sdelki": "Стадии сделки", "stadiya-proekta": "Стадии проекта",
        "etapy-sdelki": "Этапы сделки", "klassifikaciya-po-rynkam": "Рынки",
        "obuchenie": "Обучение", "partnyor": "Партнёры", "tags": "Метки",
    }
    label = tax_labels.get(taxonomy, taxonomy)

    await callback.message.edit_text(
        f"🏷 <b>{label}</b> (стр. {page + 1}/{total_pages})\n\n"
        f"Нажмите для выбора/снятия:",
        parse_mode="HTML",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )

@dp.callback_query(Form.editing_category_terms, CatBack.filter(F.action == "to_taxonomies"))
async def cb_back_to_taxonomies(callback: types.CallbackQuery, state: FSMContext):
    # Re-enter taxonomy selection
    await cb_edit_categories(callback, state)

@dp.callback_query(Form.editing_category_terms, TermToggle.filter())
async def cb_toggle_term(callback: types.CallbackQuery, state: FSMContext, callback_data: TermToggle):
    data = await state.get_data()
    draft = await database.get_draft(data["draft_id"])
    taxonomies = dict(draft["taxonomies"])
    taxonomy = callback_data.taxonomy
    term_id = callback_data.term_id

    current = list(taxonomies.get(taxonomy, []))
    if term_id in current:
        current.remove(term_id)
    else:
        current.append(term_id)

    taxonomies[taxonomy] = current
    await database.update_draft(data["draft_id"], taxonomies=taxonomies)
    await state.update_data(taxonomies=taxonomies)

    # Refresh page
    await _show_term_page(callback, state, taxonomy, _get_current_page(callback.message.reply_markup))

@dp.callback_query(Form.editing_category_terms, TermPage.filter())
async def cb_term_page(callback: types.CallbackQuery, state: FSMContext, callback_data: TermPage):
    await _show_term_page(callback, state, callback_data.taxonomy, callback_data.page)

def _get_current_page(reply_markup) -> int:
    """Extract current page number from pagination button text."""
    if not reply_markup or not reply_markup.inline_keyboard:
        return 0
    for row in reply_markup.inline_keyboard:
        for btn in row:
            text = btn.text if hasattr(btn, "text") else str(btn)
            if "/" in text:
                try:
                    return int(text.split("/")[0]) - 1
                except ValueError:
                    pass
    return 0

# ─── Healthcheck HTTP server ──────────────────────────────
async def _health_server():
    app = web.Application()
    app.router.add_get("/health", lambda r: web.Response(text="ok", status=200))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logger.info("Healthcheck server on port 8080")

# ─── Main ─────────────────────────────────────────────────
async def main():
    await database.init_db()
    logger.info("Bot starting... Allowed users: %s", config.ALLOWED_USER_IDS)
    await asyncio.gather(
        dp.start_polling(bot, skip_updates=True),
        _health_server(),
    )

if __name__ == "__main__":
    asyncio.run(main())
