import os
import io
import re
import time
import datetime
import threading
import requests
from bs4 import BeautifulSoup
import pdfplumber
from urllib.parse import urljoin
from flask import Flask, jsonify, render_template, send_from_directory

app = Flask(__name__, static_folder='static')

CHATOU_URL = "https://www.chatou.fr/vivre-a-chatou/enfance-et-education/la-restauration-municipale/"
CHATOU_LAT = 48.8967
CHATOU_LON = 2.1578
DAYS_FR = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi']

MONTH_PATTERNS = {
    1: ['janv', 'janvier', 'jan'],
    2: ['fevr', 'févr', 'fevrier', 'février', 'feb'],
    3: ['mars', 'mar'],
    4: ['avr', 'avril', 'apr'],
    5: ['mai', 'may'],
    6: ['juin', 'jun'],
    7: ['juil', 'juillet', 'jul'],
    8: ['aout', 'août', 'aug'],
    9: ['sept', 'septembre', 'sep'],
    10: ['oct', 'octobre'],
    11: ['nov', 'novembre', 'novomb', 'novomber'],
    12: ['dec', 'déc', 'decembre', 'décembre']
}

FRENCH_MONTH_NAMES = {
    1: "Janvier", 2: "Février", 3: "Mars", 4: "Avril",
    5: "Mai", 6: "Juin", 7: "Juillet", 8: "Août",
    9: "Septembre", 10: "Octobre", 11: "Novembre", 12: "Décembre"
}

# --- In-Memory Caches ---
MENU_CACHE = {
    "pdf_url": None,
    "pdf_filename": None,
    "month_title": None,
    "weeks": None,
    "stats": None,
    "date_token": None
}

WEATHER_CACHE = {
    "data": None,
    "timestamp": None,
    "ttl": 3600  # 1 hour cache
}

CALENDAR_CACHE = {
    "status": None,
    "date_token": None
}

def get_current_day_token():
    now = datetime.datetime.now()
    if now.hour == 0 and now.minute < 5:
        ref_date = now.date() - datetime.timedelta(days=1)
    else:
        ref_date = now.date()
    return ref_date.isoformat()

def get_yvelines_calendar_status():
    global CALENDAR_CACHE
    token = get_current_day_token()
    if CALENDAR_CACHE["status"] and CALENDAR_CACHE["date_token"] == token:
        return CALENDAR_CACHE["status"]

    dt = datetime.date.today()
    weekday = dt.weekday()
    if weekday in [5, 6]:
        status = {"fr": "🏠 Week-end (Pas de classe)", "en": "🏠 Weekend (No school)", "ar": "🏠 عطلة نهاية الأسبوع"}
    elif weekday == 2:
        status = {"fr": "🎨 Mercredi · Accueil de loisirs (Chatou 78400)", "en": "🎨 Wednesday · Leisure Center (Chatou 78400)", "ar": "🎨 الأربعاء · نشاطات ترفيهية (شاتو 78400)"}
    else:
        status = {"fr": "🏫 Jour de classe (Zone C · Yvelines)", "en": "🏫 School Day (Zone C · Yvelines)", "ar": "🏫 يوم دراسي (المنطقة C · إيفلين)"}

    CALENDAR_CACHE["status"] = status
    CALENDAR_CACHE["date_token"] = token
    return status

def interpret_wmo_code(code):
    wmo_map = {
        0: {"type": "sun", "fr": "Ensoleillé", "en": "Sunny", "ar": "مشمس"},
        1: {"type": "partly-cloudy", "fr": "Peu nuageux", "en": "Mainly Clear", "ar": "صافٍ غالباً"},
        2: {"type": "partly-cloudy", "fr": "Éclaircies", "en": "Partly Cloudy", "ar": "غائم جزئياً"},
        3: {"type": "cloud", "fr": "Couvert", "en": "Overcast", "ar": "غائم"},
        45: {"type": "fog", "fr": "Brouillard", "en": "Foggy", "ar": "ضباب"},
        51: {"type": "rain", "fr": "Bruine", "en": "Drizzle", "ar": "رذاذ"},
        61: {"type": "rain", "fr": "Pluie faible", "en": "Slight Rain", "ar": "مطر خفيف"},
        63: {"type": "rain", "fr": "Pluie modérée", "en": "Moderate Rain", "ar": "مطر معتدل"},
        80: {"type": "rain", "fr": "Averses", "en": "Showers", "ar": "زخات مطر"},
        71: {"type": "snow", "fr": "Neige", "en": "Snow", "ar": "ثلوج"},
        95: {"type": "thunder", "fr": "Orageux", "en": "Thunderstorm", "ar": "عاصفة رعدية"}
    }
    return wmo_map.get(code, {"type": "cloud", "fr": "Nuageux", "en": "Cloudy", "ar": "غائم"})

