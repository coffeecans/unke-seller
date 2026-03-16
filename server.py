import base64
import json
import re
import sqlite3
from datetime import datetime
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


def scrape_catalog():
    req = Request("https://unke.store/catalog", headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S | re.I)
    catalog = []
    for script in scripts:
        if "price" in script and "name" in script:
            chunks = re.findall(r"\{[^{}]{20,400}\}", script)
            for ch in chunks:
                name_m = re.search(r'"name"\s*:\s*"([^"]+)"', ch)
                price_m = re.search(r'"price"\s*:\s*"?([0-9\.,]+)"?', ch)
                if not name_m or not price_m:
                    continue
                model = name_m.group(1).strip()
                raw_price = price_m.group(1).replace(",", ".")
                try:
                    price = float(raw_price)
                except ValueError:
                    continue
                if any(x["model"] == model for x in catalog):
                    continue
                catalog.append({
                    "id": f"scrape-{len(catalog)+1}",
                    "model": model,
                    "price": price,
                    "sizes": ["XS", "S", "M", "L", "XL"],
                    "colors": ["Не указан"],
                })
    if not catalog:
        raise RuntimeError("Каталог не удалось распарсить")
    save_catalog(catalog)
    return catalog


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
            catalog = load_catalog()
            if query:
                catalog = [c for c in catalog if query in c["model"].lower()]
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
                self._json(200, {"items": catalog, "source": "scraped"})
            except Exception as e:
                catalog = load_catalog()
                self._json(200, {"items": catalog, "source": "fallback", "warning": str(e)})
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
    port = 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Server running at http://0.0.0.0:{port}")
    server.serve_forever()
