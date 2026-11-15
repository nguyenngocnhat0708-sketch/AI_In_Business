/**
 * Vietnam Air Quality Forecast Dashboard - Frontend Application
 * Tương tác API, Bản đồ Leaflet, Biểu đồ Chart.js, Bộ lọc và Tìm kiếm
 */

// Bảng màu chuẩn quy định theo mức độ rủi ro (0, 1, 2, 3)
const RISK_CONFIG = {
  0: { label: "Thấp", color: "#22c55e", bg: "#dcfce7", text: "#15803d", badgeClass: "badge-low" },
  1: { label: "Trung bình", color: "#eab308", bg: "#fef3c7", text: "#b45309", badgeClass: "badge-med" },
  2: { label: "Cao", color: "#f97316", bg: "#ffedd5", text: "#c2410c", badgeClass: "badge-high" },
  3: { label: "Rất cao", color: "#ef4444", bg: "#fee2e2", text: "#b91c1c", badgeClass: "badge-very-high" }
};

// State toàn cục của ứng dụng
const AppState = {
  allPredictions: [],
  filteredPredictions: [],
  summary: null,
  map: null,
  markers: {},
  chart: null,
  isRefreshing: false,
  autoRefreshTimer: null
};

// ==============================================================================
// KHỞI TẠO ỨNG DỤNG
// ==============================================================================

document.addEventListener("DOMContentLoaded", () => {
  initMap();
  setupEventListeners();
  fetchDashboardData();

  // Tự động tải lại dữ liệu từ Backend mỗi 5 phút (300,000 ms)
  AppState.autoRefreshTimer = setInterval(() => {
    fetchDashboardData(false);
  }, 300000);
});

// ==============================================================================
// 1. BẢN ĐỒ LEAFLET
// ==============================================================================

function initMap() {
  // Tọa độ trung tâm Việt Nam
  const vietnamCenter = [16.0, 107.5];
  
  AppState.map = L.map("vietnamMap", {
    center: vietnamCenter,
    zoom: 6,
    minZoom: 5,
    maxZoom: 12,
    zoomControl: true,
    scrollWheelZoom: true
  });

  // Sử dụng OpenStreetMap tile layer
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 18
  }).addTo(AppState.map);
}

function updateMapMarkers(predictions) {
  if (!AppState.map) return;

  // Xóa các marker cũ nếu có
  Object.values(AppState.markers).forEach(m => AppState.map.removeLayer(m));
  AppState.markers = {};

  predictions.forEach(p => {
    if (p.lat == null || p.lon == null) return;

    const riskInfo = RISK_CONFIG[p.predicted_risk_level] || RISK_CONFIG[0];

    // Tạo circle marker
    const marker = L.circleMarker([p.lat, p.lon], {
      radius: 9,
      fillColor: riskInfo.color,
      color: "#ffffff",
      weight: 2,
      opacity: 1,
      fillOpacity: 0.88
    });

    // Tạo Popup nội dung chi tiết
    const popupContent = `
      <div class="custom-popup">
        <div class="popup-title">${p.city}</div>
        <div class="popup-region">${p.region_name || p.region}</div>
        <div class="popup-risk-badge" style="background-color: ${riskInfo.bg}; color: ${riskInfo.text}">
          Rủi ro T+3: ${p.predicted_risk_name || riskInfo.label} (Mức ${p.predicted_risk_level})
        </div>
        
        <div class="popup-probs">
          <div style="font-weight: 600; margin-bottom: 4px;">Xác suất từng mức:</div>
          <div class="popup-prob-row">
            <span>Thấp:</span>
            <strong>${(p.probabilities.level_0 * 100).toFixed(1)}%</strong>
          </div>
          <div class="popup-prob-row">
            <span>Trung bình:</span>
            <strong>${(p.probabilities.level_1 * 100).toFixed(1)}%</strong>
          </div>
          <div class="popup-prob-row">
            <span>Cao:</span>
            <strong>${(p.probabilities.level_2 * 100).toFixed(1)}%</strong>
          </div>
          <div class="popup-prob-row">
            <span>Rất cao:</span>
            <strong>${(p.probabilities.level_3 * 100).toFixed(1)}%</strong>
          </div>
        </div>

        <div class="popup-time">
          <div>Đo dữ liệu: ${formatDateTime(p.time)}</div>
          <div style="color: #0284c7; font-weight: 600;">Dự báo T+3: ${formatDateTime(p.prediction_time)}</div>
        </div>
      </div>
    `;

    marker.bindPopup(popupContent);
    marker.addTo(AppState.map);
    AppState.markers[p.city] = marker;
  });
}

