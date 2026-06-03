# Rubika Panel (Telegram-controlled)

پنل تلگرامی برای مدیریت اکانت‌های روبیکا و ارسال محتوای مشترک به **مخاطبین + گروه‌ها**.

## نصب روی سرور

```bash
cd /opt
git clone https://github.com/Jack6566/rubika-panel.git
cd rubika-panel
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## ساخت فایل .env

```bash
nano .env
```

```
API_ID=2040
API_HASH=b18441a1ff607e10a989891a5462e627
BOT_TOKEN=<توکن ربات تلگرام>
OWNER_ID=5818420346
ADMIN_IDS=
LOG_GROUP_ID=-1001926302534
SEND_DELAY=0.5
```

## افزودن اکانت روبیکا

افزودن اکانت **از داخل ربات تلگرام** انجام می‌شود: در پنل روی «➕ افزودن اکانت»
بزن، شماره را بفرست، و کدی که در اپ روبیکا آمد را در همان چت تلگرام وارد کن
(رمز دومرحله‌ای هم اگر داشتی).

> اگر روزی ورود از داخل تلگرام به مشکل خورد، یک اسکریپت پشتیبان هم هست که همان
> کار را روی کنسول سرور انجام می‌دهد:
> ```bash
> cd /opt/rubika-panel && source venv/bin/activate
> python login.py
> systemctl restart rubika-panel
> ```

## اجرا (تست)

```bash
python bot.py
```

## همیشه‌روشن با systemd

```bash
sudo cp deploy/rubika-panel.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rubika-panel
systemctl status rubika-panel
```

## نکته درباره‌ی rubpy

کتابخانه‌ی `rubpy` غیررسمی است؛ اگر نام متدی روی نسخه‌ی نصب‌شده فرق داشت، فقط فایل
`rubika_client.py` را اصلاح کنید (همه‌ی فراخوانی‌های روبیکا در همان‌جا ایزوله شده‌اند).

برای دیدن متدهای نسخه‌ی نصب‌شده:

```bash
python -c "import rubpy; print(rubpy.__version__)"
python -c "from rubpy import Client; print([m for m in dir(Client) if not m.startswith('_')])"
```
