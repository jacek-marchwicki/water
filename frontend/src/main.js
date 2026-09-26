const API = "./api";
const POLL_INTERVAL = 10000;
let pollTimer = null;
let currentGoal = 1800;

let latestStatus = null;
let statusTimer = null;

function renderStatus() {
  if (!latestStatus) return;
  const el = typeof document !== "undefined" ? document.getElementById("status") : null;
  if (!el) return;

  const ago = timeAgo(latestStatus.last_seen);
  if (latestStatus.state === "connected") {
    el.className = "status online";
    el.textContent = ago && ago !== "never" ? `connected — ${ago}` : "connected";
  } else if (latestStatus.state === "scanning") {
    el.className = "status online";
    el.textContent = `scanning — ${latestStatus.detail || (ago !== "never" ? ago : "searching")}`;
  } else if (latestStatus.online) {
    el.className = "status online";
    el.textContent = ago && ago !== "never" ? `synced ${ago}` : "synced";
  } else {
    el.className = "status offline";
    el.textContent = `offline — ${latestStatus.state || "unknown"}${ago && ago !== "never" ? ` (${ago})` : ""}`;
  }
}

function startStatusTimer() {
  stopStatusTimer();
  statusTimer = setInterval(renderStatus, 1000);
}

function stopStatusTimer() {
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
  }
}

// --- Visibility-based polling ---
function startPolling() {
  stopPolling();
  pollTimer = setInterval(loadToday, POLL_INTERVAL);
  startStatusTimer();
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  stopStatusTimer();
}

// --- Navigation & Listeners ---
if (typeof document !== "undefined") {
  document.querySelectorAll(".nav-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(`page-${btn.dataset.page}`).classList.add("active");

      if (btn.dataset.page === "history") loadHistory();
    });
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopPolling();
    } else {
      loadToday();
      startPolling();
    }
  });
}

// --- Loading helpers ---
function showSkeleton() {
  document.querySelectorAll(".stat-value").forEach((el) => el.classList.add("skeleton"));
  const tbody = document.getElementById("sip-table");
  tbody.innerHTML = Array.from({ length: 3 }, () =>
    `<tr class="skeleton-row">
      <td><span class="skeleton">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</span></td>
      <td><span class="skeleton">&nbsp;&nbsp;&nbsp;&nbsp;</span></td>
      <td><span class="skeleton">&nbsp;&nbsp;&nbsp;</span></td>
      <td><div class="bar"><div class="bar-fill skeleton" style="width:60%"></div></div></td>
    </tr>`
  ).join("");
}

function hideSkeleton() {
  document.querySelectorAll(".stat-value").forEach((el) => el.classList.remove("skeleton"));
}

function animateValue(el, value) {
  el.classList.add("updating");
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      if (typeof value === "string") {
        el.textContent = value;
      } else {
        el.innerHTML = value;
      }
      el.classList.remove("updating");
    });
  });
}

// --- Today ---
let firstLoad = true;
let lastSipKey = "";

