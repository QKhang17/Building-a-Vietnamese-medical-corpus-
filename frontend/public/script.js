/* ============================================================
   MedNLP Studio — Main Script v4.0
   Supports: Dashboard, Crawl UI, Dictionary UI, Labeling UI
============================================================ */

const API_BASE = "/api";

// ============================================================
// STATE
// ============================================================
let pollingInterval = null;
let isScraping      = false;
let autoScroll      = true;
let currentCrawlMode = "tamanh";

let currentArticlesData = [];
let currentArticleId    = null;
let currentArticleFilter = "all";
let isNerActive         = false;

let aiLabelArticlesData = [];
let currentAiArticleId  = null;
let currentAiFilter     = "all";

let expertToken = localStorage.getItem("mednlp_expert_token") || "";
let currentExpert = null;
let currentExpertArticleId = null;
let currentExpertArticle = null;
let currentExpertReviewMode = "manual";

// Color mapping for entity types
const ENTITY_COLORS = {
  DISEASE:   { cls: "disease",   label: "Bệnh lý",    icon: "🟢" },
  SYMPTOM:   { cls: "symptom",   label: "Triệu chứng",icon: "🔵" },
  TREATMENT: { cls: "treatment", label: "Điều trị",   icon: "🟣" },
  LAB_TEST:  { cls: "labtest",   label: "Xét nghiệm", icon: "🟡" },
  IMAGING:   { cls: "imaging",   label: "Hình ảnh",   icon: "🩵" },
  TRAD_MED:  { cls: "tradmed",   label: "Đông y",     icon: "🟠" },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function buildEntityTooltip(item, fallbackCategory) {
  const category = item.dictionary_type || fallbackCategory;
  let tooltip = category;
  if (item.code) tooltip += ` | Mã: ${item.code}`;
  if (item.label_vn && item.label_vn.toLowerCase() !== item.term.toLowerCase()) {
    tooltip += ` | ${item.label_vn}`;
  }
  return tooltip;
}

function entityClass(type) {
  return (ENTITY_COLORS[type] || { cls: "other" }).cls;
}
function entityLabel(type) {
  return (ENTITY_COLORS[type] || { label: type }).label;
}

const MATCH_BADGES = {
  exact:         { label: "EM", css: "matched-exact",         title: "Exact Match — khớp chính xác" },
  accentless:    { label: "AC", css: "matched-accentless",    title: "Accentless — khớp đầu vào không dấu" },
  alias:         { label: "AL", css: "matched-alias",         title: "Alias — khớp bí danh" },
  abbreviation:  { label: "AB", css: "matched-abbreviation",  title: "Abbreviation — khớp chữ viết tắt" },
  clinical_form: { label: "CF", css: "matched-clinical-form", title: "Clinical Form — khớp dạng lâm sàng" },
};

function renderMatchBadge(matchedBy) {
  const badge = MATCH_BADGES[matchedBy];
  if (!badge) return "";
  return `<span class="matched-badge ${badge.css}" title="${escapeHtml(badge.title)}">${badge.label}</span>`;
}

function conceptMatchKey(name, code) {
  const normalizedName = String(name || "").normalize("NFC").trim().toLocaleLowerCase("vi-VN");
  const normalizedCode = String(code || "").replace(/[.\s]/g, "").toUpperCase();
  return `${normalizedName}::${normalizedCode}`;
}

function hydrateSavedMatchMetadata(concepts, highlightedHtml) {
  const rows = Array.isArray(concepts) ? concepts.map(item => ({ ...item })) : [];
  if (!highlightedHtml || !rows.length) return rows;
  const template = document.createElement("template");
  template.innerHTML = highlightedHtml;
  const matchTypes = new Map();
  template.content.querySelectorAll("mark[data-matched-by]").forEach(mark => {
    const key = conceptMatchKey(mark.textContent, mark.dataset.code);
    if (!matchTypes.has(key)) matchTypes.set(key, mark.dataset.matchedBy);
  });
  return rows.map(item => {
    if (item.matched_by) return item;
    const matchedBy = matchTypes.get(conceptMatchKey(item.name, item.code));
    return matchedBy ? { ...item, matched_by: matchedBy } : item;
  });
}

function cloneConcepts(concepts) {
  return Array.isArray(concepts) ? concepts.map(item => ({ ...item })) : [];
}

// ============================================================
// NAVIGATION
// ============================================================
const SCREENS = {
  dashboard:    { title: "Dashboard",           sub: "Tổng quan hệ thống" },
  crawl:        { title: "Thu thập dữ liệu",    sub: "Crawler & nhật ký" },
  labeling:     { title: "Gán nhãn văn bản",    sub: "Xử lý & gán nhãn NER" },
  "ai-label":   { title: "AI Gán nhãn",         sub: "Gán nhãn thực thể y khoa bằng AI" },
  expert:       { title: "Duyệt nhãn chuyên gia", sub: "Đối chiếu thủ công, AI và ghi nhận xét" },
  logs:         { title: "Nhật ký thu thập",    sub: "Lịch sử thu thập" },
  "split-pdf":  { title: "Tách nội dung PDF",    sub: "Tách bài báo y học thành file văn bản" },
};

function switchScreen(name) {
  if (currentExpert && name !== "expert") name = "expert";
  // Deactivate all
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach(n => n.classList.remove("active"));

  // Activate target
  const screenEl = document.getElementById(`screen-${name}`);
  if (screenEl) screenEl.classList.add("active");
  const navEl = document.querySelector(`[data-screen="${name}"]`);
  if (navEl) navEl.classList.add("active");

  // Update topbar
  const info = SCREENS[name] || {};
  document.getElementById("topbarTitle").textContent = info.title || name;
  document.getElementById("topbarSub").textContent   = info.sub || "";

  // Persist current screen in URL hash (survives page reload)
  location.hash = name;

  // Lazy load
  if (name === "dashboard")  loadDashboard();
  if (name === "labeling")   loadData();
  if (name === "ai-label")   loadAiLabelData();
  if (name === "expert" && expertToken) loadExpertReviewItems();
  if (name === "logs")       loadCrawlLogs();
}


// ============================================================
// SERVER STATUS CHECK
// ============================================================
async function checkServerStatus() {
  const dot  = document.getElementById("statusDot");
  const text = document.getElementById("statusText");
  dot.className = "status-dot checking";
  text.textContent = "Đang kết nối...";
  try {
    const res = await fetch(`${API_BASE}/status`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      dot.className = "status-dot online";
      text.textContent = "Máy chủ hoạt động";

      // Nếu crawl đang chạy trên server mà frontend chưa polling → tự resume
      const status = await res.json();
      if (status.running && !pollingInterval) {
        isScraping = true;
        appendLog("🔄 Phát hiện crawler đang chạy — tự động kết nối lại...", "info");
        document.getElementById("btnScrape").disabled = true;
        document.getElementById("btnStopScrape").style.display = "";
        document.getElementById("crawlModeTamanh").disabled = true;
        document.getElementById("crawlModePdf").disabled = true;
        _startPolling();
      }
    } else throw new Error();
  } catch {
    dot.className = "status-dot offline";
    text.textContent = "Mất kết nối";
  }
}

// ============================================================
// TOAST NOTIFICATIONS
// ============================================================
function showToast(msg, type = "info", duration = 3000) {
  const container = document.getElementById("toastContainer");
  const icons = { success: "✅", error: "❌", info: "ℹ️" };
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${icons[type] || "ℹ️"}</span><span>${msg}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = "toastOut 0.3s ease forwards";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// ============================================================
// DASHBOARD
// ============================================================
async function loadDashboard() {
  await Promise.all([
    loadKPIs(),
    loadTopConcepts(""),
    loadRecentArticles(),
  ]);
}

async function loadKPIs() {
  try {
    const [articles, concepts] = await Promise.all([
      fetch(`${API_BASE}/articles`, { cache: "no-store" }).then(r => r.json()),
      fetch(`${API_BASE}/top-concepts?limit=100`).then(r => r.json()),
    ]);

    // Một bài được tính là đã gán nhãn nếu đã lưu kết quả thủ công hoặc AI.
    const labeled = articles.filter(a => a.is_labeled || a.ai_labeled).length;

    animateCount("kpi-articles", articles.length);
    animateCount("kpi-labeled",  labeled);
    animateCount("kpi-concepts", concepts.length > 0
      ? concepts.reduce((s, c) => s + (c.frequency || 0), 0) : 0);

    document.getElementById("kpi-articles-sub").textContent =
      `${labeled} đã gán nhãn / ${articles.length} tổng`;

    // Draw donut
    buildDonut(concepts);

    currentArticlesData = articles;
  } catch(e) {
    console.warn("KPI load error:", e);
  }
}

function animateCount(id, target) {
  const el = document.getElementById(id);
  if (!el) return;
  const duration = 800;
  const start = Date.now();
  const startVal = 0;
  function step() {
    const p = Math.min((Date.now() - start) / duration, 1);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(startVal + (target - startVal) * eased).toLocaleString("vi-VN");
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

let currentConceptFilter = "";
let allConceptsCache = [];

async function loadTopConcepts(label) {
  currentConceptFilter = label;
  const container = document.getElementById("barChartContainer");
  if (!container) return;
  try {
    const url = label
      ? `${API_BASE}/top-concepts?limit=10&label=${encodeURIComponent(label)}`
      : `${API_BASE}/top-concepts?limit=10`;
    const data = await fetch(url).then(r => r.json());
    allConceptsCache = data;
    renderBarChart(data);
  } catch {
    container.innerHTML = `<div class="chart-empty">Không thể tải dữ liệu</div>`;
  }
}

function filterConcepts(label) {
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  const map = { "": "filter-all", "DISEASE": "filter-disease", "SYMPTOM": "filter-symptom" };
  document.getElementById(map[label] || "filter-all")?.classList.add("active");
  loadTopConcepts(label);
}

function renderBarChart(data) {
  const container = document.getElementById("barChartContainer");
  if (!container) return;
  if (!data || data.length === 0) {
    container.innerHTML = `<div class="chart-empty">Không có dữ liệu</div>`;
    return;
  }
  const max = Math.max(...data.map(d => d.frequency || 0));
  container.innerHTML = data.map((d, i) => {
    const pct = max > 0 ? (d.frequency / max) * 100 : 0;
    const col = ENTITY_COLORS[d.concept_type] || { cls: "other" };
    return `
      <div class="bar-row" style="animation-delay:${i * 0.05}s">
        <div class="bar-label" title="${d.concept_name}">${d.concept_name}</div>
        <div class="bar-track">
          <div class="bar-fill" style="width:${pct}%; background: ${barColor(d.concept_type)};"></div>
        </div>
        <div class="bar-count">${d.frequency}</div>
      </div>`;
  }).join("");
}

function barColor(type) {
  const colors = {
    DISEASE:   "linear-gradient(90deg,#4ade80,#86efac)",
    SYMPTOM:   "linear-gradient(90deg,#818cf8,#a5b4fc)",
    TREATMENT: "linear-gradient(90deg,#c084fc,#d8b4fe)",
    LAB_TEST:  "linear-gradient(90deg,#fbbf24,#fde68a)",
    IMAGING:   "linear-gradient(90deg,#2dd4bf,#99f6e4)",
    TRAD_MED:  "linear-gradient(90deg,#fb923c,#fdba74)",
  };
  return colors[type] || "linear-gradient(90deg, var(--primary), var(--accent))";
}

function buildDonut(concepts) {
  const canvas = document.getElementById("donutCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = 200, H = 200, R = 80, r = 50;

  // Count by type
  const counts = {};
  concepts.forEach(c => {
    counts[c.concept_type] = (counts[c.concept_type] || 0) + (c.frequency || 1);
  });

  const palette = {
    DISEASE:   "#4ade80",
    SYMPTOM:   "#818cf8",
    TREATMENT: "#c084fc",
    LAB_TEST:  "#fbbf24",
    IMAGING:   "#2dd4bf",
    TRAD_MED:  "#fb923c",
  };

  const entries = Object.entries(counts);
  const total   = entries.reduce((s, [,v]) => s + v, 0);

  ctx.clearRect(0, 0, W, H);

  if (total === 0) {
    ctx.fillStyle = "#1e2840";
    ctx.beginPath(); ctx.arc(W/2, H/2, R, 0, Math.PI*2); ctx.fill();
    ctx.clearRect(W/2-r, H/2-r, r*2, r*2);
    ctx.beginPath(); ctx.arc(W/2, H/2, r, 0, Math.PI*2); ctx.fill();
    return;
  }

  let startAngle = -Math.PI / 2;
  entries.forEach(([type, count]) => {
    const angle = (count / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(W/2, H/2);
    ctx.arc(W/2, H/2, R, startAngle, startAngle + angle);
    ctx.closePath();
    ctx.fillStyle = palette[type] || "#64748b";
    ctx.fill();
    startAngle += angle;
  });

  // Center hole
  ctx.beginPath(); ctx.arc(W/2, H/2, r, 0, Math.PI*2);
  ctx.fillStyle = "#141b2d"; ctx.fill();

  // Legend
  const legend = document.getElementById("donutLegend");
  legend.innerHTML = entries.slice(0, 6).map(([type, count]) => {
    const pct = ((count / total) * 100).toFixed(1);
    return `
      <div class="legend-row">
        <div class="legend-dot" style="background:${palette[type] || "#64748b"}"></div>
        <span class="legend-row-label">${entityLabel(type)}</span>
        <span class="legend-row-pct">${pct}%</span>
      </div>`;
  }).join("");
}

async function loadRecentArticles() {
  const el = document.getElementById("recentArticles");
  try {
    const data = await fetch(`${API_BASE}/articles`, { cache: "no-store" }).then(r => r.json());
    const recent = data.slice(0, 8);
    if (recent.length === 0) {
      el.innerHTML = `<div class="list-placeholder">Chưa có bài báo nào</div>`;
      return;
    }
    el.innerHTML = recent.map((a, i) => {
      const manualLabeled = Boolean(a.is_labeled || a.highlighted_html);
      const aiLabeled = Boolean(a.ai_labeled);
      const isLabeled = manualLabeled || aiLabeled;
      const statusText = manualLabeled && aiLabeled
        ? "Thủ công + AI"
        : (aiLabeled ? "Đã gán nhãn AI" : (manualLabeled ? "Đã gán nhãn" : "Chưa xử lý"));
      return `
        <div class="recent-item" onclick="switchScreen('labeling'); setTimeout(()=>selectArticle(${a.id}),300)">
          <div class="recent-num">${i + 1}</div>
          <div class="recent-info">
            <div class="recent-title">${a.title || "Không có tiêu đề"}</div>
            <div class="recent-meta">${a.publication_year || "—"} · ${(a.authors || "").split(",")[0]}</div>
          </div>
          <span class="recent-badge ${isLabeled ? "badge-labeled" : "badge-unlabeled"}">
            ${statusText}
          </span>
        </div>`;
    }).join("");
  } catch {
    el.innerHTML = `<div class="list-placeholder">Không thể tải dữ liệu</div>`;
  }
}

// ============================================================
// CRAWL UI
// ============================================================
const CRAWL_MODE_CONFIG = {
  tamanh: {
    url: "https://tamanhhospital.vn/",
    title: "🌐 Crawler hỏi đáp y khoa",
    sub: "Tâm Anh Hospital — chỉ thu thập Q&A công khai",
    presetLabel: "Nguồn mặc định:",
  },
  pdf: {
    url: "https://tapchinghiencuuyhoc.vn",
    title: "📄 Crawler tạp chí & file PDF",
    sub: "Thu thập bài báo OJS, tóm tắt, file TXT và tải PDF nếu có",
    presetLabel: "Nguồn tạp chí:",
  },
};

function setCrawlMode(mode, resetUrl = true, force = false) {
  if (!CRAWL_MODE_CONFIG[mode]) return;
  if (isScraping && !force) {
    showToast("Hãy dừng crawler trước khi chuyển chế độ", "error");
    return;
  }
  currentCrawlMode = mode;
  const config = CRAWL_MODE_CONFIG[mode];
  document.getElementById("crawlModeTamanh").classList.toggle("active", mode === "tamanh");
  document.getElementById("crawlModePdf").classList.toggle("active", mode === "pdf");
  document.getElementById("crawlConfigTitle").textContent = config.title;
  document.getElementById("crawlConfigSub").textContent = config.sub;
  document.getElementById("presetSourceLabel").textContent = config.presetLabel;
  document.getElementById("tamanhPresetSources").style.display = mode === "tamanh" ? "flex" : "none";
  document.getElementById("pdfPresetSources").style.display = mode === "pdf" ? "flex" : "none";
  document.getElementById("prog-departments-label").textContent = mode === "tamanh" ? "Chuyên khoa" : "Năm xử lý";
  document.getElementById("prog-files-label").textContent = mode === "tamanh" ? "File TXT" : "PDF / TXT";
  if (resetUrl) document.getElementById("targetUrl").value = config.url;
  if (!isScraping) {
    document.getElementById("prog-departments").textContent = mode === "tamanh" ? "0 / 0" : "—";
    document.getElementById("prog-files").textContent = mode === "tamanh" ? "0" : "0 / 0";
  }
}

async function refreshDashboardAfterLabeling() {
  await Promise.all([
    loadKPIs(),
    loadTopConcepts(currentConceptFilter),
    loadRecentArticles(),
  ]);
}

function setUrl(url) {
  document.getElementById("targetUrl").value = url;
}

function clearLog() {
  document.getElementById("logTerminal").innerHTML =
    `<div class="log-placeholder"><span class="log-cursor">█</span> Log đã được xóa.</div>`;
}

function toggleAutoScroll() {
  autoScroll = !autoScroll;
  document.getElementById("autoScrollBtn").textContent = autoScroll ? "↓ Tự cuộn" : "↑ Tắt cuộn";
}

function appendLog(msg, type = "") {
  const term = document.getElementById("logTerminal");
  const placeholder = term.querySelector(".log-placeholder");
  if (placeholder) placeholder.remove();

  const now  = new Date().toLocaleTimeString("vi-VN");
  const line = document.createElement("div");
  line.className = `log-line ${type}`;

  // Color based on content heuristics
  let lineType = type;
  if (!lineType) {
    if (/lỗi|error|fail|exception/i.test(msg)) lineType = "error";
    else if (/tải file pdf thành công|thành công|saved|lưu|✅|done/i.test(msg))  lineType = "success";
    else if (/cảnh báo|warn|skip|bỏ qua|trùng/i.test(msg)) lineType = "warning";
    else if (/bắt đầu|start|kết nối|connect|đang/i.test(msg)) lineType = "info";
  }
  line.className = `log-line ${lineType}`;
  line.innerHTML = `<span class="log-ts">[${now}]</span> ${msg}`;
  term.appendChild(line);

  if (autoScroll) term.scrollTop = term.scrollHeight;
}

function updateProgressUI(s) {
  if (s.source_type) {
    setCrawlMode(s.source_type === "journal_pdf" ? "pdf" : "tamanh", false, true);
  }
  const saved   = s.success || 0;
  const dup     = s.duplicates || 0;
  const skip    = s.skipped || 0;
  const done    = saved + dup + skip;
  // Giả sử mỗi năm có khoảng 100 bài để chạy phần trăm tương đối, hoặc đơn giản để 100% nếu không biết tổng
  const total   = s.total_urls || done; 
  const pct     = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;

  document.getElementById("prog-total").textContent = s.total_urls || total || 0;
  document.getElementById("prog-saved").textContent = saved;
  document.getElementById("prog-dup").textContent   = dup;
  document.getElementById("prog-skip").textContent  = skip;
  if (currentCrawlMode === "pdf") {
    document.getElementById("prog-total").textContent = s.articles_discovered || s.total_urls || 0;
    document.getElementById("prog-saved").textContent = s.articles_saved || saved;
    document.getElementById("prog-dup").textContent = s.articles_existing || dup;
    document.getElementById("prog-skip").textContent = s.unresolved || skip;
    document.getElementById("prog-departments").textContent = `${s.issues_found || 0} tập · ${s.current_year || "—"}`;
    document.getElementById("prog-files").textContent = `${s.pdf_saved || 0} PDF / ${s.files_created || 0} TXT`;
    const phase = document.getElementById("crawlPhase");
    const phaseLabels = {
      discovery: "Giai đoạn 1/3: Quét danh sách tập và URL bài",
      initial: "Giai đoạn 2/3: Thu thập dữ liệu lần đầu",
      reconciliation: "Giai đoạn 3/3: Quét đối soát và tải bù",
      done: "Đã hoàn tất đối soát",
    };
    phase.textContent = phaseLabels[s.phase] || "";
    phase.style.display = phase.textContent ? "block" : "none";
  } else {
    document.getElementById("prog-departments").textContent =
      `${s.departments_completed || 0} / ${s.departments_total || 0}`;
    document.getElementById("prog-files").textContent = s.files_created || 0;
  }


  if (s.current_url) {
    const department = s.current_department ? `${s.current_department} — ` : "";
    document.getElementById("progressMsg").textContent = `Đang xử lý: ${department}${s.current_url}`;
  }

  const badge = document.getElementById("crawlStateBadge");
  if (s.running) {
    badge.textContent = "⚙️ Đang chạy";
    badge.className   = "crawl-state-badge running";
    document.getElementById("crawl-badge").style.display = "";
  } else if (s.error) {
    badge.textContent = "❌ Lỗi";
    badge.className   = "crawl-state-badge error";
    document.getElementById("crawl-badge").style.display = "none";
  } else if (s.done) {
    badge.textContent = "✅ Hoàn thành";
    badge.className   = "crawl-state-badge done";
    document.getElementById("crawl-badge").style.display = "none";
  } else {
    badge.textContent = "Chờ";
    badge.className   = "crawl-state-badge";
    document.getElementById("crawl-badge").style.display = "none";
  }
}

function _showSummaryBox(summary) {
  const box = document.getElementById("summaryBox");
  document.getElementById("sum-success").textContent = summary.success    || 0;
  document.getElementById("sum-dup").textContent     = summary.duplicates || 0;
  document.getElementById("sum-skip").textContent    = summary.skipped    || 0;
  const journalDetails = document.getElementById("journalSummaryDetails");
  const isJournal = Array.isArray(summary.issue_details);
  journalDetails.style.display = isJournal ? "block" : "none";
  if (isJournal) {
    document.getElementById("sum-issues").textContent = summary.issues_found || summary.issue_details.length;
    document.getElementById("sum-discovered").textContent = summary.articles_discovered || 0;
    document.getElementById("sum-repaired").textContent = summary.articles_repaired || 0;
    document.getElementById("sum-unresolved").textContent = summary.unresolved || 0;
    document.getElementById("issueCountTable").innerHTML = summary.issue_details.length
      ? `<table style="width:100%; border-collapse:collapse; font-size:12px">
          <thead><tr><th style="text-align:left">Năm</th><th style="text-align:left">Tập/số</th><th style="text-align:right">Bài</th></tr></thead>
          <tbody>${summary.issue_details.map(issue => `<tr>
            <td>${issue.year || "—"}</td>
            <td title="${escapeHtml(issue.url || "")}">${escapeHtml(issue.title || "—")}</td>
            <td style="text-align:right">${issue.articles || 0}</td>
          </tr>`).join("")}</tbody>
        </table>`
      : "Không có tập nào trong khoảng năm đã chọn.";
  }
  box.style.display = "block";
}

async function startScraping() {
  const btn  = document.getElementById("btnScrape");
  const stop = document.getElementById("btnStopScrape");
  const url   = document.getElementById("targetUrl").value.trim();
  const sYear = parseInt(document.getElementById("startYear").value);
  const eYear = parseInt(document.getElementById("endYear").value);

  if (!url)          { showToast("Vui lòng nhập URL tên miền đích!", "error"); return; }
  if (isNaN(sYear) || isNaN(eYear)) { showToast("Vui lòng nhập năm hợp lệ!", "error"); return; }
  if (sYear > eYear) { showToast("Năm bắt đầu phải ≤ năm kết thúc!", "error"); return; }

  isScraping = true;
  document.getElementById("crawlModeTamanh").disabled = true;
  document.getElementById("crawlModePdf").disabled = true;
  btn.disabled = true;
  btn.innerHTML = `<span class="btn-icon">⚙️</span> Đang khởi động...`;
  stop.style.display = "";
  document.getElementById("summaryBox").style.display = "none";

  appendLog(`🚀 Bắt đầu thu thập: ${url} (${sYear}–${eYear})`, "info");
  updateProgressUI({ running: true });

  try {
    const res = await fetch(`${API_BASE}/scrape`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_url: url, start_year: sYear, end_year: eYear })
    });

    if (res.status === 409) {
      const err = await res.json();
      appendLog(`⚠️ ${err.detail}`, "warning");
      showToast(err.detail, "error");
      resetScrapeBtn();
      // Nếu đang chạy rồi → tự động bắt đầu polling để hiển thị log
      _startPolling();
      return;
    }

    if (!res.ok) throw new Error("Lỗi máy chủ");

    appendLog("✅ Lệnh đã được gửi, crawler đang chạy nền...", "success");
    _startPolling();

  } catch (err) {
    appendLog(`❌ Lỗi kết nối: ${err.message}`, "error");
    showToast("Không thể kết nối đến máy chủ", "error");
    resetScrapeBtn();
  }
}

/** Bắt đầu polling vòng lặp lấy log từ backend mỗi 2s */
function _startPolling() {
  let lastLogCount = 0;
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(async () => {
    try {
      const s = await fetch(`${API_BASE}/status`).then(r => r.json());

      // Cập nhật số liệu tiến độ
      updateProgressUI(s);

      // Append các log mới
      if (s.log_messages && s.log_messages.length > lastLogCount) {
        const newLogs = s.log_messages.slice(lastLogCount);
        newLogs.forEach(msg => appendLog(msg));
        lastLogCount = s.log_messages.length;
      }

      // Chỉ dừng polling khi done=true (crawl thực sự kết thúc)
      if (s.done) {
        clearInterval(pollingInterval); pollingInterval = null;
        if (s.error) {
          appendLog(`❌ Lỗi: ${s.error}`, "error");
          showToast("Crawler gặp lỗi!", "error");
        } else {
          appendLog("✅ Thu thập hoàn thành! Đang làm mới dữ liệu...", "success");
          showToast("Thu thập dữ liệu hoàn thành!", "success");
          _showSummaryBox(s.summary || {});
          updateProgressUI(s);
          setTimeout(() => {
            loadData();
            loadDashboard();
            loadCrawlLogs();
            appendLog(`📊 Tổng kết: ✅ ${s.summary?.success||0} lưu · 🔁 ${s.summary?.duplicates||0} trùng · ⏭ ${s.summary?.skipped||0} bỏ qua`, "info");
          }, 1500);
        }
        resetScrapeBtn();
      } else if (s.running) {
        // Đảm bảo nút Stop hiển thị khi đang chạy
        document.getElementById("btnScrape").disabled = true;
        document.getElementById("btnStopScrape").style.display = "";
      }
    } catch {}
  }, 2000);
}

function stopScraping() {
  fetch(`${API_BASE}/scrape/stop`, { method: 'POST' }).catch(() => {});
  clearInterval(pollingInterval); pollingInterval = null;
  isScraping = false;
  appendLog("⏹ Đã gửi lệnh dừng crawler", "warning");
  resetScrapeBtn();
}

function resetScrapeBtn() {
  isScraping = false;
  const btn  = document.getElementById("btnScrape");
  const stop = document.getElementById("btnStopScrape");
  btn.disabled = false;
  btn.innerHTML = `<span class="btn-icon">🚀</span> Bắt đầu thu thập`;
  stop.style.display = "none";
  document.getElementById("crawlModeTamanh").disabled = false;
  document.getElementById("crawlModePdf").disabled = false;
}

// ============================================================
// LABELING UI
// ============================================================
let articleFilter = "all";

function setArticleFilter(filter, btn) {
  articleFilter = filter;
  document.querySelectorAll(".filter-pill").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  renderArticleList();
}

async function loadData() {
  if (currentArticlesData && currentArticlesData.length > 0) {
    renderArticleList();
    return;
  }
  const query = (document.getElementById("searchInput")?.value || "").trim();
  const scroll = document.getElementById("articleListScroll");
  if (scroll) scroll.innerHTML = `<div class="list-placeholder">Đang tải...</div>`;
  try {
    const url  = query
      ? `${API_BASE}/articles?q=${encodeURIComponent(query)}`
      : `${API_BASE}/articles`;
    const data = await fetch(url).then(r => r.json());
    currentArticlesData = Array.isArray(data) ? data : [];
    renderArticleList();
  } catch {
    if (scroll) scroll.innerHTML = `<div class="list-placeholder">Lỗi kết nối máy chủ</div>`;
  }
}

function renderArticleList() {
  const scroll = document.getElementById("articleListScroll");
  const info   = document.getElementById("pageInfo");
  if (!scroll) return;

  let data = currentArticlesData;
  if (articleFilter === "labeled")   data = data.filter(a =>  a.is_labeled || a.highlighted_html);
  if (articleFilter === "unlabeled") data = data.filter(a => !a.is_labeled && !a.highlighted_html);

  if (info) info.textContent = `${data.length} bài báo`;

  if (data.length === 0) {
    scroll.innerHTML = `<div class="list-placeholder">Không tìm thấy bài báo</div>`;
    return;
  }

  scroll.innerHTML = data.map(a => {
    const isLabeled = a.is_labeled || !!a.highlighted_html;
    const isActive  = a.id === currentArticleId;
    return `
      <div class="article-list-item ${isActive ? "active" : ""}" onclick="selectArticle(${a.id})">
        <div class="ali-title">${a.title || "Không có tiêu đề"}</div>
        <div class="ali-authors">${a.authors || "Không rõ tác giả"}</div>
        <div class="ali-meta">
          <span class="ali-dot ${isLabeled ? "labeled" : "unlabeled"}"></span>
          <span>${a.publication_year || "—"}</span>
          <span>·</span>
          <span>${isLabeled ? "Đã gán nhãn" : "Chưa xử lý"}</span>
        </div>
      </div>`;
  }).join("");
}

function restoreSavedAnnotation(article) {
  article.highlighted_html = article._savedHighlightedHtml || null;
  article.matched_concepts = cloneConcepts(article._savedMatchedConcepts);
  article._hasUnsavedNerPreview = false;
}

function renderArticleAnnotationState(article) {
  const btnNer = document.getElementById("btnNer");
  const btnSave = document.getElementById("btnSaveNer");
  const nerStatus = document.getElementById("nerStatus");
  const entPanel = document.getElementById("entitiesPanel");
  const legend = document.getElementById("entityLegend");
  const textBody = document.getElementById("textBody");
  const concepts = article.matched_concepts || [];
  const hasSavedResult = Boolean(article._savedHighlightedHtml);
  const hasPreview = Boolean(article._hasUnsavedNerPreview);

  btnNer.disabled = false;
  btnNer.className = hasPreview ? "btn-ner active" : "btn-ner";
  btnNer.textContent = hasPreview
    ? "Hủy xem trước"
    : (hasSavedResult ? "Chạy lại luật" : "Bật gán nhãn");
  btnSave.style.display = hasPreview ? "" : "none";

  if (article.highlighted_html) {
    textBody.innerHTML = article.highlighted_html;
  } else {
    textBody.innerHTML = article.abstract
      ? escapeHtml(article.abstract).replaceAll("\n", "<br>")
      : "<em>Không có nội dung tóm tắt.</em>";
  }

  isNerActive = Boolean(article.highlighted_html);
  legend.style.display = concepts.length ? "flex" : "none";
  entPanel.style.display = concepts.length ? "" : "none";
  if (concepts.length) renderEntities(concepts);

  if (hasPreview) {
    nerStatus.textContent = concepts.length
      ? `Xem trước luật mới: ${concepts.length} thực thể — chưa lưu`
      : "Xem trước luật mới: không có thực thể — chưa lưu";
  } else if (hasSavedResult) {
    nerStatus.textContent = "✅ Kết quả đã lưu";
  } else {
    nerStatus.textContent = "";
  }
}

async function selectArticle(id) {
  const previous = currentArticlesData.find(a => a.id === currentArticleId);
  if (previous && previous.id !== id && previous._hasUnsavedNerPreview) {
    restoreSavedAnnotation(previous);
    showToast("Đã hủy bản xem trước chưa lưu của bài trước", "info");
  }

  currentArticleId = id;
  isNerActive = false;
  renderArticleList();

  const article = currentArticlesData.find(a => a.id === id);
  if (!article) return;

  document.getElementById("viewerPlaceholder").style.display = "none";
  document.getElementById("viewerContent").style.display = "flex";
  document.getElementById("viewerContent").style.flexDirection = "column";
  const textBody = document.getElementById("textBody");

  if (!article._loaded) {
    textBody.innerHTML = `<em style='color:var(--text-3)'>Đang tải nội dung...</em>`;
    try {
      const response = await fetch(`${API_BASE}/articles/${id}`);
      const detail = await response.json();
      if (!response.ok) throw new Error(detail.detail || "Không tải được bài báo");
      article.abstract = detail.abstract || null;
      article.highlighted_html = detail.highlighted_html || null;
      article.matched_concepts = hydrateSavedMatchMetadata(
        detail.matched_concepts || [],
        article.highlighted_html,
      );
      article._savedHighlightedHtml = article.highlighted_html;
      article._savedMatchedConcepts = cloneConcepts(article.matched_concepts);
      article._hasUnsavedNerPreview = false;
      article._loaded = true;
    } catch (error) {
      console.error("Error fetching detail:", error);
      textBody.innerHTML = `<em style='color:var(--danger)'>${escapeHtml(error.message)}</em>`;
      return;
    }
  } else if (article._savedHighlightedHtml === undefined) {
    article._savedHighlightedHtml = article.highlighted_html || null;
    article._savedMatchedConcepts = cloneConcepts(article.matched_concepts);
    article._hasUnsavedNerPreview = false;
  }

  document.getElementById("articleTitle").textContent = article.title || "Không có tiêu đề";
  document.getElementById("articleYear").textContent = article.publication_year || "—";
  document.getElementById("articleAuthors").textContent = article.authors || "Không rõ tác giả";
  document.getElementById("preprocPanel").style.display = "none";
  renderArticleAnnotationState(article);
}

async function toggleNer() {
  const article = currentArticlesData.find(a => a.id === currentArticleId);
  if (!article) return;

  if (article._hasUnsavedNerPreview) {
    restoreSavedAnnotation(article);
    document.getElementById("preprocPanel").style.display = "none";
    renderArticleAnnotationState(article);
    showToast("Đã hủy bản xem trước; dữ liệu đã lưu không thay đổi", "info");
    return;
  }

  const btnNer = document.getElementById("btnNer");
  const nerStatus = document.getElementById("nerStatus");
  btnNer.disabled = true;
  btnNer.className = "btn-ner loading";
  btnNer.textContent = "Đang phân tích...";
  nerStatus.textContent = "Đang chạy luật mới...";

  try {
    const response = await fetch(`${API_BASE}/highlight-text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text: article.abstract || "",
        threshold: 100,
        enable_tone_restore: false,
        enable_noun_phrase: false,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Không phân tích được văn bản");

    article.highlighted_html = result.highlighted_html;
    article.matched_concepts = result.matched_concepts || [];
    article.ner_note = result.note || "";
    article._hasUnsavedNerPreview = true;
    renderArticleAnnotationState(article);
    _renderPreprocLog(result.preprocessing_log, "preprocPanel", "preprocBody");
  } catch (error) {
    showToast(`Lỗi phân tích NER: ${error.message}`, "error");
    renderArticleAnnotationState(article);
    console.error(error);
  } finally {
    btnNer.disabled = false;
  }
}


function renderEntities(concepts) {
  const grid = document.getElementById("entitiesGrid");
  if (!concepts || concepts.length === 0) {
    grid.innerHTML = `<span style="color:var(--text-3);font-size:12px">Không có thực thể</span>`;
    return;
  }
  const seen = new Set();
  grid.innerHTML = concepts
    .filter(c => { const k = (c.name||"").toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; })
    .map(c => {
      const cls = entityClass(c.type || "DISEASE");
      const codeHtml  = c.code ? `<span class="entity-code">${c.code}</span>` : '';
      return `<div class="entity-tag ${cls}" title="${c.code || ''}">
        ${c.name}
        ${codeHtml}
        ${renderMatchBadge(c.matched_by)}
      </div>`;
    }).join("");
}


async function saveCurrentHighlight() {
  const article = currentArticlesData.find(a => a.id === currentArticleId);
  if (!article || !article._hasUnsavedNerPreview) {
    showToast("Không có bản xem trước mới để lưu", "error");
    return;
  }
  const action = article._savedHighlightedHtml ? "ghi đè kết quả đã lưu" : "lưu kết quả gán nhãn";
  if (!window.confirm(`Xác nhận ${action} cho bài này?`)) return;

  const btn = document.getElementById("btnSaveNer");
  btn.disabled = true;
  btn.innerHTML = "<span>⏳</span> Đang lưu...";

  try {
    const res = await fetch(`${API_BASE}/save-highlight`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({
        article_id:       article.id,
        highlighted_html: article.highlighted_html,
        matched_concepts: article.matched_concepts || []
      })
    });
    const result = await res.json();
    if (res.ok) {
      article.highlighted_html = result.highlighted_html;
      article.matched_concepts = result.matched_concepts || [];
      article._savedHighlightedHtml = article.highlighted_html;
      article._savedMatchedConcepts = cloneConcepts(article.matched_concepts);
      article._hasUnsavedNerPreview = false;
      article.is_labeled = true;
      showToast(`Đã lưu ${result.concepts_saved} thực thể!`, "success");
      renderArticleAnnotationState(article);
      renderArticleList();
      await refreshDashboardAfterLabeling();
    } else {
      showToast("Lỗi: " + (result.detail || "Không xác định"), "error");
    }
  } catch {
    showToast("Lỗi kết nối khi lưu", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Lưu kết quả";
  }
}

async function saveToDict() {
  const article = currentArticlesData.find(a => a.id === currentArticleId);
  if (!article?.matched_concepts?.length) {
    showToast("Không có thực thể để lưu vào từ điển!", "error");
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/save-to-dictionary`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ matched_concepts: article.matched_concepts })
    });
    const result = await res.json();
    if (res.ok) {
      document.getElementById("saveDictStatus").textContent =
        `✅ Đã thêm ${result.added?.length || 0} thuật ngữ`;
      showToast(`Đã thêm ${result.added?.length || 0} thuật ngữ vào từ điển!`, "success");
    }
  } catch {
    showToast("Lỗi khi lưu vào từ điển", "error");
  }
}

