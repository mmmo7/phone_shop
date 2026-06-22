import os
import json
import time
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, executor

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logging.warning("python-dotenv is not installed; .env file will not be loaded.")

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("WEBAPP_PORT", "3000"))

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "db.json")
SQLITE_PATH = os.path.join(BASE_DIR, "bot.db")
ORDERS_JSON_PATH = os.path.join(BASE_DIR, "orders.json")

with open(DB_PATH, "r", encoding="utf-8") as f:
    CATALOG = json.load(f)

PENDING = {}  # user_id -> order flow state
SEARCH_STATE = set()
CARTS = {}  # user_id -> list of items in cart
LANG_PREFS = {}  # user_id -> language code ('ru' or 'uz')

BRAND_EMOJIS = {
    "Apple": "🍎",
    "Samsung": "📱",
    "Nokia": "📞",
    "Huawei": "🔴",
    "Oppo": "🟢",
    "Redmi": "🟡",
}

AVAILABLE_COLORS = ["Красный", "Белый", "Черный"]

conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

cursor.execute(
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        phone TEXT,
        address TEXT,
        items TEXT,
        total REAL,
        status TEXT,
        created_at INTEGER
    )
    """
)
conn.commit()


def load_json_orders():
    if not os.path.exists(ORDERS_JSON_PATH):
        with open(ORDERS_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)
        return []
    try:
        with open(ORDERS_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except json.JSONDecodeError:
        pass
    with open(ORDERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump([], f, ensure_ascii=False, indent=2)
    return []


def save_orders_json(order):
    orders = load_json_orders()
    if "id" not in order or not order["id"]:
        next_id = max((o.get("id", 0) for o in orders), default=0) + 1
        order["id"] = next_id
    orders.append(order)
    with open(ORDERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    return order


def delete_order_from_json(order_id):
    orders = load_json_orders()
    filtered = [order for order in orders if order.get("id") != order_id]
    if len(filtered) == len(orders):
        return False
    with open(ORDERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)
    return True


def sync_json_orders_from_db():
    orders = load_json_orders()
    if orders:
        return orders
    cursor.execute("SELECT * FROM orders ORDER BY created_at DESC")
    rows = cursor.fetchall()
    orders_from_db = []
    for row in rows:
        order = dict(row)
        try:
            order["items"] = json.loads(order.get("items") or "[]")
        except json.JSONDecodeError:
            order["items"] = []
        orders_from_db.append(order)
    with open(ORDERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(orders_from_db, f, ensure_ascii=False, indent=2)
    return orders_from_db


def save_order(user_id, user_name, phone, address, items, total):
    cursor.execute(
        "INSERT INTO orders (user_id, user_name, phone, address, items, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, user_name, phone, address, json.dumps(items, ensure_ascii=False), total, "new", int(time.time())),
    )
    conn.commit()
    order_id = cursor.lastrowid
    save_orders_json({
        "id": order_id,
        "user_id": user_id,
        "user_name": user_name,
        "phone": phone,
        "address": address,
        "items": items,
        "total": total,
        "status": "new",
        "created_at": int(time.time()),
    })
    return order_id


def get_last_orders(limit=10):
    cursor.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


def get_last_orders_from_json(limit=10):
    orders = sync_json_orders_from_db()
    orders_sorted = sorted(orders, key=lambda o: o.get("created_at", 0), reverse=True)
    return orders_sorted[:limit]


def delete_order(order_id):
    cursor.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    return cursor.rowcount


def format_order(order):
    items = json.loads(order.get("items", "[]"))
    lines = [f"Заказ #{order['id']} от {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(order['created_at']))}"]
    lines.append(f"Пользователь: {order['user_name']} ({order['user_id']})")
    lines.append(f"Телефон: {order['phone']}")
    lines.append(f"Адрес: {order['address']}")
    lines.append(f"Статус: {order['status']}")
    total = 0.0
    for item in items:
        price = float(item['price'] or 0)
        qty = int(item['quantity'])
        total += price * qty
        lines.append(f"- {item['name']} x{qty} = {price}₽")
    lines.append(f"Итого: {total}₽")
    return "\n".join(lines)


def first_n_brands(n=10):
    return list(CATALOG.keys())[:n]


# Simple translations
TRANSLATIONS = {
    'ru': {
        'choose_brand': "Выберите бренд или используйте /search для поиска телефона. \nКоманда /cart показывает корзину.",
        'cart_command': "Команда /cart показывает корзину.",
        'search_prompt': "Введите бренд или модель для поиска:",
        'empty_search': "По запросу ничего не найдено.",
        'enter_query': "Введите текст запроса для поиска.",
        'admin_only': "Доступ запрещён",
        'lang_prompt': "Выберите язык / Tilni tanlang:",
        'invalid_lang': "Неверный язык.",
        'lang_chosen': "Язык выбран.",
        'models_page': "Модели бренда {brand}: страница {page}/{total_pages}",
        'model_not_found': "Модель не найдена",
        'choose_color': "Выберите цвет телефона ниже:",
        'add_to_cart': "🛍️ В корзину",
        'choose_qty': "Выберите количество товара:",
        'added_to_cart': "Добавлено в корзину: {name} ({color}) x{qty}",
        'added_to_cart_short': "Добавлено в корзину",
        'cart_empty': "Ваша корзина пуста.",
        'your_order': "Ваш заказ:",
        'total': "Итого:",
        'remove_item': "❌ Удалить",
        'clear_cart': "🗑️ Очистить корзину",
        'checkout_button': "✅ Оформить заказ",
        'back_to_brands': "← Назад к брендам",
        'back_to_models': "← Назад к моделям",
        'remove_invalid_index': "Неверный индекс товара.",
        'item_not_found': "Товар не найден.",
        'item_removed': "Товар удалён из корзины.",
        'cart_cleared': "Корзина очищена.",
        'cart_empty_alert': "Корзина пуста",
        'send_contact': "Отправьте контакт для оформления заказа:",
        'contact_button': "Отправить контакт",
        'no_active_order': "Нет активного заказа. Выберите товар командой /catalog",
        'send_address': "Отправьте адрес доставки одним сообщением:",
        'order_received': "Спасибо! Ваш заказ получен. Мы свяжемся с вами в ближайшее время.",
        'admin_panel': "Админ-панель:",
        'no_orders': "Нет заказов.",
        'delete_order': "🗑️ Удалить заказ",
        'order_deleted': "Заказ удалён.",
        'order_delete_not_found': "Заказ не найден.",
        'search_results': "Результаты поиска:",
        'continue_shopping': "🔍 Продолжить покупки",
        'proceed_checkout': "Оформить заказ",
    },
    'uz': {
        'choose_brand': "Brendni tanlang yoki telefonni qidirish uchun /search buyrug'idan foydalaning. \n/cart buyruq savatni ko'rsatadi.",
        'cart_command': "/cart buyruq savatni ko`rsatadi.",
        'search_prompt': "Qidirish uchun brend yoki modelni kiriting:",
        'empty_search': "So'rov bo'yicha hech narsa topilmadi.",
        'enter_query': "Qidiruv uchun matnni kiriting.",
        'admin_only': "Ruxsat rad etildi",
        'lang_prompt': "Tilni tanlang / Выберите язык:",
        'invalid_lang': "Noto'g'ri til.",
        'lang_chosen': "Til tanlandi.",
        'models_page': "{brand} brendining modellari: sahifa {page}/{total_pages}",
        'model_not_found': "Model topilmadi",
        'choose_color': "Telefon rangini tanlang:",
        'add_to_cart': "🛍️ Savatga",
        'choose_qty': "Mahsulot sonini tanlang:",
        'added_to_cart': "Savatga qo'shildi: {name} ({color}) x{qty}",
        'added_to_cart_short': "Savatga qo'shildi",
        'cart_empty': "Savat bo'sh.",
        'your_order': "Sizning buyurtmangiz:",
        'total': "Jami:",
        'remove_item': "❌ O'chirish",
        'clear_cart': "🗑️ Savatni tozalash",
        'checkout_button': "✅ Buyurtma berish",
        'back_to_brands': "← Brendlar ro'yxatiga qaytish",
        'back_to_models': "← Modellarga qaytish",
        'remove_invalid_index': "Noto'g'ri indeks.",
        'item_not_found': "Mahsulot topilmadi.",
        'item_removed': "Mahsulot savatdan o'chirildi.",
        'cart_cleared': "Savat tozalandi.",
        'cart_empty_alert': "Savat bo'sh",
        'send_contact': "Buyurtma uchun kontaktni yuboring:",
        'contact_button': "Kontaktni yuborish",
        'no_active_order': "Faol buyurtma yo'q. /catalog yordamida mahsulot tanlang",
        'send_address': "Yetkazib berish manzilini bitta xabarda yuboring:",
        'order_received': "Rahmat! Buyurtmangiz qabul qilindi. Tez orada bog'lanamiz.",
        'admin_panel': "Admin panel:",
        'no_orders': "Buyurtma yo'q.",
        'delete_order': "🗑️ Buyurtmani o'chirish",
        'order_deleted': "Buyurtma o'chirildi.",
        'order_delete_not_found': "Buyurtma topilmadi.",
        'search_results': "Qidiruv natijalari:",
        'continue_shopping': "🔍 Xaridni davom ettirish",
        'proceed_checkout': "Buyurtma berish",
    }
}


def get_user_lang(user_id):
    return LANG_PREFS.get(user_id)


def t(key, user_id, default=None):
    lang = get_user_lang(user_id) or 'ru'
    return TRANSLATIONS.get(lang, {}).get(key, default or TRANSLATIONS['ru'].get(key, ''))


def brand_models(brand, page=1, per_page=10):
    items = list(CATALOG.get(brand, {}).items())
    total = len(items)
    start = (page - 1) * per_page
    return items[start:start + per_page], total


def format_cart(user_id):
    items = CARTS.get(user_id, [])
    if not items:
        return "Корзина пуста."
    text = ["Ваш заказ:" ]
    total = 0.0
    for idx, item in enumerate(items, 1):
        price = float(item['price'] or 0)
        qty = int(item['quantity'])
        color = item.get('color', '')
        color_text = f" ({color})" if color else ""
        text.append(f"{idx}. {item['name']}{color_text} x{qty} — {price}₽")
        total += price * qty
    text.append(f"Итого: {total}₽")
    return "\n".join(text)


def delete_cart_item(user_id, index):
    cart = CARTS.get(user_id, [])
    if 0 <= index < len(cart):
        return cart.pop(index)
    return None


def clear_cart(user_id):
    CARTS.pop(user_id, None)


def build_search_results(query):
    query = query.lower()
    seen = set()
    results = []
    for brand, items in CATALOG.items():
        if query in brand.lower() and brand not in seen:
            results.append((brand, None, f"Бренд: {brand}"))
            seen.add(brand)
        for mid, info in items.items():
            name = info.get("name", "")
            company = info.get("company", "")
            if query in name.lower() or query in company.lower():
                key = (brand, mid)
                if key not in seen:
                    results.append((brand, mid, f"{name} — {info.get('price')}") )
                    seen.add(key)
    return results[:20]


def is_admin(user_id):
    return ADMIN_CHAT_ID and str(user_id) == str(ADMIN_CHAT_ID)


@dp.message_handler(commands=["start", "catalog"])
async def cmd_start(message: types.Message):
    # If user has not chosen a language yet, ask for it first
    if not get_user_lang(message.from_user.id):
        kb_lang = types.InlineKeyboardMarkup(row_width=2)
        kb_lang.add(types.InlineKeyboardButton(text="Русский", callback_data="lang|ru"))
        kb_lang.add(types.InlineKeyboardButton(text="O'zbekcha", callback_data="lang|uz"))
        await message.answer("Выберите язык / Tilni tanlang:", reply_markup=kb_lang)
        return

    kb = types.InlineKeyboardMarkup(row_width=2)
    for brand in first_n_brands(10):
        emoji = BRAND_EMOJIS.get(brand, "📦")
        kb.add(types.InlineKeyboardButton(text=f"{emoji} {brand}", callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton(text=f"🛒 {t('cart_command', message.from_user.id)}", callback_data="cart|"))
    if is_admin(message.from_user.id):
        kb.add(types.InlineKeyboardButton(text="🔑 Админка", callback_data="admin|panel"))
    await message.answer(
        t('choose_brand', message.from_user.id),
        reply_markup=kb,
    )



@dp.callback_query_handler(lambda c: c.data and c.data.startswith("lang|"))
async def on_language_select(cb: types.CallbackQuery):
    _, code = cb.data.split("|", 1)
    if code not in ("ru", "uz"):
        await cb.answer("Неверный язык.", show_alert=True)
        return
    LANG_PREFS[cb.from_user.id] = code
    await cb.answer("Язык выбран.")
    # Show main menu in selected language
    try:
        await cmd_start(cb.message)
    except Exception:
        await cb.message.answer(t('choose_brand', cb.from_user.id))
    


@dp.message_handler(commands=["search"])
async def cmd_search(message: types.Message):
    SEARCH_STATE.add(message.from_user.id)
    await message.answer(t('search_prompt', message.from_user.id))


@dp.message_handler(lambda message: message.from_user.id in SEARCH_STATE)
async def on_search_text(message: types.Message):
    SEARCH_STATE.discard(message.from_user.id)
    query = message.text.strip()
    if not query:
        await message.answer("Введите текст запроса для поиска.")
        return
    results = build_search_results(query)
    if not results:
        await message.answer("По запросу ничего не найдено.")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for brand, mid, title in results[:10]:
        if mid is None:
            kb.add(types.InlineKeyboardButton(text=title, callback_data=f"brand|{brand}"))
        else:
            kb.add(types.InlineKeyboardButton(text=title, callback_data=f"model|{brand}|{mid}"))
    await message.answer("Результаты поиска:", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("brand|"))
async def on_brand(cb: types.CallbackQuery):
    parts = cb.data.split("|")
    brand = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 1
    models, total = brand_models(brand, page)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for mid, info in models:
        price = info.get("price")
        kb.add(types.InlineKeyboardButton(text=f"{info.get('name')} — {price}", callback_data=f"model|{brand}|{mid}"))

    total_pages = ((total - 1) // 10) + 1 if total else 1
    
    # Кнопки навигации по страницам в одну строку
    nav_row = []
    if page > 1:
        nav_row.append(types.InlineKeyboardButton("<<", callback_data=f"brand|{brand}|{page - 1}"))
    nav_row.append(types.InlineKeyboardButton("🛒", callback_data="cart|"))
    if page < total_pages:
        nav_row.append(types.InlineKeyboardButton(">>", callback_data=f"brand|{brand}|{page + 1}"))
    
    if nav_row:
        kb.row(*nav_row)
    
    # Кнопка "Назад к списку брендов"
    kb.add(types.InlineKeyboardButton("← Назад к брендам", callback_data="start|"))
    
    text = f"Модели бренда {brand}: страница {page}/{total_pages}"
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("model|"))
async def on_model(cb: types.CallbackQuery):
    _, brand, mid = cb.data.split("|", 2)
    info = CATALOG.get(brand, {}).get(mid) or CATALOG.get(brand, {}).get(str(mid))
    if not info:
        await cb.answer("Модель не найдена", show_alert=True)
        return
    caption = (
        f"{info.get('name')}\n"
        f"Бренд: {info.get('company')}\n"
        f"Цена: {info.get('price')}\n"
        f"Память: {info.get('memory')} | RAM: {info.get('RAM')}\n"
        f"Выберите цвет телефона ниже:" 
    )
    kb = types.InlineKeyboardMarkup(row_width=3)
    for color in AVAILABLE_COLORS:
        emoji = "🔴" if color == "Красный" else "⚪" if color == "Белый" else "⚫"
        kb.add(types.InlineKeyboardButton(f"{emoji} {color}", callback_data=f"color|{brand}|{mid}|{color}"))
    kb.row(
        types.InlineKeyboardButton("← Назад к моделям", callback_data=f"brand|{brand}"),
        types.InlineKeyboardButton("← Назад к брендам", callback_data="start|"),
    )
    img = info.get("img_url")
    try:
        if img:
            await bot.send_photo(cb.from_user.id, img, caption=caption, reply_markup=kb)
            await cb.answer()
            await cb.message.delete()
        else:
            await cb.message.edit_text(caption, reply_markup=kb)
            await cb.answer()
    except Exception:
        await cb.message.edit_text(caption, reply_markup=kb)
        await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("color|"))
async def on_color(cb: types.CallbackQuery):
    _, brand, mid, color = cb.data.split("|", 3)
    info = CATALOG.get(brand, {}).get(mid) or CATALOG.get(brand, {}).get(str(mid))
    if not info:
        await cb.answer("Модель не найдена", show_alert=True)
        return
    caption = (
        f"{info.get('name')}\n"
        f"Бренд: {info.get('company')}\n"
        f"Цена: {info.get('price')}\n"
        f"Цвет: {color}\n"
        f"Память: {info.get('memory')} | RAM: {info.get('RAM')}"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🛍️ В корзину", callback_data=f"buy|{brand}|{mid}|{color}"))
    kb.add(types.InlineKeyboardButton("← Назад к моделям", callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton("← Назад к брендам", callback_data="start|"))
    kb.add(types.InlineKeyboardButton("🛒", callback_data="cart|"))
    img = info.get("img_url")
    try:
        if img:
            await bot.send_photo(cb.from_user.id, img, caption=caption, reply_markup=kb)
            await cb.answer()
            await cb.message.delete()
        else:
            await cb.message.edit_text(caption, reply_markup=kb)
            await cb.answer()
    except Exception:
        await cb.message.edit_text(caption, reply_markup=kb)
        await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("buy|"))
async def on_buy(cb: types.CallbackQuery):
    _, brand, mid, color = cb.data.split("|", 3)
    kb = types.InlineKeyboardMarkup(row_width=5)
    for n in range(1, 6):
        kb.add(types.InlineKeyboardButton(text=str(n), callback_data=f"qty|{brand}|{mid}|{color}|{n}"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data=f"brand|{brand}"))
    await cb.message.answer("Выберите количество товара:", reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("qty|"))
async def on_quantity(cb: types.CallbackQuery):
    _, brand, mid, color, qty = cb.data.split("|", 4)
    info = CATALOG.get(brand, {}).get(mid) or CATALOG.get(brand, {}).get(str(mid))
    if not info:
        await cb.answer("Модель не найдена", show_alert=True)
        return
    user_id = cb.from_user.id
    cart = CARTS.setdefault(user_id, [])
    item = {
        "brand": brand,
        "model_id": mid,
        "color": color,
        "name": info.get("name"),
        "price": info.get("price"),
        "quantity": int(qty),
    }
    cart.append(item)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout|"))
    kb.add(types.InlineKeyboardButton("🔍 Продолжить покупки", callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton("🛒", callback_data="cart|"))
    await cb.message.answer(f"Добавлено в корзину: {info.get('name')} ({color}) x{qty}", reply_markup=kb)
    await cb.answer("Добавлено в корзину")


@dp.callback_query_handler(lambda c: c.data and c.data == "cart|")
async def on_cart(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    cart = CARTS.get(user_id, [])
    kb = types.InlineKeyboardMarkup(row_width=1)
    if cart:
        for idx, item in enumerate(cart, 1):
            kb.add(types.InlineKeyboardButton(f"❌ Удалить {idx}", callback_data=f"remove|{idx - 1}"))
        kb.add(types.InlineKeyboardButton("🗑️ Очистить корзину", callback_data="clear_cart|"))
        kb.add(types.InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout|"))
    kb.add(types.InlineKeyboardButton("← Назад к брендам", callback_data="start|"))
    await cb.message.answer(format_cart(user_id), reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("remove|"))
async def on_remove_item(cb: types.CallbackQuery):
    _, index = cb.data.split("|", 1)
    user_id = cb.from_user.id
    try:
        index = int(index)
    except ValueError:
        await cb.answer("Неверный индекс товара.", show_alert=True)
        return
    item = delete_cart_item(user_id, index)
    if not item:
        await cb.answer("Товар не найден.", show_alert=True)
        return
    await cb.answer("Товар удалён из корзины.")
    await on_cart(cb)


@dp.callback_query_handler(lambda c: c.data and c.data == "clear_cart|")
async def on_clear_cart(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    clear_cart(user_id)
    await cb.answer("Корзина очищена.")
    await on_cart(cb)


@dp.callback_query_handler(lambda c: c.data and c.data == "checkout|")
async def on_checkout(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    if not CARTS.get(user_id):
        await cb.answer("Корзина пуста", show_alert=True)
        return
    PENDING[user_id] = {"step": "contact"}
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Отправить контакт", request_contact=True))
    await cb.message.answer("Отправьте контакт для оформления заказа:", reply_markup=kb)
    await cb.answer()


@dp.message_handler(commands=["cart"])
async def cmd_cart(message: types.Message):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Оформить заказ"))
    await message.answer(format_cart(message.from_user.id), reply_markup=kb)


@dp.message_handler(content_types=types.ContentType.CONTACT)
async def contact_handler(message: types.Message):
    user_id = message.from_user.id
    pending = PENDING.get(user_id)
    if not pending or pending.get("step") != "contact":
        await message.reply("Нет активного заказа. Выберите товар командой /catalog")
        return
    pending["phone"] = message.contact.phone_number
    pending["user_name"] = message.contact.first_name
    pending["step"] = "address"
    await message.answer("Отправьте адрес доставки одним сообщением:", reply_markup=types.ReplyKeyboardRemove())


@dp.message_handler(lambda message: message.from_user.id in PENDING and PENDING[message.from_user.id].get("step") == "address", content_types=types.ContentType.TEXT)
async def address_handler(message: types.Message):
    user_id = message.from_user.id
    pending = PENDING.get(user_id)
    if not pending:
        return
    address = message.text.strip()
    cart = CARTS.get(user_id, [])
    if not cart:
        await message.reply("Ваша корзина пуста. Добавьте товар перед оформлением.")
        return
    total = sum(float(item['price'] or 0) * int(item['quantity']) for item in cart)
    save_order(user_id, pending.get("user_name", message.from_user.first_name), pending.get("phone"), address, cart, total)
    CARTS.pop(user_id, None)
    PENDING.pop(user_id, None)
    await message.answer("Спасибо! Ваш заказ получен. Мы свяжемся с вами в ближайшее время.")
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(ADMIN_CHAT_ID, f"Новый заказ:\n{format_order({'id': 'new', 'user_name': pending.get('user_name'), 'user_id': user_id, 'phone': pending.get('phone'), 'address': address, 'items': json.dumps(cart, ensure_ascii=False), 'status':'new', 'created_at': int(time.time())})}")
        except Exception:
            pass


@dp.message_handler(commands=["admin"])
async def cmd_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещён")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📦 Просмотреть заказы", callback_data="admin|orders"))
    kb.add(types.InlineKeyboardButton("← Назад", callback_data="start|"))
    await message.answer("Админ-панель:", reply_markup=kb)


@dp.message_handler(commands=["orders"])
async def admin_orders(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    orders = get_last_orders_from_json(10)
    if not orders:
        await message.answer("Нет заказов.")
        return
    for order in orders:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🗑️ Удалить заказ", callback_data=f"admin|delete|{order['id']}"))
        kb.add(types.InlineKeyboardButton("← Назад", callback_data="admin|panel"))
        await message.answer(format_order(order), reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("admin|delete|"))
async def admin_delete_order(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Доступ запрещён", show_alert=True)
        return
    _, _, order_id = cb.data.split("|", 2)
    try:
        order_id = int(order_id)
    except ValueError:
        await cb.answer("Неверный ID заказа.", show_alert=True)
        return
    deleted = delete_order(order_id)
    deleted_json = delete_order_from_json(order_id)
    if deleted or deleted_json:
        await cb.answer("Заказ удалён.")
        await cb.message.answer(f"Заказ #{order_id} удалён.")
    else:
        await cb.answer("Заказ не найден.", show_alert=True)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("admin|"))
async def admin_panel(cb: types.CallbackQuery):
    if not is_admin(cb.from_user.id):
        await cb.answer("Доступ запрещён", show_alert=True)
        return
    action = cb.data.split("|", 1)[1]
    if action == "panel":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("📦 Просмотреть заказы", callback_data="admin|orders"))
        kb.add(types.InlineKeyboardButton("← Назад", callback_data="start|"))
        await cb.message.answer("Админ-панель:", reply_markup=kb)
        await cb.answer()
    elif action == "orders":
        orders = get_last_orders_from_json(10)
        if not orders:
            await cb.message.answer("Нет заказов.")
            await cb.answer()
            return
        for order in orders:
            await cb.message.answer(format_order(order))
        await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("start|"))
async def go_start(cb: types.CallbackQuery):
    await cmd_start(cb.message)
    await cb.answer()


async def on_startup(dispatcher):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL + WEBHOOK_PATH)


async def on_shutdown(dispatcher):
    if WEBHOOK_URL:
        await bot.delete_webhook()


if __name__ == "__main__":
    if WEBHOOK_URL:
        executor.start_webhook(
            dispatcher=dp,
            webhook_path=WEBHOOK_PATH,
            on_startup=on_startup,
            on_shutdown=on_shutdown,
            skip_updates=True,
            host=WEBAPP_HOST,
            port=WEBAPP_PORT,
        )
    else:
        executor.start_polling(dp, skip_updates=True)