async function loadToday() {
  if (firstLoad) showSkeleton();

  try {
    const [todayRes, statusRes] = await Promise.all([
      fetch(`${API}/today`),
      fetch(`${API}/status`),
    ]);
    const data = await todayRes.json();
    const status = await statusRes.json();

    hideSkeleton();
    if (!scheduleLoaded) loadSchedule();

    latestStatus = status;
    renderStatus();

    const mlEl = document.getElementById("today-ml");
    const sipsEl = document.getElementById("today-sips");
    const pctEl = document.getElementById("today-pct");
    const tempEl = document.getElementById("today-temp");

    if (firstLoad) {
      mlEl.textContent = data.total_ml;
      sipsEl.textContent = data.sip_count;
      pctEl.textContent = data.goal_pct;
      tempEl.textContent = data.last_temp_c != null ? data.last_temp_c : "—";
    } else {
      animateValue(mlEl, String(data.total_ml));
      animateValue(sipsEl, String(data.sip_count));
      animateValue(pctEl, String(data.goal_pct));
      animateValue(tempEl, data.last_temp_c != null ? String(data.last_temp_c) : "—");
    }

    document.getElementById("today-bar").style.width = `${data.goal_pct}%`;
    if (data.goal_ml) {
      currentGoal = Number(data.goal_ml);
      if (document.getElementById("display-goal")) {
        document.getElementById("display-goal").innerText = data.goal_ml;
      }
    }

    const tbody = document.getElementById("sip-table");
    const sipKey = data.sips.map((s) => s.timestamp).join(",");

    if (sipKey !== lastSipKey) {
      lastSipKey = sipKey;
      if (data.sips.length === 0) {
        tbody.innerHTML = `<tr><td colspan="4" class="empty-state">no sips recorded yet today</td></tr>`;
      } else {
        const reversed = data.sips.slice().reverse();
        tbody.innerHTML = reversed
          .map((s, i) => {
            const d = parseDate(s.timestamp);
            const t = d ? d.toLocaleTimeString() : s.timestamp;
            return `<tr class="animate-in" style="animation-delay:${i * 0.03}s">
              <td>${t}</td>
              <td>${s.intake_ml} ml</td>
              <td>${s.temp_c ?? "—"}°C</td>
              <td><button class="delete-btn" onclick="deleteSip(${s.id}, '${s.timestamp}')">🗑️ Delete</button></td>
            </tr>`;
          })
          .join("");
      }
    }

    firstLoad = false;
  } catch (e) {
    console.error("Failed to load today:", e);
    hideSkeleton();
    if (firstLoad) {
      document.getElementById("sip-table").innerHTML =
        `<tr><td colspan="4" class="error-state">failed to load — retrying</td></tr>`;
    }
  }
}

// --- History ---
let historyLoaded = false;
let historyChart = null;

async function loadHistory() {
  try {
    const res = await fetch(`${API}/history?days=90`);
    const data = await res.json();
    historyLoaded = true;

    if (data.days.length === 0) {
      document.getElementById("hist-avg").textContent = "—";
      document.getElementById("hist-best").textContent = "—";
      document.getElementById("hist-streak").textContent = "—";
      document.getElementById("heatmap").innerHTML =
        `<div class="empty-state">no history yet — drink some water!</div>`;
      return;
    }

    document.getElementById("hist-avg").textContent = data.avg_daily_ml;
    document.getElementById("hist-best").textContent = data.best_day_ml;
    document.getElementById("hist-streak").textContent = data.current_streak;

    const goal = Number(data.goal_ml) || currentGoal || Number(document.getElementById("display-goal")?.innerText) || 1800;
    currentGoal = goal;

    // Bar chart with goal line
    const ctx = document.getElementById("history-chart").getContext("2d");
    if (historyChart) historyChart.destroy();

    const last30 = data.days.slice(-30);
    historyChart = new Chart(ctx, {
      type: "bar",
      data: {
        labels: last30.map((d) => {
          if (d.date && typeof d.date === "string" && d.date.includes("-")) {
            const parts = d.date.split("-");
            if (parts.length === 3) return `${Number(parts[1])}/${Number(parts[2])}`;
          }
          const dt = parseDate(d.date) || new Date(d.date);
          return `${dt.getMonth() + 1}/${dt.getDate()}`;
        }),
        datasets: [
          {
            label: "Intake (ml)",
            data: last30.map((d) => d.total_ml),
            backgroundColor: last30.map((d) =>
              d.total_ml >= goal
                ? "rgba(102, 187, 106, 0.7)"
                : "rgba(79, 195, 247, 0.7)"
            ),
            borderRadius: 3,
            order: 1,
          },
          {
            label: "Goal",
            type: "line",
            data: last30.map(() => goal),
            borderColor: "rgba(136, 136, 136, 0.4)",
            borderDash: [6, 4],
            borderWidth: 1.5,
            pointRadius: 0,
            pointHitRadius: 0,
            fill: false,
            order: 0,
          },
        ],
      },
      options: {
        responsive: true,
        interaction: { intersect: false, mode: "index" },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) =>
                ctx.dataset.label === "Goal" ? null : `${ctx.raw} ml`,
            },
          },
        },
        scales: {
          x: {
            ticks: { color: "#888", font: { size: 9, family: "monospace" } },
            grid: { display: false },
          },
          y: {
            ticks: { color: "#888", font: { size: 10, family: "monospace" } },
            grid: { color: "#1e1e1e" },
          },
        },
      },
    });

    // Heatmap
    buildHeatmap(data.days, goal);
  } catch (e) {
    console.error("Failed to load history:", e);
    document.getElementById("heatmap").innerHTML =
      `<div class="error-state">failed to load history</div>`;
  }
}

