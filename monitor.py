import time
import json
import os
import re
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from bs4 import BeautifulSoup

try:
    from curl_cffi import requests
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8849384703:AAFFf9VbgmpadHUvYJ3ZZDagoqZyDgpz3-M"
CHAT_ID = "8368744809"

URLS_TO_TRACK = [
    {
        "name": "🏢 Продажа квартир (от $50k)",
        "badge": "🏢 КВАРТИРА (Продажа)",
        "min_price": 50000,
        "url": "https://www.list.am/category/60?n=3%2C4%2C7%2C10%2C41&cmtype=0&price1=50000&crc=1"
    },
    {
        "name": "🏡 Продажа домов (от $50k)",
        "badge": "🏡 ДОМ (Продажа)",
        "min_price": 50000,
        "url": "https://www.list.am/category/1386?n=41%2C81%2C803%2C89%2C82%2C786%2C107%2C75&cmtype=0&price1=50000&crc=1"
    },
    {
        "name": "🌳 Земельные участки (от $35k)",
        "badge": "🌳 УЧАСТОК (Продажа)",
        "min_price": 35000,
        "url": "https://www.list.am/category/1447?n=3%2C4%2C7%2C81%2C803%2C89%2C82%2C786%2C107&cmtype=0&crc=1&price1=35000"
    }
]

CHECK_INTERVAL = 120
SEEN_FILE = "seen_items.json"
# ===================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7,hy;q=0.6",
    "Referer": "https://www.list.am/",
}

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"OK - Real Estate Bot Active")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

def load_seen_items():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_seen_items(seen_items):
    try:
        items_list = list(seen_items)[-5000:]
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(items_list, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def parse_usd_price(price_str):
    try:
        clean = re.sub(r"[^\d]", "", price_str)
        if not clean:
            return 0
        num = int(clean)
        if "$" in price_str:
            return num
        elif "֏" in price_str:
            return num / 390
        elif "€" in price_str:
            return num * 1.08
        return num
    except:
        return 0

def send_telegram_message(text, photo_url=None):
    if photo_url:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": CHAT_ID,
            "photo": photo_url,
            "caption": text,
            "parse_mode": "HTML"
        }
        try:
            r = requests.post(url, data=payload, timeout=10)
            if r.status_code == 200:
                return True
        except Exception:
            pass

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False

def fetch_page(url):
    try:
        if USE_CURL_CFFI:
            return requests.get(url, impersonate="chrome120", headers=HEADERS, timeout=20)
        else:
            return requests.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        logging.error(f"Сетевая ошибка при запросе {url}: {e}")
        return None

def parse_category_page(category_info, seen_items):
    url = category_info["url"]
    min_p = category_info["min_price"]
    badge = category_info["badge"]
    name = category_info["name"]
    
    resp = fetch_page(url)
    if not resp:
        logging.warning(f"[{name}] Нет ответа от сервера (None)")
        return []
    if resp.status_code != 200:
        logging.warning(f"[{name}] Ошибка доступа (Код: {resp.status_code})")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    links = soup.find_all("a", href=True)
    item_links_count = 0
    
    for a in links:
        href = a["href"]
        if not href.startswith("/item/"):
            continue
        
        item_links_count += 1
        item_id = href.replace("/item/", "").split("?")[0].strip()
        if not item_id.isdigit() or item_id in seen_items:
            continue

        price_elem = a.find("div", class_="p") or a.find("div", class_="price")
        price = price_elem.get_text(strip=True) if price_elem else "0"
        
        usd_price = parse_usd_price(price)
        if usd_price > 0 and usd_price < min_p:
            seen_items.add(item_id)
            continue

        title_elem = a.find("div", class_="l") or a.find("div", class_="t") or a.find("div", class_="header")
        title = title_elem.get_text(strip=True) if title_elem else "Объявление"
        
        loc_elem = a.find("div", class_="d") or a.find("div", class_="at") or a.find("div", class_="location")
        location = loc_elem.get_text(" • ", strip=True) if loc_elem else "Локация не указана"

        img = a.find("img")
        photo_url = None
        if img:
            photo_url = img.get("data-original") or img.get("data-src") or img.get("src")
            if photo_url and photo_url.startswith("//"):
                photo_url = "https:" + photo_url

        items.append({
            "id": item_id,
            "badge": badge,
            "title": title,
            "price": price,
            "location": location,
            "url": f"https://www.list.am{href}",
            "photo": photo_url
        })
        
    logging.info(f"[{name}] Статус: 200 OK | Всего объявлений на странице: {item_links_count} | Новых подходящих: {len(items)}")
    return items

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    
    seen_items = load_seen_items()
    
    logging.info("🚀 Запуск синхронизации базы...")
    for cat in URLS_TO_TRACK:
        items = parse_category_page(cat, seen_items)
        for it in items:
            seen_items.add(it["id"])
        time.sleep(1)
    save_seen_items(seen_items)
    
    send_telegram_message(
        "🚀 <b>Мониторинг обновлен!</b>\n\n"
        "📁 <b>Отслеживаем:</b> Квартиры (от $50k), Дома (от $50k), Участки (от $35k)\n"
        "👤 <b>Фильтр:</b> Только собственники\n"
        f"✅ <i>Синхронизировано {len(seen_items)} объектов в памяти.</i>"
    )
    
    while True:
        try:
            time.sleep(CHECK_INTERVAL)
            
            for cat in URLS_TO_TRACK:
                new_items = parse_category_page(cat, seen_items)
                
                for item in new_items:
                    seen_items.add(item["id"])
                    
                    message = (
                        f"⚡ <b>НОВОЕ ОБЪЯВЛЕНИЕ (Собственник)</b>\n"
                        f"🏷 <b>{item['badge']}</b>\n\n"
                        f"📌 <b>{item['title']}</b>\n"
                        f"💰 <b>Цена:</b> {item['price']}\n"
                        f"📍 <b>Локация:</b> {item['location']}\n\n"
                        f"🔗 <a href='{item['url']}'>Открыть на List.am</a>"
                    )
                    
                    send_telegram_message(message, item.get("photo"))
                    time.sleep(1.5)
                
                save_seen_items(seen_items)
                time.sleep(1)
                
        except Exception as e:
            logging.error(f"Ошибка в цикле: {e}")

if __name__ == "__main__":
    main()
