# phone_shop Telegram bot

Простой пример Telegram-бота для просмотра каталога телефонов и оформления заказа.

Требования:

- Python 3.8+
- Установить зависимости: `pip install -r requirements.txt`

Запуск (PowerShell):

```powershell
$env:BOT_TOKEN="ваш_токен"
python main.py
```

Запуск (Windows):

```powershell
py -m pip install -r requirements.txt
$env:BOT_TOKEN="ваш_токен"
py main.py
```

Docker контейнерини ишга тушириш:

```powershell
docker build -t phone_shop .
docker run -e BOT_TOKEN="ваш_токен" -p 3000:3000 phone_shop
```

Файлы:

- `main.py` — основной код бота
- `db.json` — каталог товаров (уже в репозитории)
- `orders.json` — сохранённые заказы
# phone_shop
првиет