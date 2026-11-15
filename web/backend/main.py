import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import FRONTEND_DIR, CACHE_TTL_SECONDS, HOST, PORT
from .data_service import data_service

logger = logging.getLogger("air_quality_web")

# Task nền tự động kiểm tra và làm mới dữ liệu
background_task = None


async def periodic_data_sync():
    """Vòng lặp chạy ngầm kiểm tra dữ liệu mới từ Hugging Face theo chu kỳ."""
    logger.info(f"Khởi động tiến trình đồng bộ dữ liệu nền (chu kỳ {CACHE_TTL_SECONDS}s)...")
    while True:
        try:
            await asyncio.sleep(CACHE_TTL_SECONDS)
            logger.info("Đang kiểm tra dữ liệu mới từ Hugging Face...")
            await data_service.refresh_data(force=True)
        except asyncio.CancelledError:
            logger.info("Tiến trình đồng bộ nền đã dừng.")
            break
        except Exception as e:
            logger.error(f"Lỗi trong quá trình đồng bộ nền: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi tạo dữ liệu lần đầu khi server khởi động
    logger.info("Khởi động FastAPI server...")
    try:
        await data_service.refresh_data(force=True)
    except Exception as e:
        logger.warning(f"Lần tải dữ liệu khởi động gặp lỗi: {e}")

    # Bật tiến trình kiểm tra định kỳ
    global background_task
    background_task = asyncio.create_task(periodic_data_sync())

    yield

    # Tắt tiến trình khi dừng server
    if background_task:
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
    logger.info("FastAPI server đã dừng.")


app = FastAPI(
    title="Vietnam Air Quality Forecast Dashboard API",
    description="API cung cấp kết quả dự báo chất lượng không khí và nguy cơ hô hấp T+3 cho 63 tỉnh/thành phố.",
    version="1.0.0",
    lifespan=lifespan
)

# Cấu hình CORS để frontend giao tiếp mượt mà
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# API ENDPOINTS
# ==============================================================================

@app.get("/api/health", summary="Kiểm tra trạng thái Backend và Cache")
async def get_health():
    """Trả về trạng thái hoạt động của Backend, tuổi thọ cache, và kết nối upstream."""
    return data_service.get_health_status()


@app.get("/api/predictions/latest", summary="Lấy dữ liệu dự báo mới nhất của 63 tỉnh/thành")
async def get_latest_predictions(force: bool = False):
    """
    Trả về dữ liệu dự báo 63 tỉnh/thành:
    - Tên tỉnh, vùng miền, tọa độ địa lý (lat/lon)
    - Thời điểm đo (time) và thời điểm dự báo (prediction_time = T+3)
    - Mức độ rủi ro (0: Thấp, 1: Trung bình, 2: Cao, 3: Rất cao)
    - Xác suất phân bố của 4 mức rủi ro
    """
    res = await data_service.get_latest_predictions(force_refresh=force)
    if res["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": res.get("error", "Không thể tải dữ liệu dự báo."),
                "hint": "Vui lòng cấu hình HF_DATASET_REPO và HF_TOKEN trong file .env để kết nối với Hugging Face Dataset."
            }
        )

    summary = res.get("summary") or {}
    return {
        "status": res["status"],
        "total_cities": len(res.get("records", [])),
        "data_time": summary.get("data_time"),
        "prediction_time": summary.get("prediction_time"),
        "prediction_horizon_hours": 3,
        "cached_at": summary.get("cached_at"),
        "is_stale": summary.get("is_stale", False),
        "data_source": summary.get("data_source", "none"),
        "warning": summary.get("warning"),
        "predictions": res.get("records", [])
    }


@app.get("/api/summary", summary="Lấy tổng quan KPI và phân bố rủi ro toàn quốc")
async def get_summary(force: bool = False):
    """Trả về tóm tắt thống kê: số lượng tỉnh ở từng mức rủi ro, tỷ lệ phần trăm, thống kê theo miền."""
    res = await data_service.get_summary(force_refresh=force)
    if res["status"] == "error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": res.get("error", "Không thể lấy thông tin tóm tắt."),
                "hint": "Kiểm tra cấu hình biến môi trường hoặc chạy pipeline dự báo để tạo kết quả."
            }
        )
    return res["summary"]


@app.post("/api/refresh", summary="Yêu cầu làm mới dữ liệu thủ công từ Hugging Face")
@app.get("/api/refresh", summary="Yêu cầu làm mới dữ liệu thủ công (GET)")
async def manual_refresh():
    """Bỏ qua thời gian cache và tải trực tiếp dữ liệu mới nhất từ Hugging Face."""
    res = await data_service.refresh_data(force=True)
    if res["status"] == "error":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "error",
                "message": res.get("error"),
                "hint": "Cập nhật biến môi trường HF_DATASET_REPO trong file .env."
            }
        )
    return {
        "status": "success",
        "message": f"Dữ liệu đã được làm mới thành công từ {res['summary'].get('data_source')}.",
        "summary": res.get("summary")
    }


# ==============================================================================
# PHỤC VỤ STATIC FILES VÀ FRONTEND
# ==============================================================================

if FRONTEND_DIR.exists():
    # Mount các tài nguyên tĩnh như CSS, JS
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", summary="Trang chủ Dashboard", include_in_schema=False)
    async def serve_index():
        index_file = FRONTEND_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse(
            status_code=404,
            content={"error": "Chưa tìm thấy index.html trong thư mục web/frontend."}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("web.backend.main:app", host=HOST, port=PORT, reload=True)

