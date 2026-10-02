"""Import the product photos into the Arizon Yadak shop through its own admin panel.

  SITE_CODE=xxxx python3 site_import.py --base https://... --phone 09... [--dry-run] [--limit N] [--stock N]

Works like a person in the browser: signs in on /login, creates missing categories and car models,
then adds each product on /admin/products/new with its photos in order. Every result is written to a
journal, and products already created (same SKU) are skipped, so the script can be re-run safely.
"""
import argparse, json, os, re, sys, time, unicodedata
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
FA = '۰۱۲۳۴۵۶۷۸۹'

def norm(t):
    t = unicodedata.normalize('NFKC', t or '').replace('‌', ' ').replace('ي', 'ی').replace('ك', 'ک')
    t = re.sub('[۰-۹]', lambda m: str(FA.index(m.group())), t)
    return re.sub(r'\s+', ' ', t.replace('↳', '')).strip()

class Importer:
    def __init__(self, base, phone, code, journal, dry, stock, log):
        self.base, self.phone, self.code = base.rstrip('/'), phone, code
        self.journal_path, self.dry, self.stock, self.log = journal, dry, stock, log
        self.done = {}
        if os.path.exists(journal):
            for line in open(journal):
                r = json.loads(line)
                if r.get('status') in ('created', 'exists'): self.done[r['sku']] = r
        self.logins = 0

    def j(self, rec):
        rec['ts'] = time.strftime('%H:%M:%S')
        with open(self.journal_path, 'a') as f: f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    # ── session ──
    def login(self, page):
        if self.logins >= 3:
            raise SystemExit('login failed 3 times; stopping so the account is not locked by the rate limit')
        self.logins += 1
        page.goto(f'{self.base}/login?next=/admin', wait_until='domcontentloaded')
        page.locator('input[name=phone]').fill(self.phone)
        page.locator('form:has(input[name=phone]) button[type=submit]').click()
        first = page.get_by_label('رقم 1')
        first.wait_for(timeout=30000)
        first.fill(self.code)
        # a full-length code submits itself; a shorter staff code needs the button
        page.wait_for_timeout(1500)
        btn = page.get_by_role('button', name='تأیید')
        if '/login' in page.url and btn.count() and btn.is_enabled():
            btn.click()
        try:
            page.wait_for_url(lambda u: urlparse(u).path.startswith('/admin'), timeout=40000)
        except PWTimeout:
            err = page.locator('[role=alert]').all_inner_texts()
            raise SystemExit(f'login did not reach /admin: {err}')
        self.log(f'signed in ({self.logins})')

    def goto(self, page, path, ready=None):
        for attempt in range(3):
            page.goto(f'{self.base}{path}', wait_until='domcontentloaded')
            if '/login' in page.url:
                self.login(page)
                page.goto(f'{self.base}{path}', wait_until='domcontentloaded')
            if not ready: return
            try:
                # wait until React has hydrated the element, so clicks and submits are handled by the app
                page.wait_for_function(
                    """sel => { const el = document.querySelector(sel);
                       return !!el && Object.keys(el).some(k => k.startsWith('__reactProps')); }""",
                    arg=ready, timeout=45000)
                return
            except PWTimeout:
                self.log(f'page not ready ({path}), retrying')
        raise RuntimeError(f'page never became ready: {path}')

    # ── what the site already has ──
    def read_form_lists(self, page):
        self.goto(page, '/admin/products/new', ready='form:has(#f-cat)')
        opts = page.locator('#f-cat option').evaluate_all('os => os.map(o => [o.value, o.textContent])')
        tree, cur = {}, None
        for v, label in opts:
            if '↳' in label:
                tree[cur]['subs'][norm(label)] = v
            else:
                cur = norm(label); tree[cur] = dict(id=v, subs={})
        cars = page.locator('input[name=fitments]').evaluate_all(
            'xs => xs.map(x => [x.value, x.closest("label").textContent])')
        return tree, {norm(n): v for v, n in cars}

    # ── categories ──
    def create_category(self, page, name, icon, desc, parent_id=None):
        self.goto(page, '/admin/categories', ready='h1 + button.btn-primary')
        for attempt in range(3):
            page.get_by_role('button', name='افزودن دسته‌بندی').first.click()
            try:
                page.locator('#cat-name').wait_for(timeout=10000); break
            except PWTimeout:
                page.reload(wait_until='domcontentloaded'); page.wait_for_timeout(3000)
        form = page.locator('form:has(#cat-name)')
        form.locator('#cat-name').fill(name)
        if parent_id: form.locator('#cat-parent').select_option(parent_id)
        form.locator(f'label:has(input[name=icon][value="{icon}"])').click()
        if desc: form.locator('#cat-desc').fill(desc)
        form.locator('button[type=submit]').click()
        page.get_by_text('دسته‌بندی اضافه شد.').wait_for(timeout=30000)
        self.log(f'category created: {name}' + (' (sub)' if parent_id else ''))

    def ensure_categories(self, page, tree_spec):
        tree, _ = self.read_form_lists(page)
        plan = []
        for main, m in tree_spec.items():
            if norm(main) not in tree: plan.append(('main', main, m['icon'], m['description'], None))
            for sub, desc in m['subs'].items():
                have = tree.get(norm(main), {}).get('subs', {})
                if norm(sub) not in have: plan.append(('sub', sub, m['icon'], desc, main))
        self.log(f'categories to create: {len(plan)}')
        for kind, name, icon, desc, parent in plan:
            self.log(f'  + {kind}: {name}' + (f'  ← {parent}' if parent else ''))
        if self.dry: return tree
        for kind, name, icon, desc, parent in plan:
            pid = None
            if parent:
                tree, _ = self.read_form_lists(page)
                pid = tree[norm(parent)]['id']
            self.create_category(page, name, icon, desc, pid)
        tree, _ = self.read_form_lists(page)
        return tree

    # ── car models ──
    def ensure_cars(self, page, cars_spec):
        _, cars = self.read_form_lists(page)
        missing = [c for c in cars_spec if norm(c['name']) not in cars]
        self.log(f'car models to create: {len(missing)} ' + '، '.join(c['name'] for c in missing))
        if self.dry or not missing: return cars
        page.get_by_role('button', name='مدیریت خودروها').click()
        dlg = page.get_by_role('dialog')
        for c in missing:
            dlg.get_by_placeholder('مثلاً: پژو', exact=True).fill(c['make'])
            dlg.get_by_placeholder('مثلاً: پژو ۲۰۶ تیپ ۵').fill(c['name'])
            dlg.get_by_role('button', name='افزودن').click()
            dlg.get_by_text(f'«{c["name"]}» به فهرست اضافه شد.').wait_for(timeout=30000)
            self.log(f'car created: {c["name"]}')
        _, cars = self.read_form_lists(page)
        return cars

    # ── products ──
    def add_product(self, page, it, cat_id, car_ids):
        self.goto(page, '/admin/products/new', ready='form:has(#f-cat)')
        f = page.locator('form:has(#f-cat)')
        f.locator('input[name=name]').fill(it['name'])
        f.locator('input[name=sku]').fill(it['sku'])
        f.locator('#f-cat').select_option(cat_id)
        f.locator('#f-desc').fill(it['description'])
        f.locator('#f-specs').fill(it['specs'])
        f.locator('input[name=basePrice]').fill(str(it.get('price', 1000)))
        f.locator('input[name=stock]').fill(str(self.stock))
        for cid in car_ids:
            f.locator(f'input[name=fitments][value="{cid}"]').check()
        f.locator('#f-images').set_input_files(it['images'])
        f.get_by_role('button', name='ذخیره محصول').click()
        alerts = page.locator('form:has(#f-cat) [role=alert], form:has(#f-cat) .field-error')
        end = time.time() + 180
        while time.time() < end:
            if 'saved=' in page.url:
                return True, page.url.split('saved=')[-1]
            if alerts.count() and not f.get_by_role('button', name='ذخیره محصول').is_disabled():
                return False, ' | '.join(t.strip() for t in alerts.all_inner_texts())
            page.wait_for_timeout(300)
        return False, 'timeout'

    def run(self, data, limit=None, only=None):
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(locale='fa-IR', viewport={'width': 1400, 'height': 1000})
            page = ctx.new_page()
            page.set_default_timeout(60000)
            self.login(page)
            tree = self.ensure_categories(page, data['tree'])
            cars = self.ensure_cars(page, data['cars'])
            items = [it for it in data['items'] if (only is None or it['id'] in only)]
            todo = [it for it in items if it['sku'] not in self.done]
            self.log(f'products: {len(items)} total, {len(items) - len(todo)} already done, {len(todo)} to add')
            if limit: todo = todo[:limit]
            if self.dry:
                for it in todo[:5]:
                    self.log(f"  {it['sku']} {it['name']} → {it['main']} / {it['sub']} | cars: {it['cars']} | {len(it['images'])} images")
                browser.close(); return
            ok = fail = 0
            for n, it in enumerate(todo, 1):
                main = tree[norm(it['main'])]
                cat_id = main['subs'][norm(it['sub'])] if it['sub'] else main['id']
                car_ids = [cars[norm(c)] for c in it['cars'] if norm(c) in cars]
                t = time.time()
                try:
                    good, info = self.add_product(page, it, cat_id, car_ids)
                except Exception as e:  # keep going; the journal records it
                    good, info = False, f'{type(e).__name__}: {str(e)[:200]}'
                if good:
                    ok += 1; self.j(dict(sku=it['sku'], id=it['id'], name=it['name'], status='created', productId=info))
                elif 'کد کالا برای محصول دیگری' in info:
                    ok += 1; self.j(dict(sku=it['sku'], id=it['id'], name=it['name'], status='exists'))
                else:
                    fail += 1; self.j(dict(sku=it['sku'], id=it['id'], name=it['name'], status='error', error=info))
                self.log(f'[{n}/{len(todo)}] {"OK " if good else "ERR"} {it["sku"]} {it["name"]} ({time.time() - t:.1f}s) {"" if good else info}')
            self.log(f'finished: {ok} ok, {fail} errors')
            browser.close()

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--phone', required=True)
    ap.add_argument('--journal', default=os.path.join(HERE, 'import_journal.jsonl'))
    ap.add_argument('--data', default=os.path.join(HERE, 'products.json'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int)
    ap.add_argument('--only', help='comma separated source ids')
    ap.add_argument('--stock', type=int, default=0)
    a = ap.parse_args()
    code = os.environ.get('SITE_CODE') or sys.exit('set SITE_CODE')
    data = json.load(open(a.data))
    for it in data['items']:
        it['images'] = [p if os.path.isabs(p) else os.path.join(REPO, p) for p in it['images']]
    def log(m): print(m, flush=True)
    only = set(map(int, a.only.split(','))) if a.only else None
    Importer(a.base, a.phone, code, a.journal, a.dry_run, a.stock, log).run(data, a.limit, only)
