#!/bin/bash
echo "===================================="
echo "Afet Rota Sistemi Kurulum ve Baslat"
echo "===================================="

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "[1/4] Sanal ortam (venv) olusturuluyor..."
    python3 -m venv venv
fi

echo "[2/4] Bagimliliklar kontrol ediliyor..."
source venv/bin/activate
pip install -r requirements.txt

echo "[3/4] Cevre (Environment) degiskenleri hazirlaniyor..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ".env dosyasi olusturuldu. (Eger API anahtarini degistirmeniz gerekirse burayi kullaabilirsiniz)."
fi

# Determine python command
if command -v python3 &>/dev/null; then
  PYTHON_CMD="python3"
else
  PYTHON_CMD="python"
fi

echo "[4/4] Sunucu baslatiliyor..."
echo ""
echo "========================================================"
echo "Lutfen bekleyin, harita yukleniyor (14MB)..."
echo "Sunucu, .env dosyasindaki HOST ve PORT ayarlariyla baslatilacak."
echo "========================================================"

$PYTHON_CMD -m backend.app

