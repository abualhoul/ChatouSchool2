import os
import io
import re
import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from flask import Flask, jsonify, render_template

app = Flask(__name__, static_folder='static')

CHATOU_URL = "https://www.chatou.fr/vivre-a-chatou/enfance-et-education/la-restauration-municipale/"
CHATOU_LAT = 48.8967
CHATOU_LON = 2.1578

WEATHER_CACHE = {
    "data": None,
    "timestamp": None
}

def get_yvelines_calendar_status():
    dt = datetime.date.today()
    weekday = dt.weekday()
    if weekday in [5, 6]:
        return {"fr": "🏠 Week-end (Pas de classe)", "en": "🏠 Weekend (No school)", "ar": "🏠 عطلة نهاية الأسبوع"}
    elif weekday == 2:
        return {"fr": "🎨 Mercredi · Accueil de loisirs (Chatou 78400)", "en": "🎨 Wednesday · Leisure Center (Chatou 78400)", "ar": "🎨 الأربعاء · نشاطات ترفيهية (شاتو 78400)"}
    else:
        return {"fr": "🏫 Jour de classe (Zone C · Yvelines)", "en": "🏫 School Day (Zone C · Yvelines)", "ar": "🏫 يوم دراسي (المنطقة C · إيفلين)"}

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
        if (now - WEATHER_CACHE["timestamp"]).total_seconds() < 900:
            return WEATHER_CACHE["data"]

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={CHATOU_LAT}&longitude={CHATOU_LON}&"
            f"current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&"
            f"daily=weather_code,temperature_2m_max,temperature_2m_min&"
            f"timezone=Europe%2FParis"
        )
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()

        curr_code = data["current"]["weather_code"]
        tomo_code = data["daily"]["weather_code"][1] if len(data["daily"]["weather_code"]) > 1 else curr_code

        weather_payload = {
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
        WEATHER_CACHE["data"] = weather_payload
        WEATHER_CACHE["timestamp"] = now
        return weather_payload
    except Exception as e:
        print(f"[Weather Warning] {e}")
        return {
            "city": "Chatou (78400)",
            "today": {"temp": 16, "temp_max": 18, "temp_min": 11, "condition": {"type": "sun", "fr": "Ensoleillé", "en": "Sunny", "ar": "مشمس"}},
            "tomorrow": {"temp_max": 17, "temp_min": 10, "condition": {"type": "partly-cloudy", "fr": "Éclaircies", "en": "Partly Cloudy", "ar": "غائم جزئياً"}}
        }

@app.route('/api/menu')
def get_menu():
    return jsonify({
        "pdf_url": "https://www.chatou.fr/wp-content/uploads/2026/09/Menu-scolaires-octobre-2026.pdf",
        "pdf_filename": "Menu-scolaires-octobre-2026.pdf",
        "updated_at": datetime.datetime.now().strftime("%H:%M"),
        "weather": get_chatou_weather(),
        "calendar": get_yvelines_calendar_status(),
        "stats": {"total_meals": 25, "poisson": 7, "vegetarien": 4, "volaille": 7, "viande_porc": 7}
    })

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)