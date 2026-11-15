import os
import sys
import unittest
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

# Đưa thư mục gốc vào sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from web.backend.main import app
from web.backend.data_service import data_service, DataValidationError
from web.backend.config import REQUIRED_COLUMNS, CITIES_63

TEST_FIXTURE = BASE_DIR / "tests" / "fixtures" / "sample_predictions.csv"


class TestAirQualityWebAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.fixture_df = pd.read_csv(TEST_FIXTURE)

    def test_01_health_endpoint(self):
        """Kiểm tra API /api/health phản hồi đúng cấu trúc JSON."""
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertIn("timestamp", data)
        self.assertIn("cache", data)
        self.assertIn("upstream", data)
        self.assertIn("configured", data["upstream"])

    def test_02_validation_valid_dataframe(self):
        """Kiểm tra hàm validate_dataframe với dữ liệu chuẩn 63 tỉnh."""
        is_valid, msg = data_service.validate_dataframe(self.fixture_df)
        self.assertTrue(is_valid)
        self.assertIsNone(msg)

    def test_03_validation_missing_columns(self):
        """Kiểm tra phát hiện thiếu cột bắt buộc."""
        bad_df = self.fixture_df.drop(columns=["predicted_risk_level"])
        is_valid, msg = data_service.validate_dataframe(bad_df)
        self.assertFalse(is_valid)
        self.assertIn("thiếu các cột bắt buộc", msg)

    def test_04_validation_invalid_risk_level(self):
        """Kiểm tra phát hiện mức rủi ro không thuộc [0, 1, 2, 3]."""
        bad_df = self.fixture_df.copy()
        bad_df.loc[0, "predicted_risk_level"] = 5  # Không hợp lệ
        is_valid, msg = data_service.validate_dataframe(bad_df)
        self.assertFalse(is_valid)
        self.assertIn("không hợp lệ", msg)

    def test_05_validation_duplicate_city(self):
        """Kiểm tra phát hiện trùng lặp tỉnh thành."""
        bad_df = pd.concat([self.fixture_df, self.fixture_df.iloc[[0]]], ignore_index=True)
        is_valid, msg = data_service.validate_dataframe(bad_df)
        self.assertFalse(is_valid)
        self.assertIn("Phát hiện trùng lặp", msg)

    def test_06_validation_probability_bounds(self):
        """Kiểm tra xác suất vượt quá [0, 1]."""
        bad_df = self.fixture_df.copy()
        bad_df.loc[0, "probability_level_0"] = 1.5
        is_valid, msg = data_service.validate_dataframe(bad_df)
        self.assertFalse(is_valid)
        self.assertIn("nằm ngoài [0, 1]", msg)

    def test_07_enrichment_coordinates(self):
        """Kiểm tra bổ sung tọa độ địa lý cho từng tỉnh/thành."""
        records = data_service._enrich_predictions(self.fixture_df)
        self.assertEqual(len(records), len(self.fixture_df))
        
        # Kiểm tra Hà Nội và TP.HCM
        hanoi = next((r for r in records if r["city"] == "Hà Nội"), None)
        self.assertIsNotNone(hanoi)
        self.assertEqual(hanoi["region"], "Mien_Bac")
        self.assertAlmostEqual(hanoi["lat"], 21.0285, places=2)
        self.assertAlmostEqual(hanoi["lon"], 105.8542, places=2)

        hcm = next((r for r in records if r["city"] == "TP. Hồ Chí Minh"), None)
        self.assertIsNotNone(hcm)
        self.assertEqual(hcm["region"], "Mien_Nam")
        self.assertAlmostEqual(hcm["lat"], 10.8231, places=2)

    def test_08_summary_calculation(self):
        """Kiểm tra tính toán KPI và tỷ lệ phân bố toàn quốc."""
        records = data_service._enrich_predictions(self.fixture_df)
        data_service._data_time = pd.to_datetime(self.fixture_df["time"].max())
        data_service._prediction_time = pd.to_datetime(self.fixture_df["prediction_time"].max())
        summary = data_service._compute_summary(records)

        self.assertEqual(summary["total_valid_cities"], 63)
        counts = summary["counts_by_risk_level"]
        total_counts = counts["0"] + counts["1"] + counts["2"] + counts["3"]
        self.assertEqual(total_counts, 63)
        self.assertEqual(counts["high_and_very_high"], counts["2"] + counts["3"])

        # Tổng tỷ lệ phần trăm xấp xỉ 100%
        pcts = summary["percentages_by_risk_level"]
        total_pct = sum(float(pcts[str(i)]) for i in range(4))
        self.assertAlmostEqual(total_pct, 100.0, delta=1.0)

    def test_09_api_endpoints_with_mocked_cache(self):
        """Kiểm tra API trả về dữ liệu đúng chuẩn khi cache đã được nạp."""
        # Nạp fixture vào cache của data_service
        data_service._raw_df = self.fixture_df
        data_service._cached_data = data_service._enrich_predictions(self.fixture_df)
        data_service._cached_at = pd.Timestamp.now(tz="UTC")
        data_service._data_time = pd.to_datetime(self.fixture_df["time"].max())
        data_service._prediction_time = pd.to_datetime(self.fixture_df["prediction_time"].max())
        data_service._data_source = "test_fixture"
        data_service._cached_summary = data_service._compute_summary(data_service._cached_data)

        # 1. Test /api/predictions/latest
        res_pred = self.client.get("/api/predictions/latest")
        self.assertEqual(res_pred.status_code, 200)
        data_pred = res_pred.json()
        self.assertEqual(data_pred["total_cities"], 63)
        self.assertEqual(data_pred["prediction_horizon_hours"], 3)
        self.assertEqual(len(data_pred["predictions"]), 63)
        self.assertIn("probabilities", data_pred["predictions"][0])

        # 2. Test /api/summary
        res_sum = self.client.get("/api/summary")
        self.assertEqual(res_sum.status_code, 200)
        data_sum = res_sum.json()
        self.assertEqual(data_sum["total_valid_cities"], 63)
        self.assertIn("counts_by_risk_level", data_sum)
        self.assertIn("counts_by_region", data_sum)

    def test_10_frontend_index_serving(self):
        """Kiểm tra trang web giao diện HTML được phục vụ tại root path '/'."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers.get("content-type", ""))
        self.assertIn("Vietnam Air Quality", response.text)


if __name__ == "__main__":
    unittest.main()

