import json
import re
import sqlite3
from datetime import datetime
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "sales.db"
CATALOG_PATH = DATA_DIR / "catalog.json"

DATA_DIR.mkdir(exist_ok=True)

SEED_CATALOG = [
    {"id": "seed-1", "model": "Платье Evening Midi", "price": 12990, "sizes": ["XS", "S", "M", "L"], "colors": ["Черный", "Бежевый"]},
    {"id": "seed-2", "model": "Жакет Urban Tailor", "price": 15990, "sizes": ["S", "M", "L"], "colors": ["Серый", "Молочный"]},
    {"id": "seed-3", "model": "Брюки Straight Fit", "price": 8990, "sizes": ["XS", "S", "M", "L", "XL"], "colors": ["Темно-синий", "Черный"]},
    {"id": "seed-4", "model": "Рубашка Soft Cotton", "price": 7490, "sizes": ["S", "M", "L"], "colors": ["Белый", "Голубой"]},
]


def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sales (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            discount_percent REAL NOT NULL,
            note TEXT,
            photo_data_url TEXT,
            total_before REAL NOT NULL,
            total_after REAL NOT NULL,
            items_json TEXT NOT NULL
        )
        """
    )
    con.commit()
    con.close()


def ensure_catalog():
    if not CATALOG_PATH.exists():
        CATALOG_PATH.write_text(json.dumps(SEED_CATALOG, ensure_ascii=False, indent=2), encoding="utf-8")


def load_catalog():
    ensure_catalog()
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def save_catalog(catalog):
    CATALOG_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_price(raw):
    if raw is None:
        return None
    cleaned = re.sub(r"[^0-9,\.]", "", str(raw)).replace(",", ".")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _normalize_products(products):
    cleaned = []
    seen = set()
    for p in products:
        model = (p.get("model") or "").strip()
        price = _parse_price(p.get("price"))
        if not model:
            continue
        key = model.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"model": model, "price": float(price or 0)})
    return cleaned


def _extract_products_from_html(html):
    products = []

    for script_body in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.I | re.S):
        text = script_body.strip()
        try:
            data = json.loads(text)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if item.get("@type") == "Product" and item.get("name"):
                    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
                    products.append({"model": item.get("name", ""), "price": offers.get("price") or item.get("price")})
                for val in item.values():
                    if isinstance(val, (dict, list)):
                        stack.append(val)
            elif isinstance(item, list):
                stack.extend(item)

    card_matches = re.findall(r'<li[^>]*class="[^"]*product[^"]*"[^>]*>(.*?)</li>', html, flags=re.I | re.S)
    for card in card_matches:
        name_m = re.search(r'class="[^"]*woocommerce-loop-product__title[^"]*"[^>]*>(.*?)<', card, flags=re.I | re.S)
        if not name_m:
            name_m = re.search(r'<h2[^>]*>(.*?)<', card, flags=re.I | re.S)
        if not name_m:
            continue
        model = re.sub(r"<[^>]+>", "", name_m.group(1)).strip()
        price_m = re.search(r'class="[^"]*price[^"]*"[^>]*>(.*?)</', card, flags=re.I | re.S)
        raw_price = re.sub(r"<[^>]+>", "", price_m.group(1)) if price_m else ""
        products.append({"model": model, "price": raw_price})

    return _normalize_products(products)


def _extract_products_from_store_api(payload):
    products = []
    for item in payload:
        name = item.get("name")
        prices = item.get("prices") or {}
        regular = prices.get("price") or prices.get("regular_price") or item.get("price")
        # Woo Store API часто отдает цену в minor units
        if isinstance(regular, str) and regular.isdigit() and len(regular) > 3:
            cur_minor = prices.get("currency_minor_unit")
            if isinstance(cur_minor, int):
                regular = int(regular) / (10 ** cur_minor)
        products.append({"model": name, "price": regular})
    return _normalize_products(products)


def _fetch_json(url, headers, timeout=25):
    req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _scrape_from_wc_store_api(headers):
    all_products = []
    seen = set()
    for page in range(1, 21):
        url = f"https://unke.store/wp-json/wc/store/products?per_page=100&page={page}"
        payload = _fetch_json(url, headers)
        if not isinstance(payload, list) or not payload:
            break
        parsed = _extract_products_from_store_api(payload)
        added = 0
        for p in parsed:
            key = p["model"].lower()
            if key in seen:
                continue
            seen.add(key)
            all_products.append(p)
            added += 1
        if added == 0:
            break
    return all_products


def _scrape_from_catalog_pages(headers):
    all_products = []
    seen = set()
    for page in range(1, 31):
        page_url = "https://unke.store/catalog" if page == 1 else f"https://unke.store/catalog/page/{page}/"
        req = Request(page_url, headers=headers)
        with urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        parsed = _extract_products_from_html(html)
        added = 0
        for p in parsed:
            key = p["model"].lower()
            if key in seen:
                continue
            seen.add(key)
            all_products.append(p)
            added += 1
        if page > 1 and added == 0:
            break
    return all_products


def scrape_catalog():
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
        "Accept": "application/json,text/html,application/xhtml+xml",
    }

    errors = []
    all_products = []

    # 1) Основной способ: WooCommerce Store API (возвращает полный каталог по страницам)
    try:
        all_products = _scrape_from_wc_store_api(headers)
    except Exception as e:
        errors.append(f"store_api: {e}")

    # 2) Резервный способ: HTML пагинация /catalog/page/N/
    if len(all_products) < 12:
        try:
            all_products = _scrape_from_catalog_pages(headers)
        except Exception as e:
            errors.append(f"html_pages: {e}")

    if len(all_products) < 12:
        reason = "; ".join(errors) if errors else "недостаточно товаров"
        raise RuntimeError(f"Каталог не удалось распарсить полностью ({reason})")

    catalog = []
    for idx, p in enumerate(all_products, start=1):
        catalog.append({
            "id": f"scrape-{idx}",
            "model": p["model"],
            "price": p["price"],
            "sizes": ["XS", "S", "M", "L", "XL"],
            "colors": ["Не указан"],
        })

    save_catalog(catalog)
    return catalog




def _live_search_unke_site(query, headers):
    query = query.strip()
    if not query:
        return []

    # 1) WooCommerce Store API search
    try:
        payload = _fetch_json(f"https://unke.store/wp-json/wc/store/products?search={query}&per_page=20", headers)
        if isinstance(payload, list):
            results = []
            for item in payload:
                prices = item.get("prices") or {}
                raw_price = prices.get("price") or prices.get("regular_price") or item.get("price")
                if isinstance(raw_price, str) and raw_price.isdigit() and len(raw_price) > 3:
                    minor = prices.get("currency_minor_unit")
                    if isinstance(minor, int):
                        raw_price = int(raw_price) / (10 ** minor)
                images = item.get("images") or []
                image = images[0].get("src") if images and isinstance(images[0], dict) else ""
                results.append({
                    "id": item.get("id") or f"live-{len(results)+1}",
                    "model": item.get("name") or "",
                    "price": float(_parse_price(raw_price) or 0),
                    "image": image,
                    "description": re.sub(r"<[^>]+>", "", (item.get("short_description") or item.get("description") or "")).strip(),
                    "sizes": ["XS", "S", "M", "L", "XL"],
                    "colors": ["Не указан"],
                })
            normalized = []
            seen = set()
            for r in results:
                key = r["model"].strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                normalized.append(r)
            if normalized:
                return normalized
    except Exception:
        pass

    # 2) Fallback to local catalog search
    local = load_catalog()
    ql = query.lower()
    local = [x for x in local if ql in x.get("model", "").lower()][:20]
    return [{
        "id": x.get("id"),
        "model": x.get("model"),
        "price": float(x.get("price", 0)),
        "image": "",
        "description": "",
        "sizes": x.get("sizes") or ["Не указан"],
        "colors": x.get("colors") or ["Не указан"],
    } for x in local]

def add_custom_catalog_item(model, price, sizes=None, colors=None):
    catalog = load_catalog()
    model = model.strip()
    if not model:
        raise ValueError("model required")
    if any(item["model"].lower() == model.lower() for item in catalog):
        raise ValueError("item already exists")
    new_item = {
        "id": f"manual-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
        "model": model,
        "price": float(price or 0),
        "sizes": sizes or ["Не указан"],
        "colors": colors or ["Не указан"],
    }
    catalog.append(new_item)
    save_catalog(catalog)
    return new_item


def query_sales(date_value=None, search=""):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    sql = "SELECT * FROM sales"
    params = []
    where = []
    if date_value:
        where.append("substr(created_at,1,10)=?")
        params.append(date_value)
    if search:
        where.append("(note LIKE ? OR items_json LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like])
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    rows = cur.execute(sql, params).fetchall()
    con.close()
    result = []
    for row in rows:
        rec = dict(row)
        rec["items"] = json.loads(rec.pop("items_json"))
        result.append(rec)
    return result


def insert_sale(payload):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO sales (id, created_at, discount_percent, note, photo_data_url, total_before, total_after, items_json) VALUES (?,?,?,?,?,?,?,?)",
        (
            payload["id"],
            payload["created_at"],
            payload["discount_percent"],
            payload.get("note", ""),
            payload.get("photo_data_url", ""),
            payload["total_before"],
            payload["total_after"],
            json.dumps(payload["items"], ensure_ascii=False),
        ),
    )
    con.commit()
    con.close()


def update_sale(sale_id, payload):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        UPDATE sales SET created_at=?, discount_percent=?, note=?, photo_data_url=?, total_before=?, total_after=?, items_json=?
        WHERE id=?
        """,
        (
            payload["created_at"],
            payload["discount_percent"],
            payload.get("note", ""),
            payload.get("photo_data_url", ""),
            payload["total_before"],
            payload["total_after"],
            json.dumps(payload["items"], ensure_ascii=False),
            sale_id,
        ),
    )
    con.commit()
    con.close()