function buildHeatmap(days, goal = currentGoal || 1800) {
  const container = document.getElementById("heatmap");
  const dayMap = {};
  for (const d of days) dayMap[d.date] = d.total_ml;

  const today = new Date();
  const start = new Date(today.getFullYear(), 0, 1); // Jan 1 of current year
  // Align to Sunday
  start.setDate(start.getDate() - start.getDay());

  const end = new Date(today.getFullYear(), 11, 31); // Dec 31 of current year

  // Build month labels
  const months = [];
  const cursor = new Date(start);
  let weekIndex = 0;
  let lastMonth = -1;

  while (cursor <= end) {
    if (cursor.getDay() === 0) {
      const m = cursor.getMonth();
      if (m !== lastMonth) {
        months.push({ index: weekIndex, label: cursor.toLocaleString("en", { month: "short" }) });
        lastMonth = m;
      }
      weekIndex++;
    }
    cursor.setDate(cursor.getDate() + 1);
  }

  const cellSize = 12;
  const gap = 3;
  const colWidth = cellSize + gap;
  const totalWeeks = weekIndex;

  const monthLabels = months.map((m) =>
    `<span style="position:absolute;left:${m.index * colWidth}px">${m.label}</span>`
  ).join("");

  // Build cells
  const cells = [];
  const cursor2 = new Date(start);
  while (cursor2 <= end) {
    const y = cursor2.getFullYear();
    const m = String(cursor2.getMonth() + 1).padStart(2, "0");
    const dt = String(cursor2.getDate()).padStart(2, "0");
    const key = `${y}-${m}-${dt}`;
    const ml = dayMap[key] || 0;
    let level = "";
    if (ml > 0 && ml < goal * 0.4) level = "l1";
    else if (ml >= goal * 0.4 && ml < goal * 0.75) level = "l2";
    else if (ml >= goal * 0.75 && ml < goal) level = "l3";
    else if (ml >= goal) level = "l4";

    cells.push(`<div class="heatmap-cell ${level}"><span class="tip">${key}: ${ml}ml</span></div>`);
    cursor2.setDate(cursor2.getDate() + 1);
  }

  const legend = `
    <div class="heatmap-legend">
      <span>less</span>
      <div class="swatch" style="background:var(--border)"></div>
      <div class="swatch" style="background:rgba(79,195,247,0.2)"></div>
      <div class="swatch" style="background:rgba(79,195,247,0.4)"></div>
      <div class="swatch" style="background:rgba(79,195,247,0.6)"></div>
      <div class="swatch" style="background:var(--accent)"></div>
      <span>more</span>
    </div>`;

  container.innerHTML = `
    <div style="position:relative;height:16px;margin-bottom:4px;min-width:${totalWeeks * colWidth}px" class="heatmap-months">${monthLabels}</div>
    <div class="heatmap-grid">${cells.join("")}</div>
    ${legend}`;
}