function focusProvince(cityName) {
  const marker = AppState.markers[cityName];
  if (marker && AppState.map) {
    const latLng = marker.getLatLng();
    AppState.map.setView(latLng, 9, { animate: true, duration: 1.0 });
    marker.openPopup();
    
    // Cuộn mượt lên vị trí bản đồ nếu trên màn hình nhỏ
    if (window.innerWidth < 1024) {
      document.getElementById("vietnamMap").scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }
}

// ==============================================================================
// 2. BIỂU ĐỒ CHART.JS (PHÂN BỐ RỦI RO TOÀN QUỐC)
// ==============================================================================

function initOrUpdateChart(summary) {
  const ctx = document.getElementById("riskDistributionChart").getContext("2d");
  const counts = summary.counts_by_risk_level || {};
  
  const dataValues = [
    counts["0"] || 0,
    counts["1"] || 0,
    counts["2"] || 0,
    counts["3"] || 0
  ];

  const labels = ["Thấp (0)", "Trung bình (1)", "Cao (2)", "Rất cao (3)"];
  const bgColors = [
    RISK_CONFIG[0].color,
    RISK_CONFIG[1].color,
    RISK_CONFIG[2].color,
    RISK_CONFIG[3].color
  ];

  if (AppState.chart) {
    AppState.chart.data.datasets[0].data = dataValues;
    AppState.chart.update();
    return;
  }

  AppState.chart = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels: labels,
      datasets: [{
        data: dataValues,
        backgroundColor: bgColors,
        borderWidth: 2,
        borderColor: "#ffffff"
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: "bottom",
          labels: {
            boxWidth: 12,
            font: { family: "Inter", size: 12 },
            padding: 15
          }
        },
        tooltip: {
          callbacks: {
            label: function(context) {
              const total = context.dataset.data.reduce((a, b) => a + b, 0);
              const val = context.raw;
              const pct = total > 0 ? ((val / total) * 100).toFixed(1) : 0;
              return ` ${context.label}: ${val} tỉnh (${pct}%)`;
            }
          }
        }
      },
      cutout: "65%"
    }
  });
}

// ==============================================================================
// 3. CẬP NHẬT GIAO DIỆN KPI VÀ THỐNG KÊ
// ==============================================================================

function updateKPIs(summary) {
  const total = summary.total_valid_cities || 0;
  const counts = summary.counts_by_risk_level || { "0": 0, "1": 0, "2": 0, "3": 0 };
  const pcts = summary.percentages_by_risk_level || { "0": 0, "1": 0, "2": 0, "3": 0 };

  document.getElementById("kpiTotalCities").textContent = total;
  document.getElementById("kpiTotalSub").textContent = `Phạm vi: Toàn quốc (${total}/63 tỉnh)`;

  // Mức Thấp
  document.getElementById("kpiRiskLow").textContent = counts["0"] || 0;
  document.getElementById("kpiRiskLowPct").textContent = `Chiếm ${pcts["0"] || 0}% số địa điểm`;

  // Mức Trung bình
  document.getElementById("kpiRiskMed").textContent = counts["1"] || 0;
  document.getElementById("kpiRiskMedPct").textContent = `Chiếm ${pcts["1"] || 0}% số địa điểm`;

  // Mức Cao & Rất cao
  const highTotal = (counts["2"] || 0) + (counts["3"] || 0);
  document.getElementById("kpiRiskHigh").textContent = highTotal;
  document.getElementById("kpiRiskHighBreakdown").textContent = `Cao: ${counts["2"] || 0} | Rất cao: ${counts["3"] || 0}`;

  // Cập nhật mốc thời gian
  if (summary.data_time) {
    document.getElementById("dataTimeDisplay").textContent = formatDateTime(summary.data_time);
  }
  if (summary.prediction_time) {
    document.getElementById("predTimeDisplay").textContent = formatDateTime(summary.prediction_time);
  }
}

function updateRegionalStats(summary) {
  const container = document.getElementById("regionStatsContainer");
  const regData = summary.counts_by_region || {};

  container.innerHTML = "";
  Object.keys(regData).forEach(key => {
    const reg = regData[key];
    const item = document.createElement("div");
    item.className = "region-card-item";
    item.innerHTML = `
      <div>
        <div class="region-name">${reg.name}</div>
        <div style="font-size: 0.75rem; color: #64748b;">Tổng số: ${reg.total} tỉnh/thành</div>
      </div>
      <div class="region-breakdown">
        <span class="mini-badge badge-low" title="Mức Thấp">${reg["0"]}</span>
        <span class="mini-badge badge-med" title="Mức Trung bình">${reg["1"]}</span>
        <span class="mini-badge badge-high" title="Mức Cao">${reg["2"]}</span>
        <span class="mini-badge badge-very-high" title="Mức Rất cao">${reg["3"]}</span>
      </div>
    `;
    container.appendChild(item);
  });
}

