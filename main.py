import os
import json
import time
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, executor

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

with open(DB_PATH, "r", encoding="utf-8") as f:
    CATALOG = json.load(f)

PENDING = {}  # user_id -> order flow state
SEARCH_STATE = set()
CARTS = {}  # user_id -> list of items in cart

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


def save_order(user_id, user_name, phone, address, items, total):
    cursor.execute(
        "INSERT INTO orders (user_id, user_name, phone, address, items, total, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, user_name, phone, address, json.dumps(items, ensure_ascii=False), total, "new", int(time.time())),
    )
    conn.commit()


def get_last_orders(limit=10):
    cursor.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    return [dict(row) for row in rows]


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
        text.append(f"{idx}. {item['name']} x{qty} — {price}₽")
        total += price * qty
    text.append(f"Итого: {total}₽")
    return "\n".join(text)


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


@dp.message_handler(commands=["start", "catalog"])
async def cmd_start(message: types.Message):
    kb = types.InlineKeyboardMarkup()
    for brand in first_n_brands(10):
        kb.add(types.InlineKeyboardButton(text=brand, callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton(text="Корзина", callback_data="cart|"))
    await message.answer(
        "Выберите бренд или используйте /search для поиска телефона. \n" 
        "Команда /cart показывает корзину.",
        reply_markup=kb,
    )


@dp.message_handler(commands=["search"])
async def cmd_search(message: types.Message):
    SEARCH_STATE.add(message.from_user.id)
    await message.answer("Введите бренд или модель для поиска:")


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
    if page > 1:
        kb.add(types.InlineKeyboardButton("◀ Назад", callback_data=f"brand|{brand}|{page - 1}"))
    if page < total_pages:
        kb.add(types.InlineKeyboardButton("Вперед ▶", callback_data=f"brand|{brand}|{page + 1}"))

    kb.add(types.InlineKeyboardButton("Назад", callback_data="start|"))
    kb.add(types.InlineKeyboardButton("Корзина", callback_data="cart|"))
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
        f"Цвет: {info.get('color')}"
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("Купить", callback_data=f"buy|{brand}|{mid}"))
    kb.add(types.InlineKeyboardButton("Назад к моделям бренда", callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton("Назад к списку брендов", callback_data="start|"))
    kb.add(types.InlineKeyboardButton("Корзина", callback_data="cart|"))
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
    _, brand, mid = cb.data.split("|", 2)
    kb = types.InlineKeyboardMarkup(row_width=5)
    for n in range(1, 6):
        kb.add(types.InlineKeyboardButton(text=str(n), callback_data=f"qty|{brand}|{mid}|{n}"))
    kb.add(types.InlineKeyboardButton("Отмена", callback_data=f"brand|{brand}"))
    await cb.message.answer("Выберите количество товара:", reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("qty|"))
async def on_quantity(cb: types.CallbackQuery):
    _, brand, mid, qty = cb.data.split("|", 3)
    info = CATALOG.get(brand, {}).get(mid) or CATALOG.get(brand, {}).get(str(mid))
    if not info:
        await cb.answer("Модель не найдена", show_alert=True)
        return
    user_id = cb.from_user.id
    cart = CARTS.setdefault(user_id, [])
    item = {
        "brand": brand,
        "model_id": mid,
        "name": info.get("name"),
        "price": info.get("price"),
        "quantity": int(qty),
    }
    cart.append(item)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("Оформить заказ", callback_data="checkout|"))
    kb.add(types.InlineKeyboardButton("Продолжить покупки", callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton("Посмотреть корзину", callback_data="cart|"))
    await cb.message.answer(f"Добавлено в корзину: {info.get('name')} x{qty}", reply_markup=kb)
    await cb.answer("Добавлено в корзину")


@dp.callback_query_handler(lambda c: c.data and c.data == "cart|")
async def on_cart(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("Оформить заказ", callback_data="checkout|"))
    kb.add(types.InlineKeyboardButton("Назад к списку брендов", callback_data="start|"))
    await cb.message.answer(format_cart(user_id), reply_markup=kb)
    await cb.answer()


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


@dp.message_handler(commands=["orders"])
async def admin_orders(message: types.Message):
    if not ADMIN_CHAT_ID or str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    orders = get_last_orders(10)
    if not orders:
        await message.answer("Нет заказов.")
        return
    for order in orders:
        await message.answer(format_order(order))


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
