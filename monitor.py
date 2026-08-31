import time
import json
import os
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

# Список ссылок для мониторинга
URLS_TO_TRACK = [
    # 1. Квартиры (Арабкир, Аван, Зейтун, Нор-Норк, Абовян - Собственники)
    {
        "name": "🏢 Квартиры (Ереван / Абовян)",
        "url": "https://www.list.am/category/60?n=3%2C4%2C7%2C10%2C41&cmtype=0"
    },
    # 2. Дома / Земля (Котайк: Абовян, Ариндж, Балаовит, Джрвеж, Нор Ачн и др. - Собственники)
    {
        "name": "🏡 Дома / Земля (Котайк и пригород)",
        "url": "https://www.list.am/category/1386?n=41%2C81%2C803%2C89%2C82%2C786%2C107%2C75&cmtype=0"
    }
]

# Интервал проверки (в секундах) — каждые 2 минуты
CHECK_INTERVAL = 120

# Файл для сохранения уже отправленных объявлений
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
        self.wfile.write(b"List.am Bot is Running 24/7!")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    logging.info(f"Веб-сервер запущен на порту {port}")
    server.serve_forever()

def load_seen_items():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception as e:
            logging.error(f"Ошибка загрузки seen_items: {e}")
    return set()

def save_seen_items(seen_items):
    try:
        items_list = list(seen_items)[-3000:]
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(items_list, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Ошибка сохранения seen_items: {e}")

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
        except Exception as e:
            logging.warning(f"Не удалось отправить с фото: {e}")

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
    except Exception as e:
        logging.error(f"Ошибка отправки в Telegram: {e}")
        return False

def fetch_page(url):
    try:
        if USE_CURL_CFFI:
            resp = requests.get(url, impersonate="chrome120", headers=HEADERS, timeout=20)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=20)
        return resp
    except Exception as e:
        logging.error(f"Ошибка сетевого запроса к {url}: {e}")
        return None

def parse_category_page(category_info, seen_items):
    url = category_info["url"]
    cat_name = category_info["name"]
    
    resp = fetch_page(url)
    if not resp or resp.status_code != 200:
        code = resp.status_code if resp else "timeout/error"
        logging.warning(f"Ошибка доступа к {cat_name} (код {code})")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    links = soup.find_all("a", href=True)
    
    for a in links:
        href = a["href"]
        if not href.startswith("/item/"):
            continue
        
        item_id = href.replace("/item/", "").split("?")[0].strip()
        if not item_id.isdigit():
            continue
        
        if item_id in seen_items:
            continue

        full_url = f"https://www.list.am{href}"
        
        title_elem = a.find("div", class_="l") or a.find("div", class_="t") or a.find("div", class_="header")
        title = title_elem.get_text(strip=True) if title_elem else "Объявление на List.am"
        
        price_elem = a.find("div", class_="p") or a.find("div", class_="price")
        price = price_elem.get_text(strip=True) if price_elem else "Цена не указана"
        
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
            "category": cat_name,
            "title": title,
            "price": price,
            "location": location,
            "url": full_url,
            "photo": photo_url
        })
        
    return items

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    
    logging.info("🚀 Запуск облачного мониторинга List.am 24/7...")
    
    seen_items = load_seen_items()
    first_run = len(seen_items) == 0
    
    send_telegram_message(
        "☁️ <b>Облачный мониторинг List.am запущен 24/7!</b>\n\n"
        "📁 <b>Категории:</b> Квартиры (Ереван/Абовян) + Дома/Земля (Котайк)\n"
        "👤 <b>Фильтр:</b> Только собственники\n\n"
        "<i>Бот работает на сервере независимо от ноутбука.</i>"
    )
    
    while True:
        try:
            for cat in URLS_TO_TRACK:
                new_items = parse_category_page(cat, seen_items)
                
                if first_run:
                    for item in new_items:
                        seen_items.add(item["id"])
                    logging.info(f"[{cat['name']}] Загружено {len(new_items)} текущих объявлений в память.")
                else:
                    for item in new_items:
                        seen_items.add(item["id"])
                        
                        message = (
                            f"⚡ <b>НОВОЕ ОБЪЯВЛЕНИЕ (Собственник)</b>\n"
                            f"🏷 <i>{item['category']}</i>\n\n"
                            f"📌 <b>{item['title']}</b>\n"
                            f"💰 <b>Цена:</b> {item['price']}\n"
                            f"📍 <b>Локация:</b> {item['location']}\n\n"
                            f"🔗 <a href='{item['url']}'>Открыть на List.am</a>"
                        )
                        
                        send_telegram_message(message, item.get("photo"))
                        logging.info(f"⚡ Найдено: [{item['category']}] {item['title']} ({item['price']})")
                        time.sleep(1)
                
                save_seen_items(seen_items)
            
            if first_run:
                first_run = False
                send_telegram_message(f"✅ <b>База инициализирована ({len(seen_items)} объявлений).</b> Бот активен 24/7!")
                logging.info(f"Инициализация завершена. В базе {len(seen_items)} объявлений.")

        except Exception as e:
            logging.error(f"Ошибка в цикле: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