// ============================================================
// CRAWL LOGS
// ============================================================
async function loadCrawlLogs() {
  const tbody = document.getElementById("logsTableBody");
  try {
    const res = await fetch(`${API_BASE}/crawl-logs`);
    const logs = await res.json();
    if (!logs || logs.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Chưa có dữ liệu nhật ký thu thập.</td></tr>`;
      return;
    }

    tbody.innerHTML = logs.map(log => {
      const date = new Date(log.crawl_date).toLocaleDateString("vi-VN");
      let statusHtml = "";
      return `
        <tr>
          <td>${date}</td>
          <td class="text-ellipsis" title="${log.target_url}">${log.target_url || "—"}</td>
          <td style="text-align:center; font-weight:500;">${log.start_year || "—"}</td>
          <td style="text-align:center; font-weight:500;">${log.end_year || "—"}</td>
          <td style="text-align:center;">${log.total_urls}</td>
          <td style="text-align:center; color:var(--success); font-weight:500;">${log.success_count}</td>
          <td style="text-align:center; color:var(--warning); font-weight:500;">${log.duplicate_count}</td>
          <td style="text-align:center; color:var(--danger); font-weight:500;">${log.error_count}</td>
        </tr>
      `;
    }).join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="7" class="table-empty">Lỗi khi tải nhật ký: ${e.message}</td></tr>`;
  }
}

// Add loadCrawlLogs call to switchScreen if screen == "logs"
const oldSwitchScreen = switchScreen;
switchScreen = function(screenId) {
  oldSwitchScreen(screenId);
  if (screenId === "logs") {
    loadCrawlLogs();
  }
};

// ============================================================
// SPLIT PDF
// ============================================================

let _splitPdfFiles = [];

function pdfDragOver(e) {
  e.preventDefault();
  document.getElementById('splitPdfDropzone').classList.add('dragging');
}
function pdfDragLeave(e) {
  e.preventDefault();
  document.getElementById('splitPdfDropzone').classList.remove('dragging');
}
function splitPdfDrop(e) {
  e.preventDefault();
  pdfDragLeave(e);
  const files = e.dataTransfer?.files;
  if (files && files.length > 0) _setSplitPdfFiles(files);
}
function splitPdfSelected(e) {
  const files = e.target.files;
  if (files && files.length > 0) _setSplitPdfFiles(files);
}

function _setSplitPdfFiles(files) {
  _splitPdfFiles = [];
  let totalSize = 0;
  for (let i = 0; i < files.length; i++) {
    if (files[i].name.toLowerCase().endsWith('.pdf')) {
      _splitPdfFiles.push(files[i]);
      totalSize += files[i].size;
    }
  }

  if (_splitPdfFiles.length === 0) {
    showToast('Không tìm thấy file PDF nào trong thư mục!', 'error'); 
    return;
  }
  
  document.getElementById('splitPdfName').textContent = `Đã chọn ${_splitPdfFiles.length} file PDF`;
  document.getElementById('splitPdfSize').textContent = `Tổng dung lượng: ${_fmtSize(totalSize)}`;
  document.getElementById('splitPdfInfo').style.display = 'flex';
  document.getElementById('btnSplitPdf').disabled = false;
  document.getElementById('splitPdfResultPanel').style.display = 'none';
  document.getElementById('splitPdfEmpty').style.display = 'block';
}

function clearSplitPdf() {
  _splitPdfFiles = [];
  document.getElementById('splitPdfInput').value = '';
  document.getElementById('splitPdfInfo').style.display = 'none';
  document.getElementById('btnSplitPdf').disabled = true;
  document.getElementById('splitPdfResultPanel').style.display = 'none';
  document.getElementById('splitPdfEmpty').style.display = 'block';
}

function _fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

async function executeSplitPdf() {
  if (_splitPdfFiles.length === 0) { showToast('Chưa chọn file PDF', 'warning'); return; }

  const btn = document.getElementById('btnSplitPdf');
  const oldHtml = btn.innerHTML;
  btn.disabled = true;

  document.getElementById('splitPdfEmpty').style.display = 'none';
  document.getElementById('splitPdfResultPanel').style.display = 'block';
  
  const filesList = document.getElementById('splitPdfFilesList');
  filesList.innerHTML = '';
  
  let successCount = 0;

  for (let i = 0; i < _splitPdfFiles.length; i++) {
    const file = _splitPdfFiles[i];
    btn.innerHTML = `<span>⏳ Đang xử lý file ${i+1}/${_splitPdfFiles.length}...</span>`;
    document.getElementById('splitPdfMessage').textContent = `Đang xử lý ${i+1}/${_splitPdfFiles.length} file... (${file.name})`;

    const startedAt = Date.now();
    const controller = new AbortController();
    const browserTimeout = setTimeout(() => controller.abort(), 330000);
    const elapsedTimer = setInterval(() => {
      const elapsedSeconds = Math.floor((Date.now() - startedAt) / 1000);
      const phase = elapsedSeconds < 30
        ? 'đang tải PDF lên Gemini'
        : 'Gemini đang đọc bố cục và tạo nội dung các phần';
      document.getElementById('splitPdfMessage').textContent =
        `File ${i+1}/${_splitPdfFiles.length}: ${phase} — đã chờ ${elapsedSeconds} giây (${file.name})`;
    }, 1000);

    try {
      const formData = new FormData();
      formData.append('file', file);

      const res = await fetch(`${API_BASE}/extract-pdf`, {
        method: 'POST',
        body: formData,
        signal: controller.signal,
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }

      const data = await res.json();
      successCount++;

      const validation = data.validation || {};
      const validationBadge = validation.ok
        ? `<span style="margin-left:10px; color:var(--success); font-size:12px;">✓ Gemini đã tách ${validation.section_count || 0} mục</span>`
        : `<span style="margin-left:10px; color:var(--warning); font-size:12px;">⚠ Có ${(validation.issues || []).length} cảnh báo kiểm chứng</span>`;
      const articleTitle = data.article_title ? ` — ${escapeHtml(data.article_title)}` : '';
      const fileHeader = `<li><div style="margin-top:15px; margin-bottom: 5px;"><strong style="color:var(--primary); font-size:15px;">📄 ${escapeHtml(file.name)}${articleTitle}</strong>${validationBadge}</div>`;
      const innerList = data.files_created.map((f) => {
        if (typeof f === 'string') return `<div style="margin-left:20px; font-size:13px; margin-bottom:4px;">- ${escapeHtml(f)}</div>`;
        return `
          <div style="margin-left:20px; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 10px; overflow: hidden;">
            <div style="background: var(--bg-soft); padding: 8px 14px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; cursor: pointer;" onclick="const content = this.nextElementSibling; content.style.display = content.style.display === 'none' ? 'block' : 'none';">
              <strong style="color: var(--primary); font-size: 13px;">📑 ${escapeHtml(f.section_name)}</strong>
              <span style="font-size: 12px; color: var(--text-3); max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="${f.file_path}">${f.file_path.split(/[\\/\\\\]/).pop()}</span>
            </div>
            <div style="padding: 14px; font-size: 12px; color: var(--text-2); background: white; display: none; line-height: 1.5;">
              ${f.content_preview ? escapeHtml(f.content_preview).replace(/\\n/g, '<br>') : 'Không có nội dung preview'}
            </div>
          </div>
        `;
      }).join("");
      
      filesList.innerHTML += fileHeader + innerList + "</li>";

    } catch (e) {
      const message = e.name === 'AbortError'
        ? 'Quá 5 phút nhưng Gemini chưa trả kết quả. Vui lòng thử lại hoặc dùng PDF ngắn hơn.'
        : e.message;
      filesList.innerHTML += `<li><div style="margin-top:15px; margin-bottom: 5px;"><strong style="color:var(--danger); font-size:15px;">❌ ${escapeHtml(file.name)}</strong> - Lỗi: ${escapeHtml(message)}</div></li>`;
    } finally {
      clearTimeout(browserTimeout);
      clearInterval(elapsedTimer);
    }
  }

  document.getElementById('splitPdfMessage').textContent = `Hoàn tất: Xử lý thành công ${successCount}/${_splitPdfFiles.length} file.`;
  showToast(`Tách xong ${successCount}/${_splitPdfFiles.length} file PDF!`, 'success');
  
  btn.disabled = false;
  btn.innerHTML = oldHtml;
}

// Render preprocessing log panel
function _renderPreprocLog(log, panelId, bodyId) {
  const panel = document.getElementById(panelId);
  const body  = document.getElementById(bodyId);
  if (!panel || !body) return;

  if (!log || !Array.isArray(log) || log.length === 0) {
    panel.style.display = 'none';
    return;
  }

  panel.style.display = 'block';
  
  let html = '<div class="preproc-timeline">';
  
  log.forEach(step => {
    html += `
      <div class="preproc-step">
        <div class="preproc-step-header">
          <span class="preproc-step-icon">⚡</span>
          <span class="preproc-step-title">${step.step}</span>
        </div>
        <div class="preproc-step-desc">${step.description}</div>
    `;
    
    if (step.changes && step.changes.length > 0) {
      html += `<div class="preproc-changes-list">`;
      step.changes.forEach(change => {
        const parts = change.split(' → ');
        if (parts.length === 2) {
          // Highlight before/after
          html += `
            <div class="preproc-item">
              <span class="preproc-before">${parts[0].trim()}</span>
              <span class="preproc-arrow">→</span>
              <span class="preproc-after">${parts[1].trim()}</span>
            </div>
          `;
        } else {
          html += `<div class="preproc-item" style="font-size:11px">${change}</div>`;
        }
      });
      html += `</div>`;
    }
    
    html += `</div>`;
  });
  
  html += '</div>';
  body.innerHTML = html;
}

// Render entities grid with matched_by badge
function _renderEntitiesGrid(concepts, gridId) {
  const grid = document.getElementById(gridId);
  if (!grid) return;

  if (!concepts || concepts.length === 0) {
    grid.innerHTML = '<div style="color:var(--text-3);font-size:13px;padding:8px 0;">Không tìm thấy thực thể y tế</div>';
    return;
  }

  const TYPE_MAP = {
    'Bệnh Lý':                'disease',
    'Triệu Chứng':            'symptom',
    'Điều Trị':               'treatment',
    'Xét Nghiệm/Cận Lâm Sàng': 'labtest',
    'Chẩn Đoán Hình Ảnh':    'imaging',
    'Đông Y / YHCT':          'tradmed',
    'Tiến Trình Bệnh Lý':    'symptom',
    'DISEASE':                'disease',
    'SYMPTOM':                'symptom',
    'TREATMENT':              'treatment',
  };

  grid.innerHTML = concepts.map(c => {
    const cls = TYPE_MAP[c.type] || 'other';
    const codeHtml  = c.code ? `<span class="entity-code">${c.code}</span>` : '';
    return `<div class="entity-tag ${cls}">
      ${c.name}
      ${codeHtml}
      ${renderMatchBadge(c.matched_by)}
    </div>`;
  }).join('');
}
// script.js bổ sung verifyData
async function verifyData() {
    const modal = document.getElementById('verifyModal');
    const body = document.getElementById('verifyModalBody');
    modal.style.display = 'flex';
    body.innerHTML = '<div style="text-align:center; padding: 20px;">⏳ Đang kiểm chứng dữ liệu, vui lòng đợi...</div>';
    
    try {
        const res = await fetch(`${API_BASE}/verify-data`);
        if (!res.ok) throw new Error("Lỗi API verify-data");
        const data = await res.json();
        
        let html = `
            <div style="display:flex; justify-content:space-around; margin-bottom: 20px; background:var(--bg-card); padding:15px; border-radius:8px;">
                <div style="text-align:center;">
                    <div style="font-size:24px; font-weight:bold; color:var(--primary);">${data.total_articles_in_db}</div>
                    <div style="font-size:12px; color:var(--text-3);">Bài báo (DB)</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:24px; font-weight:bold; color:var(--accent);">${data.total_pdfs_on_disk}</div>
                    <div style="font-size:12px; color:var(--text-3);">File PDF (Disk)</div>
                </div>
                <div style="text-align:center;">
                    <div style="font-size:24px; font-weight:bold; color:var(--success);">${data.total_txts_on_disk}</div>
                    <div style="font-size:12px; color:var(--text-3);">File TXT (Disk)</div>
                </div>
            </div>
        `;
        
        if (data.missing_pdfs_count > 0 || data.missing_txts_count > 0) {
            html += `<div style="color:var(--danger); font-weight:bold; margin-bottom:10px;">⚠️ Phát hiện ${data.missing_pdfs_count} bài báo thiếu PDF và ${data.missing_txts_count} bài báo thiếu TXT.</div>`;
            
            if (data.missing_pdfs.length > 0) {
                html += `<h4 style="margin-top:10px;">📄 Danh sách thiếu PDF (hiển thị tối đa 50)</h4>`;
                html += `<ul style="list-style:none; padding:0;">`;
                data.missing_pdfs.forEach(m => {
                    html += `<li style="padding:8px; border-bottom:1px solid var(--border); font-size:13px;">
                        <strong>[ID: ${m.id}] ${m.title}</strong> (${m.year})<br>
                        <span style="color:var(--danger);">${m.reason}</span>
                    </li>`;
                });
                html += `</ul>`;
            }
            
            if (data.missing_txts.length > 0) {
                html += `<h4 style="margin-top:20px;">📝 Danh sách thiếu TXT (hiển thị tối đa 50)</h4>`;
                html += `<ul style="list-style:none; padding:0;">`;
                data.missing_txts.forEach(m => {
                    html += `<li style="padding:8px; border-bottom:1px solid var(--border); font-size:13px;">
                        <strong>[ID: ${m.id}] ${m.title}</strong> (${m.year})<br>
                        <span style="color:var(--danger);">${m.reason}</span>
                    </li>`;
                });
                html += `</ul>`;
            }
        } else {
            html += `<div style="text-align:center; color:var(--success); font-weight:bold; padding:20px;">✅ Dữ liệu hoàn toàn khớp nhau! Không phát hiện file bị thiếu.</div>`;
        }
        
        body.innerHTML = html;
        
    } catch (err) {
        body.innerHTML = `<div style="color:var(--danger); text-align:center; padding:20px;">❌ Lỗi: ${err.message}</div>`;
    }
}

// ============================================================
// AI LABEL UI
// ============================================================

async function loadAiLabelData() {
  if (aiLabelArticlesData && aiLabelArticlesData.length > 0) return;
  const listEl = document.getElementById("aiArticleListScroll");
  try {
    const res = await fetch(`${API_BASE}/articles`);
    aiLabelArticlesData = await res.json();
    if (aiLabelArticlesData.length > 0) {
      currentAiArticleId = aiLabelArticlesData[0].id;
    }
    renderAiArticleList();
    if (currentAiArticleId) selectAiArticle(currentAiArticleId);
  } catch (e) {
    listEl.innerHTML = `<div style="padding:20px; color:var(--danger)">Lỗi tải dữ liệu: ${e.message}</div>`;
  }
}

function filterAiArticles() {
  const query = document.getElementById("aiSearchInput").value.toLowerCase();
  const pills = document.querySelectorAll("#aiFilterPills .filter-pill");
  pills.forEach(p => {
    if (p.classList.contains("active")) {
      currentAiFilter = p.dataset.filter;
    }
  });

  const filtered = aiLabelArticlesData.filter(a => {
    const text = (a.title + " " + a.authors + " " + a.abstract).toLowerCase();
    if (!text.includes(query)) return false;

    if (currentAiFilter === "labeled") return Boolean(a.ai_labeled);
    if (currentAiFilter === "unlabeled") return !a.ai_labeled;
    return true;
  });

  const listEl = document.getElementById("aiArticleListScroll");
  listEl.innerHTML = filtered.map(a => {
    const isActive = a.id === currentAiArticleId ? "active" : "";
    const isLabeled = Boolean(a.ai_labeled);
    return `
      <div class="article-list-item ${isActive}" onclick="selectAiArticle(${a.id})">
        <div class="ali-title">${a.title || "Không có tiêu đề"}</div>
        <div class="ali-meta">
          <span class="ali-dot ${isLabeled ? "labeled" : "unlabeled"}"></span>
          <span>${a.publication_year || "—"}</span>
          <span>·</span>
          <span>${isLabeled ? "Đã gán nhãn" : "Chưa xử lý"}</span>
        </div>
      </div>
    `;
  }).join("");
}

// Add listener to filter pills
document.querySelectorAll("#aiFilterPills .filter-pill").forEach(p => {
  p.addEventListener("click", function() {
    document.querySelectorAll("#aiFilterPills .filter-pill").forEach(el => el.classList.remove("active"));
    this.classList.add("active");
    filterAiArticles();
  });
});

function renderAiArticleList() {
  filterAiArticles();
}

function renderAiLabelSnapshot(article, data) {
  const catMapping = {
    "Bệnh lý": { key: "DISEASE" },
    "Triệu chứng": { key: "SYMPTOM" },
    "Điều trị": { key: "TREATMENT" },
    "Xét nghiệm": { key: "LAB_TEST" },
    "Hình ảnh": { key: "IMAGING" },
    "Sinh lý": { key: "PHYSIOLOGY" }
  };

  if (!ENTITY_COLORS.PHYSIOLOGY) {
    ENTITY_COLORS.PHYSIOLOGY = { cls: "physiology", label: "Sinh lý", icon: "" };
  }
  if (!document.getElementById("physiology-style")) {
    const style = document.createElement("style");
    style.id = "physiology-style";
    style.innerHTML = `.entity-tag.physiology { background-color: rgba(251,146,60,0.15); border-color: rgba(251,146,60,0.4); color: #c2410c; }
    mark.ner-physiology { background: #f9a8d4; color: var(--text); font-weight: 600; border-radius: 4px; padding: 2px 5px; cursor: help; box-shadow: 0 1px 2px rgba(0,0,0,0.05); border: 1px solid rgba(0,0,0,0.08); }`;
    document.head.appendChild(style);
  }

  let totalCount = 0;
  let entitiesHtml = "";
  const matches = [];
  for (const [vnCat, termsList] of Object.entries(data || {})) {
    const mapping = catMapping[vnCat];
    if (!mapping || !Array.isArray(termsList)) continue;
    for (const rawItem of termsList) {
      const item = typeof rawItem === "string"
        ? { term: rawItem, code: "", label_vn: "", spans: [] }
        : rawItem;
      if (!item?.term) continue;

      totalCount += 1;
      const markClass = mapping.key.toLowerCase().replace("_", "");
      const tooltip = buildEntityTooltip(item, vnCat);
      const codeHtml = item.code
        ? `<span class="entity-code">${escapeHtml(item.code)}</span>`
        : "";
      const sourceLabel = item.source === "ai+dictionary" ? "AI + Từ điển" : "AI";
      entitiesHtml += `<div class="entity-tag ${mapping.key.toLowerCase()}" title="${escapeHtml(tooltip)}">
        <span>${escapeHtml(item.term)}</span>
        ${codeHtml}
        <span class="matched-badge matched-exact">${sourceLabel}</span>
      </div>`;

      for (const span of Array.isArray(item.spans) ? item.spans : []) {
        const start = Number(span.start);
        const end = Number(span.end);
        if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) continue;
        const surface = (article.abstract || "").slice(start, end);
        if (surface.toLocaleLowerCase("vi") !== item.term.toLocaleLowerCase("vi")) continue;
        matches.push({ start, end, text: surface, markClass, tooltip });
      }
    }
  }

  matches.sort((a, b) => b.text.length - a.text.length || a.start - b.start);
  const acceptedMatches = [];
  for (const match of matches) {
    const overlaps = acceptedMatches.some(
      accepted => match.start < accepted.end && match.end > accepted.start
    );
    if (!overlaps) acceptedMatches.push(match);
  }
  acceptedMatches.sort((a, b) => a.start - b.start);

  const pieces = [];
  let cursor = 0;
  for (const match of acceptedMatches) {
    pieces.push(escapeHtml((article.abstract || "").slice(cursor, match.start)));
    pieces.push(
      `<mark class="ner-${match.markClass}" title="${escapeHtml(match.tooltip)}">${escapeHtml(match.text)}</mark>`
    );
    cursor = match.end;
  }
  pieces.push(escapeHtml((article.abstract || "").slice(cursor)));
  document.getElementById("aiTextBodyArea").innerHTML = pieces.join("").replace(/\n/g, "<br>");
  document.getElementById("aiTotalEntitiesCount").textContent = String(totalCount);
  document.getElementById("aiEntitiesList").innerHTML = totalCount
    ? entitiesHtml
    : `<div class="empty-state">AI không tìm thấy thực thể nào</div>`;
  return totalCount;
}

async function selectAiArticle(id) {
  const previous = aiLabelArticlesData.find(a => a.id === currentAiArticleId);
  if (previous && previous.id !== id && previous._hasUnsavedAiPreview) {
    previous._aiLabelPreview = null;
    previous._hasUnsavedAiPreview = false;
    showToast("Đã hủy kết quả AI chưa lưu của bài trước", "info");
  }
  currentAiArticleId = id;
  renderAiArticleList(); // highlight active card

  const article = aiLabelArticlesData.find(a => a.id === id);
  if (!article) return;

  document.getElementById("aiMetaTitle").textContent = article.title || "Chưa có tiêu đề";
  document.getElementById("aiMetaYear").textContent  = article.publication_year || "N/A";
  document.getElementById("aiMetaAuthors").textContent = article.authors || "Không rõ tác giả";

  document.getElementById("btnRunAiNer").disabled = false;
  document.getElementById("btnRunAiNer").textContent = "Gán nhãn bằng AI";
  document.getElementById("btnSaveAiNer").style.display = "none";
  
  const aiTextBodyArea = document.getElementById("aiTextBodyArea");
  if (!article._loaded) {
    aiTextBodyArea.innerHTML = `<em style='color:var(--text-3)'>⏳ Đang tải nội dung...</em>`;
    try {
      const detail = await fetch(`${API_BASE}/articles/${id}`).then(r => r.json());
      if (detail) {
        article.abstract = detail.abstract || null;
        article.highlighted_html = detail.highlighted_html || null;
        article._loaded = true;
      }
    } catch (e) {
      console.error("Error fetching detail for AI:", e);
      aiTextBodyArea.innerHTML = `<em style='color:var(--danger)'>Lỗi tải nội dung bài báo</em>`;
      return;
    }
  }

  aiTextBodyArea.innerHTML = article.abstract
    ? escapeHtml(article.abstract).replace(/\n/g, "<br>")
    : "<em style='color:var(--text-3)'>Bài báo này không có nội dung tóm tắt trong cơ sở dữ liệu.</em>";

  if (!article._savedAiLabelLoaded) {
    document.getElementById("aiEntitiesList").innerHTML = `<div class="empty-state">Đang tải lịch sử gán nhãn AI...</div>`;
    try {
      const response = await fetch(`${API_BASE}/ai-label-results/${id}`);
      const saved = await response.json();
      if (!response.ok) throw new Error(saved.detail || "Không tải được lịch sử AI");
      article._savedAiLabelResult = saved.result || null;
      article._savedAiLabelCreatedAt = saved.created_at || null;
      article._savedAiLabelLoaded = true;
      article.ai_labeled = Boolean(saved.result);
    } catch (error) {
      console.error("Error fetching saved AI label:", error);
      article._savedAiLabelResult = null;
      article._savedAiLabelLoaded = true;
    }
  }
  if (currentAiArticleId !== id) return;

  if (article._savedAiLabelResult) {
    renderAiLabelSnapshot(article, article._savedAiLabelResult);
    document.getElementById("btnRunAiNer").textContent = "Gán nhãn lại bằng AI";
  } else {
    document.getElementById("aiEntitiesList").innerHTML = `<div class="empty-state">Chưa có lịch sử gán nhãn AI</div>`;
    document.getElementById("aiTotalEntitiesCount").textContent = "0";
  }
  renderAiArticleList();
}

async function runAiLabel() {
  const article = aiLabelArticlesData.find(a => a.id === currentAiArticleId);
  if (!article || !article.abstract) {
    showToast("Không có văn bản để phân tích", "error");
    return;
  }

  const btn = document.getElementById("btnRunAiNer");
  btn.disabled = true;
  btn.textContent = "Đang phân tích...";
  document.getElementById("aiEntitiesList").innerHTML = `<div class="empty-state">Đang gọi Gemini AI...</div>`;

  try {
    const res = await fetch(`${API_BASE}/ai-label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: article.abstract, article_id: article.id })
    });

    if (!res.ok) {
      throw new Error("Lỗi gọi API AI");
    }

    const data = await res.json();
    
    let totalCount = 0;

    const catMapping = {
      "Bệnh lý": { color: "#4ade80", key: "DISEASE" },
      "Triệu chứng": { color: "#818cf8", key: "SYMPTOM" },
      "Điều trị": { color: "#c084fc", key: "TREATMENT" },
      "Xét nghiệm": { color: "#fbbf24", key: "LAB_TEST" },
      "Hình ảnh": { color: "#2dd4bf", key: "IMAGING" },
      "Sinh lý": { color: "#fb923c", key: "PHYSIOLOGY" }
    };

    if(!ENTITY_COLORS["PHYSIOLOGY"]) {
      ENTITY_COLORS["PHYSIOLOGY"] = { cls: "physiology", label: "Sinh lý", icon: "" };
    }
    if (!document.getElementById("physiology-style")) {
      const style = document.createElement('style');
      style.id = "physiology-style";
      style.innerHTML = `.entity-tag.physiology { background-color: rgba(251,146,60,0.15); border-color: rgba(251,146,60,0.4); color: #c2410c; }
      mark.ner-physiology { background: #f9a8d4; color: var(--text); font-weight: 600; border-radius: 4px; padding: 2px 5px; cursor: help; box-shadow: 0 1px 2px rgba(0,0,0,0.05); border: 1px solid rgba(0,0,0,0.08); }`;
      document.head.appendChild(style);
    }

    let entitiesHtml = "";
    const matches = [];

    for (const [vnCat, termsList] of Object.entries(data)) {
      const mapping = catMapping[vnCat];
      if (!mapping || !Array.isArray(termsList)) continue;

      for (const rawItem of termsList) {
        const item = typeof rawItem === "string"
          ? { term: rawItem, code: "", label_vn: "", spans: [] }
          : rawItem;
        if (!item?.term) continue;

        totalCount += 1;
        const markClass = mapping.key.toLowerCase().replace("_", "");
        const tooltip = buildEntityTooltip(item, vnCat);
        const codeHtml = item.code
          ? `<span class="entity-code">${escapeHtml(item.code)}</span>`
          : "";
        const sourceLabel = item.source === "ai+dictionary" ? "AI + Từ điển" : "AI";
        entitiesHtml += `<div class="entity-tag ${mapping.key.toLowerCase()}" title="${escapeHtml(tooltip)}">
          <span>${escapeHtml(item.term)}</span>
          ${codeHtml}
          <span class="matched-badge matched-exact">${sourceLabel}</span>
        </div>`;

        const spans = Array.isArray(item.spans) ? item.spans : [];
        for (const span of spans) {
          const start = Number(span.start);
          const end = Number(span.end);
          if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) continue;
          const surface = article.abstract.slice(start, end);
          if (surface.toLocaleLowerCase("vi") !== item.term.toLocaleLowerCase("vi")) continue;
          matches.push({ start, end, text: surface, markClass, tooltip });
        }
      }
    }

    // Giữ span dài nhất khi nhiều thực thể chồng lấn.
    matches.sort((a, b) => b.text.length - a.text.length || a.start - b.start);
    const finalMatches = [];
    for (const m of matches) {
      const overlaps = finalMatches.some(
        accepted => m.start < accepted.end && m.end > accepted.start
      );
      if (!overlaps) finalMatches.push(m);
    }

    // Dựng HTML từ văn bản nguồn và offset đã được backend xác thực.
    finalMatches.sort((a, b) => a.start - b.start);
    const pieces = [];
    let cursor = 0;
    for (const m of finalMatches) {
      pieces.push(escapeHtml(article.abstract.slice(cursor, m.start)));
      pieces.push(
        `<mark class="ner-${m.markClass}" title="${escapeHtml(m.tooltip)}">${escapeHtml(m.text)}</mark>`
      );
      cursor = m.end;
    }
    pieces.push(escapeHtml(article.abstract.slice(cursor)));
    document.getElementById("aiTextBodyArea").innerHTML = pieces.join("").replace(/\n/g, "<br>");

    if (totalCount > 0) {
        document.getElementById("aiTotalEntitiesCount").textContent = totalCount;
        document.getElementById("aiEntitiesList").innerHTML = entitiesHtml;
    } else {
        document.getElementById("aiTotalEntitiesCount").textContent = "0";
        document.getElementById("aiEntitiesList").innerHTML = `<div class="empty-state">AI không tìm thấy thực thể nào</div>`;
    }

    article._aiLabelPreview = data;
    article._hasUnsavedAiPreview = true;
    document.getElementById("btnSaveAiNer").style.display = "flex";
    
  } catch (e) {
    article._aiLabelPreview = null;
    article._hasUnsavedAiPreview = false;
    document.getElementById("btnSaveAiNer").style.display = "none";
    document.getElementById("aiEntitiesList").innerHTML = `<div class="empty-state" style="color:var(--danger)">Lỗi: ${e.message}</div>`;
    showToast("Lỗi phân tích AI", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Gán nhãn bằng AI";
  }
}

async function saveAiLabelResult() {
  const article = aiLabelArticlesData.find(a => a.id === currentAiArticleId);
  if (!article || !article._hasUnsavedAiPreview || !article._aiLabelPreview) {
    showToast("Không có kết quả AI mới để lưu", "error");
    return;
  }

  const btn = document.getElementById("btnSaveAiNer");
  btn.disabled = true;
  btn.innerHTML = "<span>⏳</span> Đang lưu...";

  try {
    const response = await fetch(`${API_BASE}/save-ai-label`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        article_id: article.id,
        result: article._aiLabelPreview
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Không lưu được kết quả AI");

    article._savedAiLabelResult = JSON.parse(JSON.stringify(article._aiLabelPreview));
    article._savedAiLabelLoaded = true;
    article._savedAiLabelCreatedAt = new Date().toISOString();
    article._hasUnsavedAiPreview = false;
    article.ai_labeled = true;
    btn.style.display = "none";
    document.getElementById("btnRunAiNer").textContent = "Gán nhãn lại bằng AI";
    renderAiArticleList();
    showToast(`Đã lưu ${data.entities_saved} thực thể AI!`, "success");
    await refreshDashboardAfterLabeling();
  } catch (error) {
    showToast(`Lỗi: ${error.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Lưu kết quả";
  }
}

// ============================================================
// CỔNG CHUYÊN GIA
// ============================================================
async function expertApi(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (expertToken) headers.Authorization = `Bearer ${expertToken}`;
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401 && path !== "/expert/login") {
    logoutExpert(false);
    throw new Error("Phiên đăng nhập đã hết hạn");
  }
  return response;
}

function openExpertLogin() {
  if (expertToken && currentExpert) {
    logoutExpert();
    return;
  }
  document.getElementById("expertLoginError").textContent = "";
  document.getElementById("expertPassword").value = "";
  document.getElementById("expertLoginModal").style.display = "flex";
  setTimeout(() => document.getElementById("expertUsername").focus(), 50);
}

function closeExpertLogin() {
  document.getElementById("expertLoginModal").style.display = "none";
}

async function submitExpertLogin(event) {
  event.preventDefault();
  const button = document.getElementById("expertLoginSubmit");
  const error = document.getElementById("expertLoginError");
  button.disabled = true;
  button.textContent = "Đang đăng nhập...";
  error.textContent = "";
  try {
    const response = await fetch(`${API_BASE}/expert/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("expertUsername").value.trim(),
        password: document.getElementById("expertPassword").value,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Không đăng nhập được");
    expertToken = data.access_token;
    currentExpert = data.expert;
    localStorage.setItem("mednlp_expert_token", expertToken);
    updateExpertSessionUI();
    closeExpertLogin();
    showToast(`Xin chào ${currentExpert.full_name}`, "success");
    switchScreen("expert");
  } catch (err) {
    error.textContent = err.message;
  } finally {
    button.disabled = false;
    button.textContent = "Đăng nhập";
  }
}

async function restoreExpertSession() {
  if (!expertToken) return;
  try {
    const response = await expertApi("/expert/me");
    if (!response.ok) throw new Error();
    const payload = await response.json();
    currentExpert = {
      id: payload.sub,
      username: payload.username,
      full_name: payload.full_name,
    };
    updateExpertSessionUI();
  } catch {
    logoutExpert(false);
  }
}

function updateExpertSessionUI() {
  const loggedIn = Boolean(expertToken && currentExpert);
  const button = document.getElementById("expertLoginButton");
  button.textContent = loggedIn ? `${currentExpert.full_name} · Đăng xuất` : "Đăng nhập chuyên gia";
  button.classList.toggle("logged-in", loggedIn);
  document.body.classList.toggle("expert-session", loggedIn);
  document.body.classList.toggle(
    "sidebar-collapsed",
    loggedIn && localStorage.getItem("mednlp_sidebar_collapsed") === "1",
  );
  document.getElementById("sidebarToggle").textContent = document.body.classList.contains("sidebar-collapsed") ? "›" : "‹";
  document.getElementById("expertNavItem").style.display = loggedIn ? "flex" : "none";
  document.getElementById("expertIdentity").textContent = loggedIn
    ? `${currentExpert.full_name} (${currentExpert.username})`
    : "—";
}

function logoutExpert(showMessage = true) {
  expertToken = "";
  currentExpert = null;
  currentExpertArticleId = null;
  currentExpertArticle = null;
  localStorage.removeItem("mednlp_expert_token");
  updateExpertSessionUI();
  if (location.hash === "#expert") switchScreen("dashboard");
  if (showMessage) showToast("Đã đăng xuất chuyên gia", "info");
}

function toggleSidebar() {
  const collapsed = document.body.classList.toggle("sidebar-collapsed");
  const toggle = document.getElementById("sidebarToggle");
  toggle.textContent = collapsed ? "›" : "‹";
  toggle.title = collapsed ? "Mở thanh chức năng" : "Thu gọn thanh chức năng";
  toggle.setAttribute("aria-label", toggle.title);
  localStorage.setItem("mednlp_sidebar_collapsed", collapsed ? "1" : "0");
}

function openExpertWorkspace() {
  if (!expertToken || !currentExpert) {
    openExpertLogin();
    return;
  }
  switchScreen("expert");
}

async function loadExpertReviewItems() {
  if (!expertToken) return;
  const list = document.getElementById("expertReviewList");
  const query = document.getElementById("expertSearchInput").value.trim();
  list.innerHTML = `<div class="list-placeholder">Đang tải...</div>`;
  try {
    const response = await expertApi(`/expert/review-items?q=${encodeURIComponent(query)}&limit=200`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Không tải được danh sách");
    if (!data.items.length) {
      list.innerHTML = `<div class="list-placeholder">Không có văn bản phù hợp.</div>`;
      return;
    }
    list.innerHTML = data.items.map(item => {
      const manualReviewed = Boolean(item.my_reviews?.manual);
      const aiReviewed = Boolean(item.my_reviews?.ai);
      return `<div class="expert-review-item ${item.id === currentExpertArticleId ? "active" : ""}" onclick="selectExpertReview(${item.id})">
        <div class="expert-review-item-title">${escapeHtml(item.title)}</div>
        <div class="expert-review-item-meta">
          <span>${item.publication_year || "—"}</span>
          ${manualReviewed ? `<span class="review-chip reviewed">Đã nhận xét thủ công</span>` : ""}
          ${aiReviewed ? `<span class="review-chip reviewed">Đã nhận xét AI</span>` : ""}
        </div>
      </div>`;
    }).join("");
  } catch (err) {
    list.innerHTML = `<div class="list-placeholder">${escapeHtml(err.message)}</div>`;
  }
}

function flattenAiResult(aiResult) {
  const rows = [];
  if (!aiResult || typeof aiResult !== "object") return rows;
  for (const [category, values] of Object.entries(aiResult)) {
    if (!Array.isArray(values)) continue;
    values.forEach(value => rows.push(
      typeof value === "string" ? { category, term: value, spans: [] } : { category, ...value },
    ));
  }
  return rows;
}

function expertAiCategoryClass(category) {
  const normalized = String(category || "").trim().toLocaleLowerCase("vi");
  return {
    "bệnh lý": "disease",
    "triệu chứng": "symptom",
    "điều trị": "treatment",
    "xét nghiệm": "labtest",
    "hình ảnh": "imaging",
    "sinh lý": "physiology",
  }[normalized] || "disease";
}

function buildExpertAiHighlightedHtml(text, aiResult) {
  const sourceText = String(text || "");
  if (!sourceText) return "Không có nội dung";

  const matches = [];
  for (const item of flattenAiResult(aiResult)) {
    const term = String(item.term || "").trim();
    if (!term) continue;
    const markClass = expertAiCategoryClass(item.category);
    const tooltip = buildEntityTooltip(item, item.category || "AI");
    let validSpanCount = 0;

    for (const span of Array.isArray(item.spans) ? item.spans : []) {
      const start = Number(span.start);
      const end = Number(span.end);
      if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start || end > sourceText.length) continue;
      const surface = sourceText.slice(start, end);
      if (surface.toLocaleLowerCase("vi") !== term.toLocaleLowerCase("vi")) continue;
      matches.push({ start, end, text: surface, markClass, tooltip });
      validSpanCount += 1;
    }

    // Tương thích snapshot AI cũ chưa lưu spans hoặc lưu offset không còn hợp lệ.
    if (!validSpanCount) {
      const haystack = sourceText.toLocaleLowerCase("vi");
      const needle = term.toLocaleLowerCase("vi");
      let start = 0;
      while (needle && (start = haystack.indexOf(needle, start)) !== -1) {
        const end = start + term.length;
        matches.push({ start, end, text: sourceText.slice(start, end), markClass, tooltip });
        start = end;
      }
    }
  }

  // Khi nhiều nhãn chồng nhau, giữ nhãn dài hơn để văn bản không bị vỡ.
  matches.sort((a, b) => (b.end - b.start) - (a.end - a.start) || a.start - b.start);
  const accepted = [];
  for (const match of matches) {
    if (!accepted.some(value => match.start < value.end && match.end > value.start)) {
      accepted.push(match);
    }
  }
  accepted.sort((a, b) => a.start - b.start);

  const pieces = [];
  let cursor = 0;
  for (const match of accepted) {
    pieces.push(escapeHtml(sourceText.slice(cursor, match.start)));
    pieces.push(`<mark class="ner-${match.markClass}" title="${escapeHtml(match.tooltip)}">${escapeHtml(match.text)}</mark>`);
    cursor = match.end;
  }
  pieces.push(escapeHtml(sourceText.slice(cursor)));
  return pieces.join("").replaceAll("\n", "<br>");
}

async function selectExpertReview(articleId) {
  currentExpertArticleId = articleId;
  document.getElementById("expertReviewEmpty").style.display = "none";
  document.getElementById("expertReviewContent").style.display = "block";
  document.getElementById("expertArticleTitle").textContent = "Đang tải...";
  try {
    const response = await expertApi(`/expert/review-items/${articleId}`);
    const article = await response.json();
    if (!response.ok) throw new Error(article.detail || "Không tải được văn bản");
    currentExpertArticle = article;
    document.getElementById("expertArticleTitle").textContent = article.title;
    document.getElementById("expertArticleMeta").textContent =
      `${article.publication_year || "—"} · ${article.authors || "Không rõ tác giả"}`;
    renderExpertReviewMode();
    loadExpertReviewItems();
  } catch (err) {
    showToast(err.message, "error");
  }
}

function setExpertReviewMode(mode) {
  if (mode !== "manual" && mode !== "ai") return;
  currentExpertReviewMode = mode;
  renderExpertReviewMode();
}

function renderExpertReviewMode() {
  if (!currentExpertArticle) return;
  const isAi = currentExpertReviewMode === "ai";
  document.getElementById("expertModeManual").classList.toggle("active", !isAi);
  document.getElementById("expertModeAi").classList.toggle("active", isAi);
  document.getElementById("expertActiveResultBox").classList.toggle("ai", isAi);
  document.getElementById("expertActiveResultTitle").textContent = isAi
    ? "Kết quả gán nhãn AI"
    : "Kết quả gán nhãn thủ công";
  document.getElementById("expertLabeledTextTitle").textContent = isAi
    ? "Văn bản được AI gán nhãn"
    : "Văn bản đã được gán nhãn thủ công";
  document.getElementById("expertSourceText").innerHTML = isAi
    ? buildExpertAiHighlightedHtml(currentExpertArticle.abstract, currentExpertArticle.ai_result)
    : (currentExpertArticle.highlighted_html
      || escapeHtml(currentExpertArticle.abstract || "Không có nội dung").replaceAll("\n", "<br>"));
  const result = isAi ? currentExpertArticle.ai_result : currentExpertArticle.manual_entities;
  document.getElementById("expertActiveResult").textContent = result
    ? JSON.stringify(result, null, 2)
    : (isAi ? "Chưa có kết quả AI" : "Chưa có nhãn thủ công");

  const myReview = currentExpertArticle.my_reviews?.[currentExpertReviewMode];
  const sourceName = isAi ? "AI" : "thủ công";
  document.getElementById("expertNoteLabel").textContent = `Nhận xét cho kết quả gán nhãn ${sourceName}`;
  document.getElementById("expertNote").value = myReview?.note || "";
  document.getElementById("expertSaveTime").textContent = myReview?.updated_at
    ? `Đã lưu: ${new Date(myReview.updated_at).toLocaleString("vi-VN")}`
    : "Chưa lưu nhận xét";
  document.getElementById("expertHistoryTitle").textContent = `Nhận xét của các chuyên gia cho nhãn ${sourceName}`;

  const history = (currentExpertArticle.reviews || [])
    .filter(review => review.label_source === currentExpertReviewMode);
  document.getElementById("expertReviewHistory").innerHTML = history.length
    ? history.map(review => `<div class="expert-history-card">
        <div class="expert-history-head"><strong>${escapeHtml(review.expert_name)}</strong><span>${new Date(review.updated_at).toLocaleString("vi-VN")}</span></div>
        <div class="expert-history-note">${escapeHtml(review.note || "Không có nhận xét")}</div>
      </div>`).join("")
    : `<div class="empty-state">Chưa có chuyên gia nhận xét cho nguồn nhãn này.</div>`;
}

async function saveExpertReview() {
  if (!currentExpertArticleId) return;
  const button = document.getElementById("expertSaveButton");
  const buttonLabel = document.getElementById("expertSaveButtonLabel");
  button.disabled = true;
  buttonLabel.textContent = "Đang lưu...";
  try {
    const response = await expertApi(`/expert/reviews/${currentExpertArticleId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        note: document.getElementById("expertNote").value,
        label_source: currentExpertReviewMode,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Không lưu được nhận xét");
    showToast("Đã lưu nhận xét chuyên gia", "success");
    await selectExpertReview(currentExpertArticleId);
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    button.disabled = false;
    buttonLabel.textContent = "Lưu nhận xét";
  }
}

window.onerror = function(msg, url, lineNo, columnNo, error) { console.error(msg + ' at line ' + lineNo); return false; };

// ============================================================
// KHỜI TẠO KHI TRANG LOAD — auto-resume nếu crawl đang chạy
// ============================================================
document.addEventListener("DOMContentLoaded", async () => {
  // Luôn khởi tạo đúng nguồn Tâm Anh và khoảng năm gần nhất; người dùng vẫn có thể sửa trước khi chạy.
  const currentYear = new Date().getFullYear();
  setCrawlMode("tamanh");
  document.getElementById("startYear").value = Math.max(2015, currentYear - 1);
  document.getElementById("endYear").value = currentYear;
  document.getElementById("startYear").max = currentYear;
  document.getElementById("endYear").max = currentYear;
  await restoreExpertSession();
  // Kiểm tra server + tự resume polling nếu cần
  await checkServerStatus();

  // Restore screen từ URL hash (ví dụ nếu user đang ở tab crawl thì giữ nguyên)
  const hash = location.hash.replace("#", "");
  if (currentExpert) {
    switchScreen("expert");
  } else if (hash && document.getElementById(`screen-${hash}`)) {
    switchScreen(hash);
  } else {
    switchScreen("dashboard");
  }

  // Poll server status mỗi 10 giây để cập nhật status dot + tự resume nếu cần
  setInterval(checkServerStatus, 10000);
});
