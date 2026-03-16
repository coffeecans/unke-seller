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
                    price = _parse_price((item.get("offers") or {}).get("price") if isinstance(item.get("offers"), dict) else None)
                    if price is None:
                        price = _parse_price(item.get("price"))
                    products.append({"model": item.get("name", "").strip(), "price": price})
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
        price_m = re.search(r'class="[^"]*price[^"]*"[^>]*>(.*?)</span>', card, flags=re.I | re.S)
        raw_price = re.sub(r"<[^>]+>", "", price_m.group(1)) if price_m else ""
        price = _parse_price(raw_price)
        products.append({"model": model, "price": price})

    cleaned = []
    seen = set()
    for p in products:
        model = p.get("model", "").strip()
        price = p.get("price")
        if not model or model in seen:
            continue
        seen.add(model)
        cleaned.append({"model": model, "price": float(price or 0)})
    return cleaned


def scrape_catalog():
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/123 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    all_products = []
    seen = set()
    max_pages = 30

    for page in range(1, max_pages + 1):
        page_url = "https://unke.store/catalog" if page == 1 else f"https://unke.store/catalog/page/{page}/"
        req = Request(page_url, headers=headers)
        with urlopen(req, timeout=25) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        parsed = _extract_products_from_html(html)
        added_on_page = 0
        for p in parsed:
            if p["model"] in seen:
                continue
            seen.add(p["model"])
            all_products.append(p)
            added_on_page += 1

        if page > 1 and added_on_page == 0:
            break

    if len(all_products) < 12:
        raise RuntimeError("Каталог не удалось распарсить полностью")

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
