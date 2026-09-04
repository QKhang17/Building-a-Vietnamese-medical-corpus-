const API = "/api/annotation";
const token = localStorage.getItem("mednlp_expert_token") || "";
const TYPES = ["Bệnh lý", "Triệu chứng", "Điều trị", "Xét nghiệm", "Hình ảnh", "Sinh lý"];
let assignments = [];
let current = null;
let entities = [];
let dirty = false;
let activeAccumulatedMs = 0;
let lastActiveTick = Date.now();
let lastInteractionAt = Date.now();
const ACTIVE_IDLE_LIMIT_MS = 60_000;

const byId = id => document.getElementById(id);
const escapeHtml = value => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const clone = value => JSON.parse(JSON.stringify(value));

function toast(message, error = false) {
  const node = byId("toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.hidden = false;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => { node.hidden = true; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let data = {};
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const error = new Error(data.detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function uuid() {
  return crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function tickActiveTime() {
  const now = Date.now();
  if (current && !document.hidden && now - lastInteractionAt <= ACTIVE_IDLE_LIMIT_MS) {
    activeAccumulatedMs += Math.max(0, now - lastActiveTick);
  }
  lastActiveTick = now;
}

function noteInteraction() {
  tickActiveTime();
  lastInteractionAt = Date.now();
}

function activeSeconds() {
  tickActiveTime();
  const seconds = Math.min(14400, Math.max(0, Math.round(activeAccumulatedMs / 1000)));
  activeAccumulatedMs = 0;
  return seconds;
}

async function restoreSession() {
  if (!token) {
    byId("authWarning").hidden = false;
    document.querySelector("main").setAttribute("aria-disabled", "true");
    return false;
  }
  try {
    const response = await fetch("/api/expert/me", { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error();
    const expert = await response.json();
    byId("sessionLabel").textContent = `${expert.full_name} (${expert.username})`;
    return true;
  } catch {
    byId("authWarning").hidden = false;
    return false;
  }
}

async function loadAssignments() {
  const params = new URLSearchParams({ limit: "500" });
  if (byId("roleFilter").value) params.set("role", byId("roleFilter").value);
  if (byId("statusFilter").value) params.set("status", byId("statusFilter").value);
  byId("assignmentList").innerHTML = '<div class="placeholder">Đang tải...</div>';
  try {
    const data = await api(`/assignments?${params}`);
    assignments = data.items || [];
    renderAssignments();
  } catch (error) {
    byId("assignmentList").innerHTML = `<div class="placeholder">${escapeHtml(error.message)}</div>`;
  }
}

function renderAssignments() {
  if (!assignments.length) {
    byId("assignmentList").innerHTML = '<div class="placeholder">Không có assignment phù hợp.</div>';
    return;
  }
  byId("assignmentList").innerHTML = assignments.map(item => `
    <div class="assignment-card ${current?.id === item.id ? "active" : ""}" data-id="${item.id}">
      <div class="assignment-title">${escapeHtml(item.title)}</div>
      <div class="assignment-meta">
        <span class="chip">${escapeHtml(item.split_name)}</span>
        <span class="chip ${item.annotation_mode}">${escapeHtml(item.annotation_mode)}</span>
        <span class="chip">${escapeHtml(item.assignment_role)}</span>
        <span class="chip">${escapeHtml(item.status)}</span>
      </div>
    </div>`).join("");
  document.querySelectorAll(".assignment-card").forEach(node => node.addEventListener("click", () => openAssignment(Number(node.dataset.id))));
}

async function openAssignment(id) {
  if (dirty && !confirm("Phiên hiện tại chưa lưu. Bạn vẫn muốn chuyển tài liệu?")) return;
  try {
    let detail = await api(`/assignments/${id}`);
    if (detail.status === "assigned") detail = await api(`/assignments/${id}/start`, { method: "POST" });
    current = detail;
    entities = clone(detail.entities || []);
    if (!entities.length && detail.annotation_mode === "assisted" && (detail.preannotation || []).length) {
      entities = clone(detail.preannotation).map(item => ({ ...item, client_id: item.client_id || uuid(), decision: item.decision || "proposed", version: 1 }));
      dirty = true;
    } else {
      dirty = false;
    }
    activeAccumulatedMs = 0;
    lastActiveTick = Date.now();
    lastInteractionAt = Date.now();
    renderEditor();
    renderAssignments();
  } catch (error) { toast(error.message, true); }
}

function renderEditor() {
  byId("emptyState").hidden = true;
  byId("editor").hidden = false;
  byId("projectLabel").textContent = `${current.project_name} · ${current.split_name}`;
  byId("documentTitle").textContent = current.title;
  byId("documentMeta").textContent = `Bài ${current.article_id} · ${current.publication_year || "không rõ năm"} · version ${current.entities_version}`;
  byId("statusBadge").textContent = `${current.assignment_role} / ${current.status}`;
  byId("sourceText").value = current.source_text || "";
  const blind = current.annotation_mode === "blind";
  byId("blindNotice").textContent = blind
    ? "Chế độ blind: nhãn máy và bản của chuyên gia còn lại được ẩn hoàn toàn."
    : current.assignment_role === "adjudicator"
      ? "Chế độ phân xử: chỉ mở sau khi hai bản độc lập đã được nộp và có bất đồng."
      : "Chế độ assisted: entity đề xuất chỉ là tiền nhãn; mọi quyết định của bạn được ghi audit.";
  byId("adjudicationPanel").hidden = current.assignment_role !== "adjudicator";
  renderSubmissions();
  renderEntities();
  const editable = ["assigned", "in_progress", "conflict"].includes(current.status);
  byId("saveButton").disabled = !editable;
  byId("undoButton").disabled = !editable;
  byId("submitButton").hidden = current.assignment_role !== "annotator";
  byId("submitButton").disabled = current.status !== "in_progress";
  byId("lockButton").hidden = current.assignment_role !== "adjudicator";
  byId("lockButton").disabled = !["conflict", "in_progress"].includes(current.status);
  byId("addSelection").disabled = !editable;
}

function renderSubmissions() {
  const panel = byId("submissionCards");
  if (current.assignment_role !== "adjudicator") { panel.innerHTML = ""; return; }
  const submissions = current.submissions || [];
  panel.innerHTML = submissions.length ? submissions.map((item, index) => `
    <div class="submission-card">
      <strong>${escapeHtml(item.expert_name)}</strong>
      <div class="document-meta">${escapeHtml(item.annotation_mode)} · ${item.active_seconds}s · ${item.entities.length} entity</div>
      <pre>${escapeHtml(JSON.stringify(item.entities, null, 2))}</pre>
      <button type="button" data-submission="${index}">Nạp bản này để phân xử</button>
    </div>`).join("") : '<div class="placeholder">Hai người gán nhãn chưa nộp đủ.</div>';
  panel.querySelectorAll("[data-submission]").forEach(button => button.addEventListener("click", () => {
    const selected = submissions[Number(button.dataset.submission)]?.entities || [];
    entities = clone(selected).map(item => ({ ...item, client_id: uuid(), source: "adjudicator", decision: item.decision === "rejected" ? "rejected" : "accepted", version: 1 }));
    dirty = true;
    renderEntities();
  }));
}

function updateSurface(entity) {
  const text = current.source_text || "";
  entity.start = Number(entity.start);
  entity.end = Number(entity.end);
  entity.surface = entity.start >= 0 && entity.end > entity.start && entity.end <= text.length ? text.slice(entity.start, entity.end) : "";
}

function renderEntities() {
  entities.sort((a, b) => Number(a.start) - Number(b.start) || Number(a.end) - Number(b.end));
  byId("entityCount").textContent = `${entities.length} entity`;
  byId("entityRows").innerHTML = entities.map((item, index) => `
    <tr data-index="${index}">
      <td><div class="span-inputs"><input data-field="start" type="number" min="0" value="${Number(item.start)}"><input data-field="end" type="number" min="1" value="${Number(item.end)}"></div></td>
      <td class="surface-cell">${escapeHtml(item.surface)}</td>
      <td><select data-field="type">${TYPES.map(type => `<option ${type === item.type ? "selected" : ""}>${type}</option>`).join("")}</select></td>
      <td><input data-field="code" value="${escapeHtml(item.code || "")}"></td>
      <td>${escapeHtml(item.source || "human")}</td>
      <td><select data-field="decision">${["proposed","accepted","modified","added","rejected"].map(value => `<option ${value === item.decision ? "selected" : ""}>${value}</option>`).join("")}</select></td>
      <td><input data-field="reason" value="${escapeHtml(item.reason || "")}"></td>
      <td><button type="button" data-delete="${index}" aria-label="Xóa entity">Xóa</button></td>
    </tr>`).join("");
  byId("entityRows").querySelectorAll("input,select").forEach(control => control.addEventListener("change", event => {
    const row = event.target.closest("tr");
    const entity = entities[Number(row.dataset.index)];
    const field = event.target.dataset.field;
    entity[field] = ["start", "end"].includes(field) ? Number(event.target.value) : event.target.value;
    if (["start", "end"].includes(field)) updateSurface(entity);
    if (
      ["start", "end", "type", "code"].includes(field)
      && ["ai", "dictionary", "ai+dictionary"].includes(entity.source)
      && ["proposed", "accepted"].includes(entity.decision)
    ) entity.decision = "modified";
    entity.version = Number(entity.version || 1) + 1;
    dirty = true;
    renderEntities();
  }));
  byId("entityRows").querySelectorAll("[data-delete]").forEach(button => button.addEventListener("click", () => {
    const index = Number(button.dataset.delete);
    const entity = entities[index];
    if (["ai", "dictionary", "ai+dictionary"].includes(entity.source)) {
      entity.decision = "rejected";
      entity.reason = entity.reason || "Xóa bởi chuyên gia";
      entity.version = Number(entity.version || 1) + 1;
    } else {
      entities.splice(index, 1);
    }
    dirty = true;
    renderEntities();
  }));
  renderHighlight();
}

function renderHighlight() {
  const text = current.source_text || "";
  const active = entities.filter(item => item.decision !== "rejected" && Number.isInteger(Number(item.start)) && Number.isInteger(Number(item.end)))
    .sort((a, b) => Number(a.start) - Number(b.start));
  let cursor = 0;
  const html = [];
  for (const item of active) {
    const start = Number(item.start), end = Number(item.end);
    if (start < cursor || start < 0 || end <= start || end > text.length) continue;
    html.push(escapeHtml(text.slice(cursor, start)));
    html.push(`<mark title="${escapeHtml(`${item.type} · ${item.code || "không mã"}`)}">${escapeHtml(text.slice(start, end))}</mark>`);
    cursor = end;
  }
  html.push(escapeHtml(text.slice(cursor)));
  byId("highlightPreview").innerHTML = html.join("");
}

function addSelection() {
  const source = byId("sourceText");
  const start = source.selectionStart, end = source.selectionEnd;
  if (end <= start) return toast("Hãy chọn một đoạn văn bản trước.", true);
  if (entities.some(item => item.decision !== "rejected" && start < Number(item.end) && end > Number(item.start))) return toast("Vùng chọn chồng lấn entity hiện có.", true);
  entities.push({ client_id:uuid(), start, end, surface:current.source_text.slice(start,end), type:byId("newEntityType").value, code:"", source:current.assignment_role === "adjudicator" ? "adjudicator" : "human", decision:"added", reason:"", version:1 });
  dirty = true;
  renderEntities();
}

async function saveCurrent() {
  if (!current) return;
  try {
    const data = await api(`/assignments/${current.id}/entities`, {
      method:"PUT",
      body:JSON.stringify({ expected_version:current.entities_version, entities, active_seconds_delta:activeSeconds() }),
    });
    current.entities_version = data.entities_version;
    current.status = data.status;
    entities = clone(data.entities);
    dirty = false;
    toast("Đã lưu phiên bản có audit.");
    renderEditor();
    await loadAssignments();
  } catch (error) {
    if (error.status === 409) toast("Có phiên mới hơn trên máy chủ. Hãy tải lại assignment.", true);
    else toast(error.message, true);
    throw error;
  }
}

async function submitCurrent() {
  if (dirty) await saveCurrent();
  if (!confirm("Nộp bản gán nhãn? Sau khi nộp bạn không thể sửa trực tiếp.")) return;
  try {
    const data = await api(`/assignments/${current.id}/submit`, { method:"POST", body:JSON.stringify({ expected_version:current.entities_version }) });
    toast(data.auto_locked ? "Hai bản giống nhau; snapshot vàng đã được khóa." : "Đã nộp bản gán nhãn.");
    await openAssignment(current.id);
    await loadAssignments();
  } catch (error) { toast(error.message, true); }
}

async function undoCurrent() {
  try {
    const data = await api(`/assignments/${current.id}/undo`, { method:"POST", body:JSON.stringify({ expected_version:current.entities_version }) });
    current.entities_version = data.entities_version;
    current.status = data.status;
    entities = clone(data.entities);
    dirty = false;
    toast("Đã hoàn tác lần lưu cuối.");
    renderEditor();
  } catch (error) { toast(error.message, true); }
}

async function lockCurrent() {
  if (dirty) await saveCurrent();
  if (!confirm("Khóa snapshot vàng? Thao tác này không thể hoàn tác.")) return;
  try {
    const data = await api(`/assignments/${current.id}/lock-adjudication`, { method:"POST", body:JSON.stringify({ expected_version:current.entities_version }) });
    toast(`Đã khóa bản vàng ${data.snapshot_sha256.slice(0,12)}…`);
    await openAssignment(current.id);
    await loadAssignments();
  } catch (error) { toast(error.message, true); }
}

byId("refreshQueue").addEventListener("click", loadAssignments);
byId("roleFilter").addEventListener("change", loadAssignments);
byId("statusFilter").addEventListener("change", loadAssignments);
byId("addSelection").addEventListener("click", addSelection);
byId("saveButton").addEventListener("click", () => saveCurrent().catch(() => {}));
byId("submitButton").addEventListener("click", () => submitCurrent().catch(() => {}));
byId("undoButton").addEventListener("click", undoCurrent);
byId("lockButton").addEventListener("click", () => lockCurrent().catch(() => {}));
window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });
setInterval(tickActiveTime, 1000);
for (const eventName of ["pointerdown", "keydown", "input", "change", "selectionchange"]) {
  document.addEventListener(eventName, noteInteraction, { passive: true });
}
document.addEventListener("visibilitychange", () => {
  lastActiveTick = Date.now();
  if (!document.hidden) lastInteractionAt = Date.now();
});

(async () => { if (await restoreSession()) await loadAssignments(); })();