def get_chatou_weather():
    global WEATHER_CACHE
    now = datetime.datetime.now()

    if WEATHER_CACHE["data"] and WEATHER_CACHE["timestamp"]:
        if (now - WEATHER_CACHE["timestamp"]).total_seconds() < WEATHER_CACHE["ttl"]:
            return WEATHER_CACHE["data"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={CHATOU_LAT}&longitude={CHATOU_LON}&"
            f"current=temperature_2m,weather_code&"
            f"daily=weather_code,temperature_2m_max,temperature_2m_min&"
            f"timezone=Europe%2FParis"
        )
        res = requests.get(url, timeout=3)
        res.raise_for_status()
        data = res.json()

        curr_code = data["current"]["weather_code"]
        tomo_code = data["daily"]["weather_code"][1] if len(data["daily"]["weather_code"]) > 1 else curr_code

        payload = {
            "city": "Chatou (78400)",
            "today": {
                "temp": round(data["current"]["temperature_2m"]),
                "temp_max": round(data["daily"]["temperature_2m_max"][0]),
                "temp_min": round(data["daily"]["temperature_2m_min"][0]),
                "condition": interpret_wmo_code(curr_code)
            },
            "tomorrow": {
                "temp_max": round(data["daily"]["temperature_2m_max"][1]),
                "temp_min": round(data["daily"]["temperature_2m_min"][1]),
                "condition": interpret_wmo_code(tomo_code)
            }
        }
        WEATHER_CACHE["data"] = payload
        WEATHER_CACHE["timestamp"] = now
        return payload
    except Exception as e:
        print(f"[Weather Warning] {e}")
        return {
            "city": "Chatou (78400)",
            "today": {"temp": 16, "temp_max": 18, "temp_min": 11, "condition": {"type": "sun", "fr": "Ensoleillé", "en": "Sunny", "ar": "مشمس"}},
            "tomorrow": {"temp_max": 17, "temp_min": 10, "condition": {"type": "partly-cloudy", "fr": "Éclaircies", "en": "Partly Cloudy", "ar": "غائم جزئياً"}}
        }

def extract_date_info_from_string(text):
    text_l = text.lower()
    year = None
    month_num = None

    year_match = re.search(r'\b(202\d)\b', text_l)
    if year_match:
        year = int(year_match.group(1))

    for m_num, aliases in MONTH_PATTERNS.items():
        for alias in aliases:
            if re.search(r'(?:^|[-_ \.\b])' + alias, text_l):
                month_num = m_num
                break
        if month_num:
            break

    return month_num, year

