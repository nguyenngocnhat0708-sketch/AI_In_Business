import asyncio
import logging
from datetime import datetime, timezone, timedelta
from io import StringIO
from typing import Dict, Any, Optional, Tuple, List
from zoneinfo import ZoneInfo

import pandas as pd
from huggingface_hub import hf_hub_download

from .config import (
    HF_TOKEN,
    HF_DATASET_REPO,
    REMOTE_PREDICTION_FILE,
    LOCAL_PREDICTION_FILE,
    CACHE_TTL_SECONDS,
    STALE_THRESHOLD_HOURS,
    REQUIRED_COLUMNS,
    RISK_LABELS,
    REGION_NAMES,
    CITIES_63,
)

logger = logging.getLogger("air_quality_web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

VN_TIMEZONE = ZoneInfo("Asia/Ho_Chi_Minh")

# Bản đồ tên chuẩn hóa sang tọa độ từ CITIES_63
PROVINCE_COORDINATES = {}
for city_id, info in CITIES_63.items():
    PROVINCE_COORDINATES[info["name"]] = {
        "lat": info["lat"],
        "lon": info["lon"],
        "region": info["region"],
        "region_name": REGION_NAMES.get(info["region"], info["region"]),
        "city_id": city_id
    }


class DataValidationError(Exception):
    """Lỗi khi dữ liệu CSV không đạt chuẩn kiểm định"""
    pass


class AirQualityDataService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._cached_data: Optional[List[Dict[str, Any]]] = None
        self._cached_summary: Optional[Dict[str, Any]] = None
        self._cached_at: Optional[datetime] = None
        self._data_time: Optional[datetime] = None
        self._prediction_time: Optional[datetime] = None
        self._data_source: str = "none"
        self._is_stale: bool = False
        self._last_error: Optional[str] = None
        self._warning: Optional[str] = None
        self._raw_df: Optional[pd.DataFrame] = None

    def validate_dataframe(self, df: pd.DataFrame) -> Tuple[bool, Optional[str]]:
        """Kiểm tra toàn diện tính hợp lệ của DataFrame dự báo."""
        if df is None or df.empty:
            return False, "Dữ liệu rỗng, không có bản ghi nào."

        # 1. Kiểm tra đủ các cột bắt buộc
        missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
        if missing_cols:
            return False, f"Dữ liệu thiếu các cột bắt buộc: {missing_cols}"

        # 2. Kiểm tra bản ghi trùng lặp tỉnh
        duplicates = df[df.duplicated(subset=["city"], keep=False)]["city"].tolist()
        if duplicates:
            return False, f"Phát hiện trùng lặp dữ liệu tại các tỉnh: {list(set(duplicates))}"

        # 3. Kiểm tra các giá trị khuyết ở các cột quan trọng
        critical_cols = ["city", "time", "prediction_time", "predicted_risk_level"]
        null_counts = df[critical_cols].isna().sum()
        has_nulls = null_counts[null_counts > 0]
        if not has_nulls.empty:
            return False, f"Phát hiện giá trị null ở các trường quan trọng: {has_nulls.to_dict()}"

        # 4. Kiểm tra nhãn mức độ rủi ro (phải thuộc {0, 1, 2, 3})
        try:
            risk_levels = df["predicted_risk_level"].astype(int)
            invalid_risks = df[~risk_levels.isin([0, 1, 2, 3])]["predicted_risk_level"].tolist()
            if invalid_risks:
                return False, f"Giá trị mức rủi ro không hợp lệ (phải là 0, 1, 2, 3): {invalid_risks}"
        except Exception as e:
            return False, f"Không thể chuyển đổi cột predicted_risk_level sang số nguyên: {e}"

        # 5. Kiểm tra giá trị xác suất (phải nằm trong khoảng 0.0 - 1.0)
        prob_cols = [f"probability_level_{i}" for i in range(4)]
        for col in prob_cols:
            try:
                probs = df[col].astype(float)
                if (probs < -0.01).any() or (probs > 1.01).any():
                    return False, f"Cột {col} chứa giá trị xác suất không hợp lệ (nằm ngoài [0, 1])."
            except Exception as e:
                return False, f"Cột xác suất {col} chứa dữ liệu không hợp lệ: {e}"

        return True, None

    def _enrich_predictions(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Bổ sung tọa độ lat/lon và format dữ liệu chuẩn cho Frontend."""
        records = []
        for _, row in df.iterrows():
            city_name = str(row["city"]).strip()
            risk_val = int(row["predicted_risk_level"])
            risk_name = RISK_LABELS.get(risk_val, str(row.get("predicted_risk_name", "")))
            
            geo_info = PROVINCE_COORDINATES.get(city_name, {
                "lat": None,
                "lon": None,
                "region": str(row.get("region", "")),
                "region_name": REGION_NAMES.get(str(row.get("region", "")), str(row.get("region", ""))),
                "city_id": ""
            })

            record = {
                "city": city_name,
                "region": str(row.get("region", geo_info["region"])),
                "region_name": geo_info["region_name"],
                "lat": geo_info["lat"],
                "lon": geo_info["lon"],
                "time": str(row["time"]),
                "prediction_time": str(row["prediction_time"]),
                "predicted_risk_level": risk_val,
                "predicted_risk_name": risk_name,
                "probabilities": {
                    "level_0": round(float(row.get("probability_level_0", 0.0)), 4),
                    "level_1": round(float(row.get("probability_level_1", 0.0)), 4),
                    "level_2": round(float(row.get("probability_level_2", 0.0)), 4),
                    "level_3": round(float(row.get("probability_level_3", 0.0)), 4),
                }
            }
            records.append(record)
        return records

    def _compute_summary(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Tính toán các chỉ số KPI, phân bố rủi ro theo toàn quốc và theo vùng miền."""
        total = len(records)
        counts = {0: 0, 1: 0, 2: 0, 3: 0}
        region_stats = {
            "Mien_Bac": {"name": "Miền Bắc", "total": 0, "0": 0, "1": 0, "2": 0, "3": 0},
            "Mien_Trung": {"name": "Miền Trung & Tây Nguyên", "total": 0, "0": 0, "1": 0, "2": 0, "3": 0},
            "Mien_Nam": {"name": "Miền Nam", "total": 0, "0": 0, "1": 0, "2": 0, "3": 0},
        }

        for r in records:
            lvl = r["predicted_risk_level"]
            counts[lvl] = counts.get(lvl, 0) + 1
            
            region_key = r.get("region", "")
            if region_key in region_stats:
                region_stats[region_key]["total"] += 1
                region_stats[region_key][str(lvl)] += 1

        percentages = {
            str(lvl): round((counts[lvl] / total * 100), 1) if total > 0 else 0.0
            for lvl in [0, 1, 2, 3]
        }

        # Kiểm tra độ cũ của dữ liệu
        is_stale = self._is_stale
        if self._data_time:
            now_vn = datetime.now(VN_TIMEZONE)
            dt = self._data_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=VN_TIMEZONE)
            elapsed_hours = (now_vn - dt).total_seconds() / 3600.0
            if elapsed_hours > STALE_THRESHOLD_HOURS:
                is_stale = True

        return {
            "total_valid_cities": total,
            "counts_by_risk_level": {
                "0": counts[0],
                "1": counts[1],
                "2": counts[2],
                "3": counts[3],
                "high_and_very_high": counts[2] + counts[3]
            },
            "percentages_by_risk_level": percentages,
            "counts_by_region": region_stats,
            "data_time": str(self._data_time) if self._data_time else None,
            "prediction_time": str(self._prediction_time) if self._prediction_time else None,
            "cached_at": self._cached_at.isoformat() if self._cached_at else None,
            "is_stale": is_stale,
            "data_source": self._data_source,
            "warning": self._warning,
            "last_error": self._last_error
        }

    def _fetch_from_huggingface(self) -> pd.DataFrame:
        """Tải file CSV từ Hugging Face Hub dataset repository."""
        if not HF_DATASET_REPO:
            raise ValueError("HF_DATASET_REPO chưa được cấu hình.")

        logger.info(f"Đang tải dự báo từ Hugging Face: {HF_DATASET_REPO}/{REMOTE_PREDICTION_FILE}")
        downloaded_path = hf_hub_download(
            repo_id=HF_DATASET_REPO,
            filename=REMOTE_PREDICTION_FILE,
            repo_type="dataset",
            token=HF_TOKEN or None,
        )
        return pd.read_csv(downloaded_path)

    def _fetch_from_local_file(self) -> Optional[pd.DataFrame]:
        """Đọc từ file local nếu có (dành cho môi trường offline/development)."""
        if LOCAL_PREDICTION_FILE.exists():
            logger.info(f"Đang đọc từ file local: {LOCAL_PREDICTION_FILE}")
            return pd.read_csv(LOCAL_PREDICTION_FILE)
        return None

    async def refresh_data(self, force: bool = False) -> Dict[str, Any]:
        """Tải và cập nhật dữ liệu với cơ chế cache và fallback an toàn."""
        async with self._lock:
            now = datetime.now(timezone.utc)

            # Kiểm tra cache còn hạn hay không
            if not force and self._cached_data is not None and self._cached_at is not None:
                age_seconds = (now - self._cached_at).total_seconds()
                if age_seconds < CACHE_TTL_SECONDS:
                    return {
                        "status": "cached",
                        "records": self._cached_data,
                        "summary": self._cached_summary
                    }

            # Tiến hành tải dữ liệu mới
            df: Optional[pd.DataFrame] = None
            source_used = "none"
            fetch_error_msg = None

            # Ưu tiên 1: Tải từ Hugging Face
            if HF_DATASET_REPO:
                try:
                    df = await asyncio.to_thread(self._fetch_from_huggingface)
                    source_used = "huggingface"
                except Exception as e:
                    fetch_error_msg = f"Không thể tải từ Hugging Face ({HF_DATASET_REPO}): {str(e)}"
                    logger.warning(fetch_error_msg)

            # Ưu tiên 2: Dự phòng file local nếu chưa có dữ liệu và HF thất bại
            if df is None:
                try:
                    df = await asyncio.to_thread(self._fetch_from_local_file)
                    if df is not None:
                        source_used = "local_file"
                except Exception as e:
                    logger.warning(f"Không thể đọc file local: {e}")

            # Xử lý kết quả tải
            if df is not None:
                is_valid, validation_msg = self.validate_dataframe(df)
                if is_valid:
                    # Chuyển đổi và lưu trữ
                    df["time"] = pd.to_datetime(df["time"], errors="coerce")
                    df["prediction_time"] = pd.to_datetime(df["prediction_time"], errors="coerce")
                    
                    self._raw_df = df
                    self._cached_data = self._enrich_predictions(df)
                    self._cached_at = now
                    self._data_time = df["time"].max()
                    self._prediction_time = df["prediction_time"].max()
                    self._data_source = source_used
                    self._is_stale = False
                    self._last_error = None
                    
                    if source_used == "local_file":
                        self._warning = (
                            "Đang sử dụng dữ liệu từ file lưu trữ cục bộ. "
                            "Hãy cấu hình HF_DATASET_REPO để nhận dữ liệu thời gian thực từ pipeline AI."
                        )
                    else:
                        self._warning = None

                    self._cached_summary = self._compute_summary(self._cached_data)
                    logger.info(f"Cập nhật dữ liệu thành công từ {source_used} ({len(self._cached_data)} tỉnh/thành).")
                    return {
                        "status": "success",
                        "records": self._cached_data,
                        "summary": self._cached_summary
                    }
                else:
                    logger.error(f"Dữ liệu tải về không hợp lệ: {validation_msg}")
                    fetch_error_msg = f"Dữ liệu tải về không hợp lệ: {validation_msg}"

            # Nếu tải thất bại nhưng đã có cache trước đó: GIỮ CACHE CŨ và đánh dấu STALE
            if self._cached_data is not None:
                self._is_stale = True
                self._warning = (
                    f"Không thể cập nhật dữ liệu mới nhất ({fetch_error_msg}). "
                    f"Hệ thống đang hiển thị bản dự báo đã lưu đệm từ {self._cached_at.strftime('%H:%M:%S %d/%m/%Y')}."
                )
                self._cached_summary = self._compute_summary(self._cached_data)
                logger.info("Giữ lại dữ liệu cache cũ do tải mới không thành công.")
                return {
                    "status": "stale_fallback",
                    "records": self._cached_data,
                    "summary": self._cached_summary
                }

            # Nếu tải thất bại và chưa từng có cache: Báo lỗi rõ ràng
            self._last_error = fetch_error_msg or (
                "HF_DATASET_REPO chưa được cấu hình trong biến môi trường và không tìm thấy dữ liệu cục bộ."
            )
            return {
                "status": "error",
                "error": self._last_error,
                "records": [],
                "summary": None
            }

    async def get_latest_predictions(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Lấy danh sách dự báo mới nhất."""
        res = await self.refresh_data(force=force_refresh)
        return res

    async def get_summary(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Lấy bản tóm tắt phân bố rủi ro."""
        res = await self.refresh_data(force=force_refresh)
        if res["status"] in ["success", "cached", "stale_fallback"]:
            return {
                "status": "success",
                "summary": res["summary"]
            }
        return {
            "status": "error",
            "error": res.get("error", "Không có dữ liệu dự báo khả dụng.")
        }

    def get_health_status(self) -> Dict[str, Any]:
        """Kiểm tra trạng thái hoạt động của Backend và Cache."""
        now = datetime.now(timezone.utc)
        cache_age = None
        if self._cached_at:
            cache_age = round((now - self._cached_at).total_seconds())

        status = "healthy"
        if self._cached_data is None:
            status = "uninitialized" if not self._last_error else "error"
        elif self._is_stale:
            status = "degraded"

        return {
            "status": status,
            "timestamp": now.isoformat(),
            "cache": {
                "has_data": self._cached_data is not None,
                "total_records": len(self._cached_data) if self._cached_data else 0,
                "cached_at": self._cached_at.isoformat() if self._cached_at else None,
                "cache_age_seconds": cache_age,
                "is_stale": self._is_stale,
                "source": self._data_source,
                "warning": self._warning,
                "last_error": self._last_error
            },
            "upstream": {
                "hf_dataset_repo": HF_DATASET_REPO,
                "configured": bool(HF_DATASET_REPO),
                "has_token": bool(HF_TOKEN),
                "remote_file": REMOTE_PREDICTION_FILE
            }
        }


# Singleton instance toàn hệ thống
data_service = AirQualityDataService()

