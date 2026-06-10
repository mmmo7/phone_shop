import os
import json
import time
import logging
from aiogram import Bot, Dispatcher, types, executor

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN") or "YOUR_BOT_TOKEN_HERE"
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")

bot = Bot(token=TOKEN)
dp = Dispatcher(bot)

# load catalog
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "db.json")

with open(DB_PATH, "r", encoding="utf-8") as f:
    CATALOG = json.load(f)

PENDING = {}  # user_id -> (brand, model_id)

def first_n_brands(n=10):
    return list(CATALOG.keys())[:n]

def brand_models(brand, n=10):
    items = list(CATALOG.get(brand, {}).items())
    return items[:n]


@dp.message_handler(commands=["start", "catalog"])
async def cmd_start(message: types.Message):
    kb = types.InlineKeyboardMarkup()
    for brand in first_n_brands(10):
        kb.add(types.InlineKeyboardButton(text=brand, callback_data=f"brand|{brand}"))
    await message.answer("Выберите бренд:", reply_markup=kb)


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("brand|"))
async def on_brand(cb: types.CallbackQuery):
    _, brand = cb.data.split("|", 1)
    models = brand_models(brand, 10)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for mid, info in models:
        price = info.get("price")
        text = f"{info.get('name')} — {price}"
        kb.add(types.InlineKeyboardButton(text=text, callback_data=f"model|{brand}|{mid}"))
    kb.add(types.InlineKeyboardButton("Назад", callback_data="start|"))
    try:
        await cb.message.edit_text(f"Модели бренда {brand}:", reply_markup=kb)
    except Exception:
        await cb.message.answer(f"Модели бренда {brand}:", reply_markup=kb)
    await cb.answer()


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("model|"))
async def on_model(cb: types.CallbackQuery):
    _, brand, mid = cb.data.split("|", 2)
    info = CATALOG.get(brand, {}).get(mid) or CATALOG.get(brand, {}).get(str(mid))
    if not info:
        await cb.answer("Модель не найдена", show_alert=True)
        return
    caption = (f"{info.get('name')}\nБренд: {info.get('company')}\n"
               f"Цена: {info.get('price')}\nПамять: {info.get('memory')} | RAM: {info.get('RAM')}\nЦвет: {info.get('color')}")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("Купить", callback_data=f"buy|{brand}|{mid}"))
    kb.add(types.InlineKeyboardButton("Назад к моделям бренда", callback_data=f"brand|{brand}"))
    kb.add(types.InlineKeyboardButton("Назад к списку брендов", callback_data="start|"))
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
    PENDING[cb.from_user.id] = (brand, mid)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Отправить контакт", request_contact=True))
    await cb.message.answer("Нажмите кнопку, чтобы отправить контакт для оформления заказа:", reply_markup=kb)
    await cb.answer()


@dp.message_handler(content_types=types.ContentType.CONTACT)
async def contact_handler(message: types.Message):
    user_id = message.from_user.id
    if user_id not in PENDING:
        await message.reply("Нет активного заказа. Выберите товар командой /catalog")
        return
    brand, mid = PENDING.pop(user_id)
    info = CATALOG.get(brand, {}).get(str(mid)) or CATALOG.get(brand, {}).get(mid)
    order = {
        "user_id": user_id,
        "name": message.contact.first_name,
        "phone": message.contact.phone_number,
        "brand": brand,
        "model_id": mid,
        "product": info,
        "ts": int(time.time())
    }
    try:
        orders = []
        orders_path = os.path.join(BASE_DIR, "orders.json")
        if os.path.exists(orders_path):
            with open(orders_path, "r", encoding="utf-8") as f:
                orders = json.load(f)
        orders.append(order)
        with open(orders_path, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
    except Exception:
        await message.reply("Ошибка сохранения заказа.")
        return

    await message.reply("Спасибо! Ваш заказ получен. Мы свяжемся с вами в ближайшее время.", reply_markup=types.ReplyKeyboardRemove())
    if ADMIN_CHAT_ID:
        try:
            await bot.send_message(ADMIN_CHAT_ID, f"Новый заказ:\n{order}")
        except Exception:
            pass


@dp.callback_query_handler(lambda c: c.data and c.data.startswith("start|"))
async def go_start(cb: types.CallbackQuery):
    await cmd_start(cb.message)
    await cb.answer()


if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