def calc_totals(items, discount_percent):
    total_before = sum(float(i.get("price", 0)) * float(i.get("quantity", 0)) for i in items)
    total_after = total_before * (1 - float(discount_percent or 0) / 100)
    return round(total_before, 2), round(total_after, 2)


def to_excel_xml(rows):
    header = """<?xml version=\"1.0\"?>\n<?mso-application progid=\"Excel.Sheet\"?>\n<Workbook xmlns=\"urn:schemas-microsoft-com:office:spreadsheet\" xmlns:ss=\"urn:schemas-microsoft-com:office:spreadsheet\"><Worksheet ss:Name=\"Продажи\"><Table>"""
    cols = ["ID", "Дата", "Товары", "Скидка %", "Итог", "Заметка"]
    hrow = "<Row>" + "".join([f"<Cell><Data ss:Type=\"String\">{c}</Data></Cell>" for c in cols]) + "</Row>"
    body = []
    for row in rows:
        item_text = "; ".join([f"{i['model']} x{i['quantity']} {i['size']} {i['color']}" for i in row["items"]])
        cells = [
            row["id"],
            row["created_at"],
            item_text,
            str(row["discount_percent"]),
            str(row["total_after"]),
            (row.get("note") or "").replace("&", "и"),
        ]
        body.append("<Row>" + "".join([f"<Cell><Data ss:Type=\"String\">{c}</Data></Cell>" for c in cells]) + "</Row>")
    tail = "</Table></Worksheet></Workbook>"
    return (header + hrow + "".join(body) + tail).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/catalog":
            query = parse_qs(parsed.query).get("q", [""])[0].lower().strip()
            limit_raw = parse_qs(parsed.query).get("limit", [""])[0].strip()
            catalog = load_catalog()
            if query:
                catalog = [c for c in catalog if query in c["model"].lower()]
            if limit_raw.isdigit() and int(limit_raw) > 0:
                catalog = catalog[-int(limit_raw):][::-1]
            self._json(200, {"items": catalog})
            return

        if parsed.path == "/api/catalog/site-search":
            query = parse_qs(parsed.query).get("q", [""])[0].strip()
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
                "Accept": "application/json,text/html,application/xhtml+xml",
            }
            items = _live_search_unke_site(query, headers)
            self._json(200, {"items": items})
            return

        if parsed.path == "/api/sales":
            params = parse_qs(parsed.query)
            date_v = params.get("date", [""])[0]
            search = params.get("search", [""])[0]
            rows = query_sales(date_v, search)
            total = round(sum(float(r["total_after"]) for r in rows), 2)
            self._json(200, {"items": rows, "day_total": total})
            return

        if parsed.path == "/api/sales/export.xlsx":
            rows = query_sales()
            data = to_excel_xml(rows)
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.ms-excel")
            self.send_header("Content-Disposition", "attachment; filename=sales-export.xls")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if parsed.path == "/" or parsed.path == "":
            file_path = PUBLIC_DIR / "index.html"
        else:
            file_path = PUBLIC_DIR / parsed.path.lstrip("/")

        if file_path.exists() and file_path.is_file():
            mime = "text/plain"
            if file_path.suffix == ".html":
                mime = "text/html; charset=utf-8"
            elif file_path.suffix == ".css":
                mime = "text/css; charset=utf-8"
            elif file_path.suffix == ".js":
                mime = "application/javascript; charset=utf-8"
            data = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self._json(404, {"error": "Not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/catalog/refresh":
            try:
                catalog = scrape_catalog()
                self._json(200, {"items": catalog, "source": "scraped", "count": len(catalog)})
            except Exception as e:
                catalog = load_catalog()
                self._json(200, {"items": catalog, "source": "fallback", "count": len(catalog), "warning": str(e)})
            return

        if parsed.path == "/api/catalog/manual":
            body = self._read_json()
            try:
                item = add_custom_catalog_item(
                    model=body.get("model", ""),
                    price=body.get("price", 0),
                    sizes=body.get("sizes") or ["Не указан"],
                    colors=body.get("colors") or ["Не указан"],
                )
                self._json(201, {"item": item})
            except ValueError as e:
                self._json(400, {"error": str(e)})
            return

        if parsed.path == "/api/sales":
            body = self._read_json()
            sale_id = body.get("id") or datetime.now().strftime("sale-%Y%m%d%H%M%S%f")
            items = body.get("items", [])
            discount = float(body.get("discount_percent", 0))
            total_before, total_after = calc_totals(items, discount)
            payload = {
                "id": sale_id,
                "created_at": body.get("created_at") or datetime.now().isoformat(timespec="seconds"),
                "discount_percent": discount,
                "note": body.get("note", ""),
                "photo_data_url": body.get("photo_data_url", ""),
                "items": items,
                "total_before": total_before,
                "total_after": total_after,
            }
            insert_sale(payload)
            self._json(201, payload)
            return

        self._json(404, {"error": "Not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/sales/"):
            sale_id = parsed.path.split("/")[-1]
            body = self._read_json()
            items = body.get("items", [])
            discount = float(body.get("discount_percent", 0))
            total_before, total_after = calc_totals(items, discount)
            payload = {
                "created_at": body.get("created_at") or datetime.now().isoformat(timespec="seconds"),
                "discount_percent": discount,
                "note": body.get("note", ""),
                "photo_data_url": body.get("photo_data_url", ""),
                "items": items,
                "total_before": total_before,
                "total_after": total_after,
            }
            update_sale(sale_id, payload)
            self._json(200, {"id": sale_id, **payload})
            return

        self._json(404, {"error": "Not found"})


if __name__ == "__main__":
    init_db()
    ensure_catalog()
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running at http://0.0.0.0:{port}")
    server.serve_forever()