// --- Utils ---
function parseDate(iso) {
  if (!iso) return null;
  if (iso instanceof Date) return isNaN(iso.getTime()) ? null : iso;
  if (typeof iso === "number") {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? null : d;
  }
  const s = String(iso).trim();
  if (!s || s === "null" || s === "undefined") return null;

  // Numeric timestamp in seconds or milliseconds
  if (/^\d{10,13}$/.test(s)) {
    const ms = s.length === 10 ? Number(s) * 1000 : Number(s);
    const d = new Date(ms);
    return isNaN(d.getTime()) ? null : d;
  }

  // If already contains timezone indicator (Z, +HH:MM, -HH:MM, +HHMM, -HHMM)
  if (/(?:Z|[+-]\d{2}(?::?\d{2})?)$/i.test(s)) {
    const d = new Date(s);
    if (!isNaN(d.getTime())) return d;
  }

  // SQLite UTC format "YYYY-MM-DD HH:MM:SS" or naive ISO format
  const normalized = s.replace(" ", "T");
  const utcDate = new Date(normalized + "Z");
  if (!isNaN(utcDate.getTime())) return utcDate;

  const directDate = new Date(s);
  if (!isNaN(directDate.getTime())) return directDate;

  return null;
}

function timeAgo(iso) {
  const d = parseDate(iso);
  if (!d) return "never";
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (isNaN(diff)) return "never";
  if (diff < 60) return `${diff}s ago`;
  const m = Math.floor(diff / 60);
  const s = diff % 60;
  if (diff < 3600) return `${m}m ${s}s ago`;
  const h = Math.floor(diff / 3600);
  const remM = m % 60;
  if (diff < 86400) return `${h}h ${remM}m ${s}s ago`;
  const day = Math.floor(diff / 86400);
  const remH = h % 24;
  return `${day}d ${remH}h ago`;
}

// --- Init ---
if (typeof window !== "undefined" && typeof document !== "undefined") {
  loadToday();
  startPolling();
}

// --- Interactive Actions ---
const root = typeof window !== "undefined" ? window : globalThis;

root.updateSliderText = function(val) {
  const el = typeof document !== "undefined" ? document.getElementById("slider-val") : null;
  if (el) el.innerText = val;
};

root.logSip = async function(ml) {
  try {
    const res = await fetch("./commands/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ml: ml })
    });
    const data = await res.json();
    if (data.ok) {
      historyLoaded = false;
      loadToday();
      if (typeof document !== "undefined" && document.getElementById("page-history")?.classList.contains("active")) {
        loadHistory();
      }
    }
  } catch (e) {
    console.error("Log sip error", e);
  }
};

root.logCustomSip = async function() {
  const input = typeof document !== "undefined" ? document.getElementById("custom-slider") : null;
  const val = input ? parseInt(input.value) : 0;
  await root.logSip(val);
};

root.deleteSip = async function(id, timestamp) {
  if (typeof confirm !== "undefined" && !confirm("Are you sure you want to delete this sip entry? It will update Home Assistant and subtract the amount from your physical bottle display.")) return;
  try {
    const res = await fetch("./commands/delete_sip", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id, timestamp: timestamp })
    });
    const data = await res.json();
    if (data.ok) {
      historyLoaded = false;
      loadToday();
      if (typeof document !== "undefined" && document.getElementById("page-history")?.classList.contains("active")) {
        loadHistory();
      }
    }
  } catch (e) {
    console.error("Delete sip error", e);
  }
};

root.flashLED = async function() {
  await fetch("./commands/flash", { method: "POST" });
  if (typeof alert !== "undefined") alert("Flash command queued!");
};

root.setLED = async function() {
  const mode = typeof document !== "undefined" ? document.getElementById("led-select")?.value : "breathe";
  await fetch("./commands/led", { method: "POST", body: JSON.stringify({ mode: mode, color: "blue" }) });
};

root.setGoal = async function(ml) {
  try {
    currentGoal = Number(ml);
    const displayEl = typeof document !== "undefined" ? document.getElementById("display-goal") : null;
    if (displayEl) displayEl.innerText = ml;
    historyLoaded = false;

    const res = await fetch("./commands/goal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ml: ml })
    });
    const data = await res.json();
    if (data.ok) {
      if (data.goal_ml) currentGoal = Number(data.goal_ml);
      loadToday();
      if (typeof document !== "undefined" && document.getElementById("page-history")?.classList.contains("active")) {
        loadHistory();
      }
    }
  } catch (e) {
    console.error("Set goal error", e);
  }
};

