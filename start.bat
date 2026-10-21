@echo off
setlocal
echo ====================================
echo Afet Rota Sistemi Kurulum ve Baslat
echo ====================================

:: Check if virtual environment exists
if not exist venv (
    echo [1/4] Sanal ortam venv olusturuluyor...
    python -m venv venv
)

echo [2/4] Bagimliliklar kontrol ediliyor...
call venv\Scripts\activate
pip install -r requirements.txt

echo [3/4] Cevre (Environment) degiskenleri hazirlaniyor...
if not exist .env (
    copy .env.example .env
    echo .env dosyasi olusturuldu. ROBOFLOW_API_KEY vs. bu dosyadan duzenlenebilir.
)

echo [4/4] Sunucu baslatiliyor...
echo.
echo ========================================================
echo Lutfen bekleyin, harita yukleniyor (14MB)...
echo Sunucu, .env dosyasindaki HOST ve PORT ayarlariyla baslatilacak.
echo ========================================================
python -m backend.app

pause

