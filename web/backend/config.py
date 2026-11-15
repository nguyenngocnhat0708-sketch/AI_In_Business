import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Tìm thư mục gốc dự án
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Tải biến môi trường từ file .env nếu có
ENV_FILE = BASE_DIR / ".env"
if ENV_FILE.exists():
    load_dotenv(dotenv_path=ENV_FILE)
else:
    load_dotenv()  # Nạp từ môi trường hệ thống

# Cấu hình Hugging Face
HF_TOKEN = os.getenv("HF_TOKEN", "").strip() or None
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "").strip() or None
REMOTE_PREDICTION_FILE = os.getenv(
    "REMOTE_PREDICTION_FILE", "predictions/latest_predictions.csv"
).strip()

# Cấu hình Cache và cảnh báo
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "300"))  # 5 phút mặc định
STALE_THRESHOLD_HOURS = int(os.getenv("STALE_THRESHOLD_HOURS", "4"))  # 4 giờ

# Server host & port
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# Thư mục dữ liệu và static
DATA_DIR = BASE_DIR / "data"
LOCAL_PREDICTION_FILE = DATA_DIR / "latest_predictions.csv"
FRONTEND_DIR = BASE_DIR / "web" / "frontend"

# Định nghĩa các trường dữ liệu bắt buộc trong CSV dự báo
REQUIRED_COLUMNS = [
    "city",
    "region",
    "time",
    "prediction_time",
    "predicted_risk_level",
    "predicted_risk_name",
    "probability_level_0",
    "probability_level_1",
    "probability_level_2",
    "probability_level_3"
]

RISK_LABELS = {
    0: "Thấp",
    1: "Trung bình",
    2: "Cao",
    3: "Rất cao"
}

REGION_NAMES = {
    "Mien_Bac": "Miền Bắc",
    "Mien_Trung": "Miền Trung",
    "Mien_Nam": "Miền Nam"
}

# Nạp danh sách 63 tỉnh/thành từ src/provinces.py một cách an toàn
if str(BASE_DIR / "src") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "src"))

try:
    from provinces import CITIES_63
except Exception:
    # Dự phòng nếu không thể import từ src.provinces
    CITIES_63 = {}

