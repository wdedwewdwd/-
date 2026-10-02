# ورود خودکار محصولات به سایت آریزون یدک

این ابزار عکس‌های پوشه‌ی `تصاویر محصولات` را از طریق پنل مدیریت خود سایت، به‌صورت محصول ثبت می‌کند؛ دقیقاً همان کاری که مدیر در مرورگر انجام می‌دهد.

- `products.json`: اطلاعات ۵۹۱ محصول (نام، دسته، خودروهای سازگار، توضیحات، مشخصات فنی، کد کالا و ترتیب عکس‌ها) و درخت دسته‌بندی‌ها و فهرست خودروها.
- `site_import.py`: وارد پنل می‌شود، دسته‌ها و خودروهای جاافتاده را می‌سازد و محصولات را یکی‌یکی با عکس‌هایشان ثبت می‌کند. نتیجه‌ی هر محصول در `import_journal.jsonl` ثبت می‌شود و اجرای دوباره، محصولات ثبت‌شده را (با همان کد کالا) تکرار نمی‌کند.
- `site_catalog.py`: منطق ساخت `products.json` (متن توضیحات هر نوع قطعه، تشخیص خودرو از روی نام و ...).

اجرا (به Python و Playwright نیاز دارد):

```bash
SITE_CODE=<کد ورود مدیر> python3 tools/site-import/site_import.py \
  --base https://site-production-da14.up.railway.app --phone 09xxxxxxxxx --dry-run   # فقط نمایش برنامه
SITE_CODE=<کد ورود مدیر> python3 tools/site-import/site_import.py \
  --base https://site-production-da14.up.railway.app --phone 09xxxxxxxxx --stock 0     # ثبت واقعی
```
