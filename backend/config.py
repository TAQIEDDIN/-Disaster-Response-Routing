"""
Merkezi konfigürasyon modülü.
Tüm ayarlar .env dosyasından yüklenir.
"""
import os
from dotenv import load_dotenv

# Proje kök dizinindeki .env dosyasını yükle
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"))


# --- Roboflow AI Ayarları ---
ROBOFLOW_API_KEY = os.getenv("ROBOFLOW_API_KEY", "")
ROBOFLOW_API_URL = os.getenv("ROBOFLOW_API_URL", "https://serverless.roboflow.com")
ROBOFLOW_MODEL_ID = os.getenv("ROBOFLOW_MODEL_ID", "earthquake-damage-detection-xmfgr/1")

# AI tespit eşikleri
AI_CONFIDENCE = float(os.getenv("AI_CONFIDENCE", "0.25"))
AI_IOU_THRESHOLD = float(os.getenv("AI_IOU_THRESHOLD", "0.4"))

# --- Harita Ayarları ---
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Antakya, Hatay, Turkey")

# --- Rota Motoru Ayarları ---
# Tehlike bölgesi baz yarıçapı (metre)
DANGER_RADIUS_BASE = float(os.getenv("DANGER_RADIUS_BASE", "50"))
# Tehlike bölgesindeki edge'lere uygulanacak ağırlık çarpanı
DANGER_WEIGHT_MULTIPLIER = float(os.getenv("DANGER_WEIGHT_MULTIPLIER", "100"))

# --- Sunucu Ayarları ---
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