// ==============================================================================
// 4. BẢNG DỮ LIỆU & BỘ LỌC
// ==============================================================================

function renderTable(predictions) {
  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = "";

  if (!predictions || predictions.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="text-center loading-cell">Không tìm thấy địa điểm nào khớp với bộ lọc.</td></tr>`;
    document.getElementById("filteredCount").textContent = `Hiển thị 0 / ${AppState.allPredictions.length} tỉnh`;
    return;
  }

  document.getElementById("filteredCount").textContent = `Hiển thị ${predictions.length} / ${AppState.allPredictions.length} tỉnh/thành`;

  const fragment = document.createDocumentFragment();

  predictions.forEach(p => {
    const riskInfo = RISK_CONFIG[p.predicted_risk_level] || RISK_CONFIG[0];
    const tr = document.createElement("tr");

    tr.innerHTML = `
      <td class="city-cell">${p.city}</td>
      <td>${p.region_name || p.region}</td>
      <td>
        <span class="badge ${riskInfo.badgeClass}">
          ${p.predicted_risk_name || riskInfo.label} (${p.predicted_risk_level})
        </span>
      </td>
      <td>
        <div>${(p.probabilities.level_0 * 100).toFixed(1)}%</div>
        <div class="prob-bar-container"><div class="prob-bar-fill" style="width: ${p.probabilities.level_0 * 100}%; background-color: var(--risk-0-color)"></div></div>
      </td>
      <td>
        <div>${(p.probabilities.level_1 * 100).toFixed(1)}%</div>
        <div class="prob-bar-container"><div class="prob-bar-fill" style="width: ${p.probabilities.level_1 * 100}%; background-color: var(--risk-1-color)"></div></div>
      </td>
      <td>
        <div>${(p.probabilities.level_2 * 100).toFixed(1)}%</div>
        <div class="prob-bar-container"><div class="prob-bar-fill" style="width: ${p.probabilities.level_2 * 100}%; background-color: var(--risk-2-color)"></div></div>
      </td>
      <td>
        <div>${(p.probabilities.level_3 * 100).toFixed(1)}%</div>
        <div class="prob-bar-container"><div class="prob-bar-fill" style="width: ${p.probabilities.level_3 * 100}%; background-color: var(--risk-3-color)"></div></div>
      </td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="focusProvince('${p.city}')">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>
          </svg>
          Xem
        </button>
      </td>
    `;
    fragment.appendChild(tr);
  });

  tbody.appendChild(fragment);
}

function applyFiltersAndSort() {
  const searchTerm = document.getElementById("searchInput").value.trim().toLowerCase();
  const selectedRegion = document.getElementById("regionFilter").value;
  const selectedRisk = document.getElementById("riskFilter").value;
  const sortOrder = document.getElementById("sortOrder").value;

  let filtered = [...AppState.allPredictions];

  // 1. Tìm kiếm theo tên
  if (searchTerm) {
    filtered = filtered.filter(p => p.city.toLowerCase().includes(searchTerm));
  }

  // 2. Lọc theo vùng miền
  if (selectedRegion !== "ALL") {
    filtered = filtered.filter(p => p.region === selectedRegion);
  }

  // 3. Lọc theo mức rủi ro
  if (selectedRisk !== "ALL") {
    const riskInt = parseInt(selectedRisk, 10);
    filtered = filtered.filter(p => p.predicted_risk_level === riskInt);
  }

  // 4. Sắp xếp
  if (sortOrder === "risk_desc") {
    filtered.sort((a, b) => b.predicted_risk_level - a.predicted_risk_level || a.city.localeCompare(b.city, 'vi'));
  } else if (sortOrder === "risk_asc") {
    filtered.sort((a, b) => a.predicted_risk_level - b.predicted_risk_level || a.city.localeCompare(b.city, 'vi'));
  } else if (sortOrder === "name_asc") {
    filtered.sort((a, b) => a.city.localeCompare(b.city, 'vi'));
  }

  AppState.filteredPredictions = filtered;
  renderTable(filtered);
}

// ==============================================================================
// 5. GỌI API BACKEND VÀ XỬ LÝ DỮ LIỆU
// ==============================================================================

async function fetchDashboardData(isManual = false) {
  const statusPill = document.getElementById("connectionStatus");
  const statusText = document.getElementById("statusText");
  const refreshBtn = document.getElementById("refreshBtn");
  const icon = refreshBtn.querySelector(".icon-refresh");

  if (isManual) {
    icon.classList.add("spinning");
  }

  try {
    // 1. Gọi API lấy dự báo mới nhất
    const endpoint = isManual ? "/api/refresh" : "/api/predictions/latest";
    const method = isManual ? "POST" : "GET";
    
    const response = await fetch(endpoint, { method: method });
    
    if (!response.ok) {
      const errJson = await response.json().catch(() => ({}));
      throw new Error(errJson.detail?.message || errJson.message || `HTTP ${response.status}`);
    }

    const data = await response.json();
    const predictions = data.predictions || [];

    // Nếu dữ liệu trả về từ manual refresh thì cấu trúc có summary
    let summary = data.summary;
    if (!summary) {
      // Gọi thêm API summary nếu cần
      const sumRes = await fetch("/api/summary");
      if (sumRes.ok) {
        summary = await sumRes.json();
      }
    }

    AppState.allPredictions = predictions;
    AppState.summary = summary;

    // Cập nhật giao diện
    updateMapMarkers(predictions);
    if (summary) {
      initOrUpdateChart(summary);
      updateKPIs(summary);
      updateRegionalStats(summary);
      handleStaleAndWarnings(summary);
    }
    applyFiltersAndSort();

    // Cập nhật trạng thái kết nối
    statusPill.className = "status-pill status-online";
    statusText.textContent = "Trực tuyến";

  } catch (error) {
    console.error("Lỗi khi tải dữ liệu dự báo:", error);
    statusPill.className = "status-pill status-offline";
    statusText.textContent = "Lỗi kết nối";

    showAlert(
      `Không thể kết nối hoặc tải dữ liệu từ Backend: ${error.message}. ` +
      `Kiểm tra lại cấu hình HF_DATASET_REPO trong file .env.`,
      true
    );
  } finally {
    if (isManual) {
      setTimeout(() => {
        icon.classList.remove("spinning");
      }, 500);
    }
  }
}

function handleStaleAndWarnings(summary) {
  if (summary.is_stale || summary.warning) {
    const pill = document.getElementById("connectionStatus");
    pill.className = "status-pill status-stale";
    document.getElementById("statusText").textContent = "Dữ liệu cũ";
    
    const msg = summary.warning || "Dữ liệu dự báo hiện tại có thể chưa được cập nhật từ giờ gần nhất.";
    showAlert(msg, false);
  } else {
    hideAlert();
  }
}

function showAlert(message, isDanger = false) {
  const banner = document.getElementById("alertBanner");
  const msgEl = document.getElementById("alertMessage");
  banner.classList.remove("hidden");
  banner.className = isDanger ? "alert-banner alert-danger" : "alert-banner";
  msgEl.textContent = message;
}

function hideAlert() {
  document.getElementById("alertBanner").classList.add("hidden");
}

// ==============================================================================
// 6. TIỆN ÍCH VÀ SỰ KIỆN GIAO DIỆN
// ==============================================================================

function formatDateTime(isoString) {
  if (!isoString) return "--:-- --/--/----";
  try {
    const d = new Date(isoString);
    if (isNaN(d.getTime())) return isoString;
    const hours = String(d.getHours()).padStart(2, "0");
    const minutes = String(d.getMinutes()).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    const month = String(d.getMonth() + 1).padStart(2, "0");
    const year = d.getFullYear();
    return `${hours}:${minutes} ${day}/${month}/${year}`;
  } catch {
    return isoString;
  }
}

function setupEventListeners() {
  // Nút làm mới
  document.getElementById("refreshBtn").addEventListener("click", () => {
    fetchDashboardData(true);
  });

  // Tìm kiếm
  const searchInput = document.getElementById("searchInput");
  const clearBtn = document.getElementById("clearSearchBtn");
  
  searchInput.addEventListener("input", () => {
    if (searchInput.value.trim().length > 0) {
      clearBtn.classList.remove("hidden");
    } else {
      clearBtn.classList.add("hidden");
    }
    applyFiltersAndSort();
  });

  clearBtn.addEventListener("click", () => {
    searchInput.value = "";
    clearBtn.classList.add("hidden");
    applyFiltersAndSort();
  });

  // Bộ lọc vùng & rủi ro & sắp xếp
  document.getElementById("regionFilter").addEventListener("change", applyFiltersAndSort);
  document.getElementById("riskFilter").addEventListener("change", applyFiltersAndSort);
  document.getElementById("sortOrder").addEventListener("change", applyFiltersAndSort);

  // Đặt lại bản đồ
  document.getElementById("resetMapBtn").addEventListener("click", () => {
    if (AppState.map) {
      AppState.map.setView([16.0, 107.5], 6, { animate: true });
    }
  });

  // Đóng banner cảnh báo
  document.getElementById("closeAlertBtn").addEventListener("click", hideAlert);

  // Modal setup guidance
  const modal = document.getElementById("setupModal");
  document.getElementById("closeModalBtn").addEventListener("click", () => modal.classList.add("hidden"));
  document.getElementById("understandBtn").addEventListener("click", () => modal.classList.add("hidden"));
}