root.promptCustomGoal = async function() {
  const curr = typeof document !== "undefined" ? document.getElementById("display-goal")?.innerText || "1800" : "1800";
  const val = typeof prompt !== "undefined" ? prompt("Enter daily hydration goal in mL:", curr) : null;
  if (val && !isNaN(val) && parseInt(val) > 0) {
    await root.setGoal(parseInt(val));
  }
};

// --- Active Day Schedule & Reminders ---
let scheduleLoaded = false;

root.updateToggleText = function(checked) {
  const lbl = typeof document !== "undefined" ? document.getElementById("sched-toggle-label") : null;
  if (lbl) {
    lbl.textContent = checked ? "Reminders On" : "Reminders Off";
    lbl.style.color = checked ? "var(--accent)" : "var(--text-dim)";
  }
};

async function loadSchedule() {
  try {
    const res = await fetch("./api/schedule");
    if (!res.ok) return;
    const data = await res.json();
    scheduleLoaded = true;

    if (typeof document === "undefined") return;
    const wakeInput = document.getElementById("sched-wake-input");
    const sleepInput = document.getElementById("sched-sleep-input");
    const intervalSelect = document.getElementById("sched-interval-select");
    const toggle = document.getElementById("sched-reminder-toggle");

    if (wakeInput && data.wake_time) wakeInput.value = data.wake_time;
    if (sleepInput && data.sleep_time) sleepInput.value = data.sleep_time;
    if (intervalSelect && data.interval_min) intervalSelect.value = String(data.interval_min);
    if (toggle) {
      toggle.checked = Boolean(data.reminder_on);
      root.updateToggleText(toggle.checked);
    }
  } catch (e) {
    console.error("Failed to load schedule:", e);
  }
}

function showScheduleFeedback(text, isError = false) {
  const fb = typeof document !== "undefined" ? document.getElementById("sched-feedback") : null;
  if (!fb) return;
  fb.textContent = text;
  fb.style.color = isError ? "var(--red)" : "#4ade80";
  fb.style.display = "inline";
  setTimeout(() => {
    fb.style.display = "none";
  }, 4000);
}

root.saveSchedule = async function() {
  const wake = typeof document !== "undefined" ? document.getElementById("sched-wake-input")?.value || "08:00" : "08:00";
  const sleep = typeof document !== "undefined" ? document.getElementById("sched-sleep-input")?.value || "20:00" : "20:00";
  const interval = typeof document !== "undefined" ? parseInt(document.getElementById("sched-interval-select")?.value || "60") : 60;
  const on = typeof document !== "undefined" ? Boolean(document.getElementById("sched-reminder-toggle")?.checked) : false;

  try {
    const res = await fetch("./commands/schedule", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wake, sleep, interval, on })
    });
    const data = await res.json();
    if (data && data.ok) {
      showScheduleFeedback("✔ Schedule saved & synced!");
    } else {
      showScheduleFeedback("❌ Failed to save", true);
    }
  } catch (e) {
    console.error("Save schedule error:", e);
    showScheduleFeedback("❌ Network error", true);
  }
};

root.syncClock = async function() {
  try {
    const res = await fetch("./commands/time", { method: "POST" });
    const data = await res.json();
    if (data && data.ok) {
      showScheduleFeedback("✔ Clock sync queued!");
    } else {
      showScheduleFeedback("❌ Failed to sync time", true);
    }
  } catch (e) {
    console.error("Sync time error:", e);
    showScheduleFeedback("❌ Network error", true);
  }
};

root.parseDate = parseDate;
root.timeAgo = timeAgo;
root.renderStatus = renderStatus;
root.getLatestStatus = () => latestStatus;

