# Sử dụng Python 3.11 slim tối ưu dung lượng
FROM python:3.11-slim

# Thiết lập biến môi trường không ghi file .pyc và xuất log ngay lập tức
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

# Cài đặt curl để hỗ trợ healthcheck nếu cần
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Sao chép và cài đặt các phụ thuộc nhẹ của Website
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

# Sao chép mã nguồn Backend, Frontend và metadata tỉnh/thành
COPY web/ ./web/
COPY src/provinces.py ./src/provinces.py

# Expose cổng ứng dụng
EXPOSE 8000

# Kiểm tra trạng thái container (Healthcheck)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/api/health || exit 1

# Khởi chạy ứng dụng FastAPI bằng Uvicorn
CMD ["uvicorn", "web.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