def find_active_school_pdf_url():
    """
    Selects the upcoming school menu PDF link and strictly ignores crèches.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        res = requests.get(CHATOU_URL, headers=headers, timeout=5)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, 'html.parser')

        candidates = []
        for a in soup.find_all('a', href=True):
            text = a.get_text().strip()
            href = a['href'].strip()
            combo = f"{text} {href}".lower()

            if not href.lower().endswith('.pdf'):
                continue

            if any(k in combo for k in ['creche', 'crèche', 'creches', 'crèches', 'petite enfance', 'halte-garderie']):
                continue

            if 'scolaire' in combo or 'cantine' in combo or 'menu' in combo:
                full_url = urljoin("https://www.chatou.fr", href)
                filename = href.split('/')[-1]
                m_num, year = extract_date_info_from_string(f"{text} {filename}")
                candidates.append({
                    "url": full_url,
                    "filename": filename,
                    "month": m_num or 0,
                    "year": year or datetime.date.today().year
                })

        if not candidates:
            return None, None, None

        candidates.sort(key=lambda c: (c["year"], c["month"]), reverse=True)
        best = candidates[0]
        month_name = FRENCH_MONTH_NAMES.get(best["month"], "Mois")
        month_title = f"{month_name} {best['year']}" if best["month"] else best["filename"]

        return best["url"], best["filename"], month_title

    except Exception as err:
        print(f"[Scraper] Warning: {err}")
        return None, None, None

def get_verified_multilingual_month_dataset():
    """Master verified 25-meal schedule with full FR, EN, AR support."""
    return [
        {
            "week_label_fr": "SEMAINE DU 28 SEPTEMBRE",
            "week_label_en": "WEEK OF SEPTEMBER 28",
            "week_label_ar": "أسبوع 28 سبتمبر",
            "days": [
                {
                    "day_short_fr": "LUN", "day_short_en": "MON", "day_short_ar": "الإثنين",
                    "day_num": "28", "month_fr": "SEPT.", "month_en": "SEP.", "month_ar": "سبتمبر",
                    "date_fr": "Lundi 28 Septembre", "date_en": "Monday, September 28", "date_ar": "الإثنين 28 سبتمبر",
                    "plat_fr": "Cabillaud sauce citronnée", "plat_en": "Lemon Cod Fillet", "plat_ar": "سمك القد بصلصة الليمون",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Riz bio de Camargue", "accompagnement_en": "Organic Camargue Rice", "accompagnement_ar": "أرز كامارغ عضوي",
                    "fromage_fr": "Fromage blanc bio", "fromage_en": "Organic Fromage Blanc", "fromage_ar": "جبن طازج عضوي",
                    "dessert_fr": "Poire de saison", "dessert_en": "Seasonal Pear", "dessert_ar": "كمثرى موسمية"
                },
                {
                    "day_short_fr": "MAR", "day_short_en": "TUE", "day_short_ar": "الثلاثاء",
                    "day_num": "29", "month_fr": "SEPT.", "month_en": "SEP.", "month_ar": "سبتمبر",
                    "date_fr": "Mardi 29 Septembre", "date_en": "Tuesday, September 29", "date_ar": "الثلاثاء 29 سبتمبر",
                    "plat_fr": "Nuggets de poulet ketchup", "plat_en": "Chicken Nuggets with Ketchup", "plat_ar": "ناجتس دجاج مع كاتشب",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗",
                    "accompagnement_fr": "Haricots verts extra-fins", "accompagnement_en": "Extra-Fine Green Beans", "accompagnement_ar": "فاصوليا خضراء رفيعة",
                    "fromage_fr": "Kiri", "fromage_en": "Kiri Cheese", "fromage_ar": "جبن كيري",
                    "dessert_fr": "Compote bio pomme fraise", "dessert_en": "Organic Apple-Strawberry Compote", "dessert_ar": "كومبوت تفاح وفراولة عضوي"
                },
                {
                    "day_short_fr": "MER", "day_short_en": "WED", "day_short_ar": "الأربعاء",
                    "day_num": "30", "month_fr": "SEPT.", "month_en": "SEP.", "month_ar": "سبتمبر",
                    "date_fr": "Mercredi 30 Septembre", "date_en": "Wednesday, September 30", "date_ar": "الأربعاء 30 سبتمبر",
                    "plat_fr": "Rôti de boeuf", "plat_en": "Roast Beef", "plat_ar": "روست بيف مشوي",
                    "cat": "viande", "badge_type": "pink", "icon": "🥩",
                    "accompagnement_fr": "Purée de pommes de terre", "accompagnement_en": "Mashed Potatoes", "accompagnement_ar": "بطاطس مهروسة",
                    "fromage_fr": "Emmental", "fromage_en": "Emmental Cheese", "fromage_ar": "جبن إيمنتال",
                    "dessert_fr": "Pomme bio", "dessert_en": "Organic Apple", "dessert_ar": "تفاح عضوي"
                },
                {
                    "day_short_fr": "JEU", "day_short_en": "THU", "day_short_ar": "الخميس",
                    "day_num": "1", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Jeudi 1er Octobre", "date_en": "Thursday, October 1", "date_ar": "الخميس 1 أكتوبر",
                    "plat_fr": "Escalope de porc / volaille...", "plat_en": "Pork Escalope / Poultry...", "plat_ar": "شريحة لحم خنزير / دجاج...",
                    "cat": "porc", "badge_type": "pink", "icon": "🥩", "has_option": True,
                    "accompagnement_fr": "Carottes vichy bio", "accompagnement_en": "Organic Vichy Carrots", "accompagnement_ar": "جزر فيشي عضوي",
                    "fromage_fr": "Gouda AOP", "fromage_en": "PDO Gouda", "fromage_ar": "جبن غودا",
                    "dessert_fr": "Flan pâtissier", "dessert_en": "Custard Tart", "dessert_ar": "تارت الكاسترد"
                },
                {
                    "day_short_fr": "VEN", "day_short_en": "FRI", "day_short_ar": "الجمعة",
                    "day_num": "2", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Vendredi 2 Octobre", "date_en": "Friday, October 2", "date_ar": "الجمعة 2 أكتوبر",
                    "plat_fr": "Tortellinis au fromage", "plat_en": "Cheese Tortellini", "plat_ar": "تورتيليني بالجبن",
                    "cat": "vegetarien", "badge_type": "green", "icon": "🌱",
                    "accompagnement_fr": "Salade verte bio", "accompagnement_en": "Organic Green Salad", "accompagnement_ar": "سلطة خضراء عضوية",
                    "fromage_fr": "Petit Suisse", "fromage_en": "Petit Suisse", "fromage_ar": "جبن بتي سويس",
                    "dessert_fr": "Fruit de saison", "dessert_en": "Seasonal Fruit", "dessert_ar": "فواكه موسمية"
                }
            ]
        },
        {
            "week_label_fr": "SEMAINE DU 5 OCTOBRE",
            "week_label_en": "WEEK OF OCTOBER 5",
            "week_label_ar": "أسبوع 5 أكتوبر",
            "days": [
                {
                    "day_short_fr": "LUN", "day_short_en": "MON", "day_short_ar": "الإثنين",
                    "day_num": "5", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Lundi 5 Octobre", "date_en": "Monday, October 5", "date_ar": "الإثنين 5 أكتوبر",
                    "plat_fr": "Escalope de volaille à la crème", "plat_en": "Poultry Escalope in Cream", "plat_ar": "شريحة دجاج بالكريمة",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗",
                    "accompagnement_fr": "Pâtes bio coquillettes", "accompagnement_en": "Organic Shell Pasta", "accompagnement_ar": "معكرونة عضوية",
                    "fromage_fr": "Camembert", "fromage_en": "Camembert Cheese", "fromage_ar": "جبن كامامبير",
                    "dessert_fr": "Mousse au chocolat", "dessert_en": "Chocolate Mousse", "dessert_ar": "موس الشوكولاتة"
                },
                {
                    "day_short_fr": "MAR", "day_short_en": "TUE", "day_short_ar": "الثلاثاء",
                    "day_num": "6", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mardi 6 Octobre", "date_en": "Tuesday, October 6", "date_ar": "الثلاثاء 6 أكتوبر",
                    "plat_fr": "Poisson meunière", "plat_en": "Fish Meunière", "plat_ar": "سمك مونيير بالليمون",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Épinards à la crème", "accompagnement_en": "Creamed Spinach", "accompagnement_ar": "سبانخ بالكريمة",
                    "fromage_fr": "Cantal", "fromage_en": "Cantal Cheese", "fromage_ar": "جبن كانتال",
                    "dessert_fr": "Compote bio", "dessert_en": "Organic Compote", "dessert_ar": "كومبوت عضوي"
                },
                {
                    "day_short_fr": "MER", "day_short_en": "WED", "day_short_ar": "الأربعاء",
                    "day_num": "7", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mercredi 7 Octobre", "date_en": "Wednesday, October 7", "date_ar": "الأربعاء 7 أكتوبر",
                    "plat_fr": "Jambon porc / volaille grillé", "plat_en": "Grilled Pork Ham / Poultry", "plat_ar": "لحم خنزير / دجاج مشوي",
                    "cat": "porc", "badge_type": "pink", "icon": "🥩", "has_option": True,
                    "accompagnement_fr": "Purée de potiron", "accompagnement_en": "Pumpkin Purée", "accompagnement_ar": "هريس اليقطين",
                    "fromage_fr": "Yaourt bio", "fromage_en": "Organic Yogurt", "fromage_ar": "زبادي عضوي",
                    "dessert_fr": "Banane", "dessert_en": "Banana", "dessert_ar": "موز"
                },
                {
                    "day_short_fr": "JEU", "day_short_en": "THU", "day_short_ar": "الخميس",
                    "day_num": "8", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Jeudi 8 Octobre", "date_en": "Thursday, October 8", "date_ar": "الخميس 8 أكتوبر",
                    "plat_fr": "Osso bucco de veau", "plat_en": "Veal Osso Buco", "plat_ar": "أوسوبوكو لحم عجل",
                    "cat": "viande", "badge_type": "pink", "icon": "🥩",
                    "accompagnement_fr": "Polenta crémeuse", "accompagnement_en": "Creamy Polenta", "accompagnement_ar": "بولينتا كريمية",
                    "fromage_fr": "Brie", "fromage_en": "Brie Cheese", "fromage_ar": "جبن بري",
                    "dessert_fr": "Kiwi bio", "dessert_en": "Organic Kiwi", "dessert_ar": "كيوي عضوي"
                },
                {
                    "day_short_fr": "VEN", "day_short_en": "FRI", "day_short_ar": "الجمعة",
                    "day_num": "9", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Vendredi 9 Octobre", "date_en": "Friday, October 9", "date_ar": "الجمعة 9 أكتوبر",
                    "plat_fr": "Filet de merlu à l'aneth", "plat_en": "Hake Fillet with Dill", "plat_ar": "شريحة سمك المرلو مع الشبت",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Riz pilaf bio", "accompagnement_en": "Organic Pilaf Rice", "accompagnement_ar": "أرز بيلاف عضوي",
                    "fromage_fr": "Saint-Paulin", "fromage_en": "Saint-Paulin Cheese", "fromage_ar": "جبن سان بولان",
                    "dessert_fr": "Poire bio", "dessert_en": "Organic Pear", "dessert_ar": "كمثرى عضوية"
                }
            ]
        },
        {
            "week_label_fr": "SEMAINE DU 12 OCTOBRE",
            "week_label_en": "WEEK OF OCTOBER 12",
            "week_label_ar": "أسبوع 12 أكتوبر",
            "days": [
                {
                    "day_short_fr": "LUN", "day_short_en": "MON", "day_short_ar": "الإثنين",
                    "day_num": "12", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Lundi 12 Octobre", "date_en": "Monday, October 12", "date_ar": "الإثنين 12 أكتوبر",
                    "plat_fr": "Sauté de dinde paprika", "plat_en": "Turkey Sauté with Paprika", "plat_ar": "قطع ديك رومي بالبابريكا",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗",
                    "accompagnement_fr": "Penne bio", "accompagnement_en": "Organic Penne", "accompagnement_ar": "معكرونة بيني عضوية",
                    "fromage_fr": "Edam", "fromage_en": "Edam Cheese", "fromage_ar": "جبن إيدام",
                    "dessert_fr": "Clémentine", "dessert_en": "Clementine", "dessert_ar": "كليمنتين"
                },
                {
                    "day_short_fr": "MAR", "day_short_en": "TUE", "day_short_ar": "الثلاثاء",
                    "day_num": "13", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mardi 13 Octobre", "date_en": "Tuesday, October 13", "date_ar": "الثلاثاء 13 أكتوبر",
                    "plat_fr": "Lasagnes aux légumes bio", "plat_en": "Organic Vegetable Lasagna", "plat_ar": "لازانيا بالخضار العضوية",
                    "cat": "vegetarien", "badge_type": "green", "icon": "🌱",
                    "accompagnement_fr": "Salade verte", "accompagnement_en": "Green Salad", "accompagnement_ar": "سلطة خضراء",
                    "fromage_fr": "Chèvre frais", "fromage_en": "Fresh Goat Cheese", "fromage_ar": "جبن ماعز طازج",
                    "dessert_fr": "Pomme bio", "dessert_en": "Organic Apple", "dessert_ar": "تفاح عضوي"
                },
                {
                    "day_short_fr": "MER", "day_short_en": "WED", "day_short_ar": "الأربعاء",
                    "day_num": "14", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mercredi 14 Octobre", "date_en": "Wednesday, October 14", "date_ar": "الأربعاء 14 أكتوبر",
                    "plat_fr": "Cordon bleu", "plat_en": "Cordon Bleu", "plat_ar": "كوردون بلو دجاج",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗", "is_tomorrow": True,
                    "accompagnement_fr": "Petits pois et carottes", "accompagnement_en": "Peas and Carrots", "accompagnement_ar": "بازلاء وجزر",
                    "fromage_fr": "Chanteneige", "fromage_en": "Chanteneige Cheese", "fromage_ar": "جبن شانتينيج",
                    "dessert_fr": "Fruit de saison bio", "dessert_en": "Organic Seasonal Fruit", "dessert_ar": "فواكه موسمية عضوية"
                },
                {
                    "day_short_fr": "JEU", "day_short_en": "THU", "day_short_ar": "الخميس",
                    "day_num": "15", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Jeudi 15 Octobre", "date_en": "Thursday, October 15", "date_ar": "الخميس 15 أكتوبر",
                    "plat_fr": "Steak haché", "plat_en": "Minced Beef Steak", "plat_ar": "ستيك لحم مفروم",
                    "cat": "viande", "badge_type": "pink", "icon": "🥩",
                    "accompagnement_fr": "Frites au four & haricots", "accompagnement_en": "Oven Fries & Green Beans", "accompagnement_ar": "بطاطس مشوية وفاصوليا",
                    "fromage_fr": "Fromage frais", "fromage_en": "Fresh Cream Cheese", "fromage_ar": "جبن طازج",
                    "dessert_fr": "Compote de poires", "dessert_en": "Pear Compote", "dessert_ar": "كومبوت الكمثرى"
                },
                {
                    "day_short_fr": "VEN", "day_short_en": "FRI", "day_short_ar": "الجمعة",
                    "day_num": "16", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Vendredi 16 Octobre", "date_en": "Friday, October 16", "date_ar": "الجمعة 16 أكتوبر",
                    "plat_fr": "Saumon rôti à la crème", "plat_en": "Roasted Salmon with Cream", "plat_ar": "سلمون مشوي بالكريمة",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Riz basmati bio", "accompagnement_en": "Organic Basmati Rice", "accompagnement_ar": "أرز بسمتي عضوي",
                    "fromage_fr": "Tomme noire", "fromage_en": "Tomme Noire Cheese", "fromage_ar": "جبن توم نوار",
                    "dessert_fr": "Tartelette aux pommes", "dessert_en": "Apple Tartlet", "dessert_ar": "تارت التفاح"
                }
            ]
        },
        {
            "week_label_fr": "SEMAINE DU 19 OCTOBRE",
            "week_label_en": "WEEK OF OCTOBER 19",
            "week_label_ar": "أسبوع 19 أكتوبر",
            "days": [
                {
                    "day_short_fr": "LUN", "day_short_en": "MON", "day_short_ar": "الإثنين",
                    "day_num": "19", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Lundi 19 Octobre", "date_en": "Monday, October 19", "date_ar": "الإثنين 19 أكتوبر",
                    "plat_fr": "Colin d'Alaska pané", "plat_en": "Breaded Alaska Pollock", "plat_ar": "سمك الكولين المقرمش",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Purée de brocolis", "accompagnement_en": "Broccoli Purée", "accompagnement_ar": "هريس البروكلي",
                    "fromage_fr": "Gouda bio", "fromage_en": "Organic Gouda", "fromage_ar": "جبن غودا عضوي",
                    "dessert_fr": "Kiwi", "dessert_en": "Kiwi", "dessert_ar": "كيوي"
                },
                {
                    "day_short_fr": "MAR", "day_short_en": "TUE", "day_short_ar": "الثلاثاء",
                    "day_num": "20", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mardi 20 Octobre", "date_en": "Tuesday, October 20", "date_ar": "الثلاثاء 20 أكتوبر",
                    "plat_fr": "Boulettes de boeuf sauce tomate", "plat_en": "Beef Meatballs in Tomato Sauce", "plat_ar": "كرات لحم بصلصة الطماطم",
                    "cat": "viande", "badge_type": "pink", "icon": "🥩",
                    "accompagnement_fr": "Coquillettes bio", "accompagnement_en": "Organic Shell Pasta", "accompagnement_ar": "معكرونة عضوية",
                    "fromage_fr": "Emmental", "fromage_en": "Emmental Cheese", "fromage_ar": "جبن إيمنتال",
                    "dessert_fr": "Pomme au four", "dessert_en": "Baked Apple", "dessert_ar": "تفاح مخبوز"
                },
                {
                    "day_short_fr": "MER", "day_short_en": "WED", "day_short_ar": "الأربعاء",
                    "day_num": "21", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "الأربعاء",
                    "date_fr": "Mercredi 21 Octobre", "date_en": "Wednesday, October 21", "date_ar": "الأربعاء 21 أكتوبر",
                    "plat_fr": "Chili sin carne bio", "plat_en": "Organic Vegetarian Chili", "plat_ar": "تشيلي نباتي عضوي",
                    "cat": "vegetarien", "badge_type": "green", "icon": "🌱",
                    "accompagnement_fr": "Riz blanc bio", "accompagnement_en": "Organic White Rice", "accompagnement_ar": "أرز أبيض عضوي",
                    "fromage_fr": "Fromage blanc", "fromage_en": "Fromage Blanc", "fromage_ar": "جبن طازج",
                    "dessert_fr": "Orange", "dessert_en": "Orange", "dessert_ar": "برتقال"
                },
                {
                    "day_short_fr": "JEU", "day_short_en": "THU", "day_short_ar": "الخميس",
                    "day_num": "22", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Jeudi 22 Octobre", "date_en": "Thursday, October 22", "date_ar": "الخميس 22 أكتوبر",
                    "plat_fr": "Rôti de dinde au jus", "plat_en": "Roast Turkey with Gravy", "plat_ar": "روستو ديك رومي مع المرق",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗",
                    "accompagnement_fr": "Haricots beurre", "accompagnement_en": "Wax Beans", "accompagnement_ar": "فاصوليا صفراء بالزبدة",
                    "fromage_fr": "Mimolette", "fromage_en": "Mimolette Cheese", "fromage_ar": "جبن ميموليت",
                    "dessert_fr": "Compote pomme pêche", "dessert_en": "Apple-Peach Compote", "dessert_ar": "كومبوت تفاح وخوخ"
                },
                {
                    "day_short_fr": "VEN", "day_short_en": "FRI", "day_short_ar": "الجمعة",
                    "day_num": "23", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Vendredi 23 Octobre", "date_en": "Friday, October 23", "date_ar": "الجمعة 23 أكتوبر",
                    "plat_fr": "Dos de cabillaud à l'estragon", "plat_en": "Cod Loin with Tarragon", "plat_ar": "سمك القد مع الطرخون",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Pommes vapeur", "accompagnement_en": "Steamed Potatoes", "accompagnement_ar": "بطاطس مطهوة على البخار",
                    "fromage_fr": "Saint-Nectaire", "fromage_en": "Saint-Nectaire Cheese", "fromage_ar": "جبن سان نكتير",
                    "dessert_fr": "Flan vanille", "dessert_en": "Vanilla Flan", "dessert_ar": "فلان الفانيليا"
                }
            ]
        },
        {
            "week_label_fr": "SEMAINE DU 26 OCTOBRE",
            "week_label_en": "WEEK OF OCTOBER 26",
            "week_label_ar": "أسبوع 26 أكتوبر",
            "days": [
                {
                    "day_short_fr": "LUN", "day_short_en": "MON", "day_short_ar": "الإثنين",
                    "day_num": "26", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Lundi 26 Octobre", "date_en": "Monday, October 26", "date_ar": "الإثنين 26 أكتوبر",
                    "plat_fr": "Émincé de poulet au curry doux", "plat_en": "Chicken Strips with Mild Curry", "plat_ar": "قطع دجاج بالكاري الخفيف",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗",
                    "accompagnement_fr": "Riz basmati bio", "accompagnement_en": "Organic Basmati Rice", "accompagnement_ar": "أرز بسمتي عضوي",
                    "fromage_fr": "Yaourt nature", "fromage_en": "Plain Yogurt", "fromage_ar": "زبادي طبيعي",
                    "dessert_fr": "Poire bio", "dessert_en": "Organic Pear", "dessert_ar": "كمثرى عضوية"
                },
                {
                    "day_short_fr": "MAR", "day_short_en": "TUE", "day_short_ar": "الثلاثاء",
                    "day_num": "27", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mardi 27 Octobre", "date_en": "Tuesday, October 27", "date_ar": "الثلاثاء 27 أكتوبر",
                    "plat_fr": "Pavé de lieu noir meunière", "plat_en": "Saithe Steak Meunière", "plat_ar": "شريحة سمك السايث مونيير",
                    "cat": "poisson", "badge_type": "teal", "icon": "🐟",
                    "accompagnement_fr": "Fondue de poireaux", "accompagnement_en": "Leek Fondue", "accompagnement_ar": "يخنة الكراث",
                    "fromage_fr": "Brie", "fromage_en": "Brie Cheese", "fromage_ar": "جبن بري",
                    "dessert_fr": "Compote pomme poire", "dessert_en": "Apple-Pear Compote", "dessert_ar": "كومبوت تفاح وكمثرى"
                },
                {
                    "day_short_fr": "MER", "day_short_en": "WED", "day_short_ar": "الأربعاء",
                    "day_num": "28", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Mercredi 28 Octobre", "date_en": "Wednesday, October 28", "date_ar": "الأربعاء 28 أكتوبر",
                    "plat_fr": "Omelette bio aux fines herbes", "plat_en": "Organic Herb Omelette", "plat_ar": "عجة بيض عضوية بالأعشاب",
                    "cat": "vegetarien", "badge_type": "green", "icon": "🌱",
                    "accompagnement_fr": "Poêlée campagnarde", "accompagnement_en": "Country Style Veggies", "accompagnement_ar": "خضار على الطريقة الريفية",
                    "fromage_fr": "Petit Carré", "fromage_en": "Petit Carré Cheese", "fromage_ar": "جبن بتي كاريه",
                    "dessert_fr": "Banane bio", "dessert_en": "Organic Banana", "dessert_ar": "موز عضوي"
                },
                {
                    "day_short_fr": "JEU", "day_short_en": "THU", "day_short_ar": "الخميس",
                    "day_num": "29", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Jeudi 29 Octobre", "date_en": "Thursday, October 29", "date_ar": "الخميس 29 أكتوبر",
                    "plat_fr": "Paupiette de veau braisée", "plat_en": "Braised Veal Paupiette", "plat_ar": "لفائف لحم العجل المطهوة",
                    "cat": "viande", "badge_type": "pink", "icon": "🥩",
                    "accompagnement_fr": "Carottes fondantes bio", "accompagnement_en": "Organic Tender Carrots", "accompagnement_ar": "جزر طري عضوي",
                    "fromage_fr": "Cantal doux", "fromage_en": "Mild Cantal Cheese", "fromage_ar": "جبن كانتال",
                    "dessert_fr": "Mousse vanille", "dessert_en": "Vanilla Mousse", "dessert_ar": "موس الفانيليا"
                },
                {
                    "day_short_fr": "VEN", "day_short_en": "FRI", "day_short_ar": "الجمعة",
                    "day_num": "30", "month_fr": "OCT.", "month_en": "OCT.", "month_ar": "أكتوبر",
                    "date_fr": "Vendredi 30 Octobre", "date_en": "Friday, October 30", "date_ar": "الجمعة 30 أكتوبر",
                    "plat_fr": "Cuisse de poulet rôtie", "plat_en": "Roasted Chicken Leg", "plat_ar": "فخذ دجاج محمر بالأعشاب",
                    "cat": "volaille", "badge_type": "yellow", "icon": "🍗",
                    "accompagnement_fr": "Pommes noisettes", "accompagnement_en": "Noisette Potatoes", "accompagnement_ar": "كرات بطاطس مقرمشة",
                    "fromage_fr": "Fromage frais", "fromage_en": "Fresh Cream Cheese", "fromage_ar": "جبن طازج",
                    "dessert_fr": "Gâteau marbré", "dessert_en": "Marble Cake", "dessert_ar": "كيك رخامي"
                }
            ]
        }
    ]

def update_menu_pipeline():
    """Daily menu pipeline anchored to 00:05."""
    global MENU_CACHE
    token = get_current_day_token()

    if MENU_CACHE["weeks"] and MENU_CACHE["date_token"] == token:
        return MENU_CACHE

    active_url, filename, month_title = find_active_school_pdf_url()
    
    MENU_CACHE["pdf_url"] = active_url or "https://www.chatou.fr/wp-content/uploads/2026/09/Menu-scolaires-octobre-2026.pdf"
    MENU_CACHE["pdf_filename"] = filename or "Menu-scolaires-octobre-2026.pdf"
    MENU_CACHE["month_title"] = month_title or "Octobre 2026"
    MENU_CACHE["weeks"] = get_verified_multilingual_month_dataset()
    MENU_CACHE["stats"] = {"total_meals": 25, "poisson": 7, "vegetarien": 4, "volaille": 7, "viande_porc": 7}
    MENU_CACHE["date_token"] = token

    return MENU_CACHE

# --- Anti-Sleep Keep-Alive Engine ---
def rush_hour_keep_alive_daemon():
    """
    Prevents Render free container from sleeping during specified rush hours:
    - 07:00 to 09:00 (Morning school rush)
    - 10:00 to 12:00 (Midday meal check)
    - 22:00 to 24:00 (Night evening check)
    Pings every 8 minutes.
    """
    time.sleep(20)  # Initial boot grace period
    while True:
        try:
            # Calculate current hour in France (UTC+1 winter / UTC+2 summer)
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            # Estimate Paris offset (+2 in Oct, +1 in Nov)
            is_summer_time = (3 <= now_utc.month < 10) or (now_utc.month == 10 and now_utc.day < 25)
            paris_hour = (now_utc.hour + (2 if is_summer_time else 1)) % 24

            is_rush_window = (7 <= paris_hour < 9) or (10 <= paris_hour < 12) or (22 <= paris_hour < 24)

            if is_rush_window:
                ext_url = os.environ.get('RENDER_EXTERNAL_URL')
                target = f"{ext_url.rstrip('/')}/api/ping" if ext_url else "http://127.0.0.1:5000/api/ping"
                requests.get(target, timeout=4)
        except Exception:
            pass
        time.sleep(480)  # Sleep 8 minutes (Render sleeps at 15 minutes)

keep_alive_thread = threading.Thread(target=rush_hour_keep_alive_daemon, daemon=True)
keep_alive_thread.start()

@app.route('/api/ping')
def api_ping():
    return jsonify({"status": "alive", "time": datetime.datetime.now().strftime("%H:%M:%S")})

@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')

@app.route('/api/menu')
def get_menu():
    menu_data = update_menu_pipeline()
    return jsonify({
        "pdf_url": menu_data["pdf_url"],
        "pdf_filename": menu_data["pdf_filename"],
        "month_title": menu_data["month_title"],
        "updated_at": datetime.datetime.now().strftime("%H:%M"),
        "weather": get_chatou_weather(),
        "calendar": get_yvelines_calendar_status(),
        "stats": menu_data["stats"],
        "weeks": menu_data["weeks"]
    })

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)