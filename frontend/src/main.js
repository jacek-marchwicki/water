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

let latestBattery = null;

function renderBattery(battery, charging) {
  latestBattery = { battery, charging };
  if (typeof document === "undefined") return;

  const pctEl = document.getElementById("today-battery");
  const unitEl = document.getElementById("today-battery-unit");
  const barEl = document.getElementById("battery-bar");
  const badgeEl = document.getElementById("battery-charging-badge");
  const statusEl = document.getElementById("battery-status-text");

  const headerPill = document.getElementById("header-battery");
  const headerVal = document.getElementById("header-battery-val");
  const headerIcon = document.getElementById("header-battery-icon");

  if (battery === null || battery === undefined || isNaN(Number(battery))) {
    if (pctEl) pctEl.textContent = "—";
    if (unitEl) unitEl.style.display = "none";
    if (barEl) barEl.style.width = "0%";
    if (badgeEl) badgeEl.style.display = "none";
    if (statusEl) {
      statusEl.textContent = "Waiting for sync";
      statusEl.style.color = "var(--text-dim)";
    }
    if (headerPill) headerPill.style.display = "none";
    return;
  }

  const pct = Math.max(0, Math.min(100, Math.round(Number(battery))));
  const isCharging = Boolean(charging);

  if (pctEl) {
    if (firstLoad) {
      pctEl.textContent = String(pct);
    } else {
      animateValue(pctEl, String(pct));
    }
  }
  if (unitEl) unitEl.style.display = "inline";

  // Battery level bar with contextual styling
  if (barEl) {
    barEl.style.width = `${pct}%`;
    if (isCharging) {
      barEl.style.background = "#38bdf8";
    } else if (pct <= 20) {
      barEl.style.background = "var(--red)";
    } else if (pct <= 40) {
      barEl.style.background = "#fbbf24";
    } else {
      barEl.style.background = "var(--green)";
    }
  }

  // Charging badge
  if (badgeEl) {
    badgeEl.style.display = isCharging ? "inline-flex" : "none";
  }

  // Card status text
  if (statusEl) {
    if (isCharging) {
      statusEl.textContent = "⚡ Charging";
      statusEl.style.color = "#38bdf8";
    } else if (pct <= 20) {
      statusEl.textContent = "Low Battery";
      statusEl.style.color = "var(--red)";
    } else {
      statusEl.textContent = "Not charging";
      statusEl.style.color = "var(--text-dim)";
    }
  }

  // Header pill (persistent across all pages)
  if (headerPill) {
    headerPill.style.display = "inline-flex";
    headerPill.className = "battery-pill" + (isCharging ? " charging" : pct <= 20 ? " low" : "");
    if (headerVal) headerVal.textContent = `${pct}%`;
    if (headerIcon) {
      headerIcon.textContent = isCharging ? "⚡" : (pct <= 20 ? "🪫" : "🔋");
    }
    headerPill.title = `Battery: ${pct}%${isCharging ? " (Charging)" : ""}`;
  }
}

// --- Pacing & Schedule Progress ---
let latestPacing = null;

function renderPacing(data, schedule = null) {
  if (!data) return;

  const total = Number(data.total_ml ?? data.today_total_ml ?? 0);
  const expected = Number(data.expected_ml ?? 0);
  const behind = Number(data.behind_ml ?? (data.to_reach_expected_ml ?? Math.max(0, expected - total)));
  const diff = expected - total;
  const goal = Number(data.goal_ml ?? currentGoal ?? 1800);

  const wakeInput = typeof document !== "undefined" ? document.getElementById("sched-wake-input") : null;
  const wakeTime = (schedule && schedule.wake_time) || (data && data.wake_time) || (wakeInput ? wakeInput.value : "08:00");

  const pacePct = expected > 0
    ? Math.min(100, Math.round((total / expected) * 100))
    : (total > 0 ? 100 : 0);

  latestPacing = {
    total_ml: total,
    expected_ml: expected,
    behind_ml: behind,
    goal_ml: goal,
    diff: diff,
    pace_pct: pacePct,
    on_track: diff <= 0,
    wake_time: wakeTime,
  };

  if (typeof document === "undefined") return;

  // 1. Stat Card Elements
  const expectedEl = document.getElementById("pacing-expected-ml");
  const barEl = document.getElementById("pacing-bar");
  const statusEl = document.getElementById("pacing-status-text");

  if (expectedEl) {
    if (firstLoad) {
      expectedEl.textContent = String(expected);
    } else {
      animateValue(expectedEl, String(expected));
    }
  }

  if (barEl) {
    barEl.style.width = `${pacePct}%`;
    barEl.style.background = diff > 0 ? "#fbbf24" : "var(--green)";
  }

  if (statusEl) {
    if (diff > 0) {
      statusEl.textContent = `Drink ${diff} ml to reach target`;
      statusEl.style.color = "#fbbf24";
    } else if (diff < 0) {
      statusEl.textContent = `✔ +${Math.abs(diff)} ml ahead of pace`;
      statusEl.style.color = "var(--green)";
    } else if (expected === 0) {
      statusEl.textContent = `Day starts at ${wakeTime}`;
      statusEl.style.color = "var(--text-dim)";
    } else {
      statusEl.textContent = "✔ On target pace";
      statusEl.style.color = "var(--green)";
    }
  }

  // 2. Smart Reminders Card Pacing Elements
  const smartExpectedEl = document.getElementById("smart-pacing-expected");
  const smartNeededEl = document.getElementById("smart-pacing-needed");
  const smartPill = document.getElementById("smart-pacing-status-pill");

  if (smartExpectedEl) {
    smartExpectedEl.textContent = `${expected} mL`;
  }
  if (smartNeededEl) {
    smartNeededEl.textContent = diff > 0 ? `${diff} mL` : "0 mL (On Track)";
    smartNeededEl.style.color = diff > 0 ? "#fbbf24" : "#4ade80";
  }
  if (smartPill) {
    if (diff > 0) {
      smartPill.textContent = `Behind (${diff} mL)`;
      smartPill.style.background = "rgba(251,191,36,0.15)";
      smartPill.style.color = "#fbbf24";
    } else if (diff < 0) {
      smartPill.textContent = `Ahead (+${Math.abs(diff)} mL)`;
      smartPill.style.background = "rgba(34,197,94,0.15)";
      smartPill.style.color = "#4ade80";
    } else {
      smartPill.textContent = "On Track";
      smartPill.style.background = "rgba(34,197,94,0.15)";
      smartPill.style.color = "#4ade80";
    }
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
    loadSmartReminders();

    latestStatus = status;
    renderStatus();

    const battery = (status && status.battery !== undefined && status.battery !== null)
      ? status.battery
      : (data && data.battery !== undefined && data.battery !== null ? data.battery : null);
    const charging = (status && status.charging !== undefined && status.charging !== null)
      ? status.charging
      : (data && data.charging !== undefined && data.charging !== null ? data.charging : null);

    renderBattery(battery, charging);
    renderPacing(data);

    const mlEl = document.getElementById("today-ml");
    const sipsEl = document.getElementById("today-sips");
    const pctEl = document.getElementById("today-pct");

    if (firstLoad) {
      mlEl.textContent = data.total_ml;
      sipsEl.textContent = data.sip_count;
      pctEl.textContent = data.goal_pct;
    } else {
      animateValue(mlEl, String(data.total_ml));
      animateValue(sipsEl, String(data.sip_count));
      animateValue(pctEl, String(data.goal_pct));
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
        tbody.innerHTML = `<tr><td colspan="3" class="empty-state">no sips recorded yet today</td></tr>`;
      } else {
        const reversed = data.sips.slice().reverse();
        tbody.innerHTML = reversed
          .map((s, i) => {
            const d = parseDate(s.timestamp);
            const t = d ? d.toLocaleTimeString() : s.timestamp;
            return `<tr class="animate-in" style="animation-delay:${i * 0.03}s">
              <td>${t}</td>
              <td>${s.intake_ml} ml</td>
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
        `<tr><td colspan="3" class="error-state">failed to load — retrying</td></tr>`;
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
    const activeNote = document.getElementById("smart-active-hours-note");
    if (activeNote && data.wake_time && data.sleep_time) {
      activeNote.textContent = `${data.wake_time} - ${data.sleep_time}`;
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
      const activeNote = document.getElementById("smart-active-hours-note");
      if (activeNote) activeNote.textContent = `${wake} - ${sleep}`;
    } else {
      showScheduleFeedback("❌ Failed to save", true);
    }
  } catch (e) {
    console.error("Save schedule error:", e);
    showScheduleFeedback("❌ Network error", true);
  }
};

// --- Smart Hydration Glow Reminders ---
let smartRemindersLoaded = false;

root.updateSmartToggleText = function(checked) {
  const lbl = typeof document !== "undefined" ? document.getElementById("smart-toggle-label") : null;
  if (lbl) {
    lbl.textContent = checked ? "Smart Glow On" : "Smart Glow Off";
    lbl.style.color = checked ? "var(--accent)" : "var(--text-dim)";
  }
};

root.renderSmartBadge = function(data) {
  const badge = typeof document !== "undefined" ? document.getElementById("smart-reminders-badge") : null;
  if (!badge) return;
  if (!data || !data.enabled) {
    badge.textContent = "Disabled";
    badge.style.background = "rgba(255,255,255,0.08)";
    badge.style.color = "var(--text-dim)";
  } else if (data.state === "outside_hours") {
    badge.textContent = "🌙 Night (Quiet)";
    badge.style.background = "rgba(148,163,184,0.15)";
    badge.style.color = "#94a3b8";
  } else if (data.state === "snoozed") {
    const minLeft = Math.max(1, Math.ceil((data.snooze_remaining_seconds || 0) / 60));
    badge.textContent = `💤 Snoozed (${minLeft}m left)`;
    badge.style.background = "rgba(56,189,248,0.15)";
    badge.style.color = "#38bdf8";
  } else if (data.state === "auto_off") {
    badge.textContent = "🚪 Away (Silenced)";
    badge.style.background = "rgba(244,63,94,0.15)";
    badge.style.color = "#f43f5e";
  } else if (data.state === "escalated") {
    badge.textContent = "🌈 Escalated Glow";
    badge.style.background = "rgba(168,85,247,0.2)";
    badge.style.color = "#c084fc";
  } else if (data.state === "gentle") {
    badge.textContent = "💡 Gentle Reminder";
    badge.style.background = "rgba(234,179,8,0.2)";
    badge.style.color = "#facc15";
  } else if (data.state === "on_track") {
    badge.textContent = "🟢 On Track";
    badge.style.background = "rgba(34,197,94,0.15)";
    badge.style.color = "#4ade80";
  } else {
    const idleMin = Math.round(data.idle_minutes || 0);
    badge.textContent = `🟢 Idle (${idleMin}m idle)`;
    badge.style.background = "rgba(34,197,94,0.15)";
    badge.style.color = "#4ade80";
  }
};

async function loadSmartReminders() {
  try {
    const res = await fetch("./api/smart-reminders");
    if (!res.ok) return;
    const data = await res.json();
    root.renderSmartBadge(data);
    if (data && data.expected_ml !== undefined) {
      renderPacing(data);
    }

    if (typeof document === "undefined") return;
    const activeNote = document.getElementById("smart-active-hours-note");
    if (activeNote && data.wake_time && data.sleep_time) {
      activeNote.textContent = `${data.wake_time} - ${data.sleep_time}`;
    }

    if (!smartRemindersLoaded) {
      smartRemindersLoaded = true;
      const toggle = document.getElementById("smart-reminder-toggle");
      const intervalSelect = document.getElementById("smart-interval-select");
      const gentleModeSelect = document.getElementById("smart-gentle-mode-select");
      const repeatSelect = document.getElementById("smart-repeat-select");
      const escalationSelect = document.getElementById("smart-escalation-select");
      const escalatedModeSelect = document.getElementById("smart-escalated-mode-select");
      const snoozeSelect = document.getElementById("smart-snooze-select");
      const autooffSelect = document.getElementById("smart-autooff-select");
      const behindOnlyCheckbox = document.getElementById("smart-behind-only-checkbox");

      if (toggle) {
        toggle.checked = Boolean(data.enabled);
        root.updateSmartToggleText(toggle.checked);
      }
      if (intervalSelect && data.sip_interval_min) intervalSelect.value = String(data.sip_interval_min);
      if (gentleModeSelect && data.gentle_mode) gentleModeSelect.value = data.gentle_mode;
      if (repeatSelect && data.gentle_repeat_min) repeatSelect.value = String(data.gentle_repeat_min);
      if (escalationSelect && data.escalation_delay_min) escalationSelect.value = String(data.escalation_delay_min);
      if (escalatedModeSelect && data.escalated_mode) escalatedModeSelect.value = data.escalated_mode;
      if (snoozeSelect && data.snooze_min) snoozeSelect.value = String(data.snooze_min);
      if (autooffSelect && data.auto_off_min) autooffSelect.value = String(data.auto_off_min);
      if (behindOnlyCheckbox) behindOnlyCheckbox.checked = Boolean(data.behind_only);
    }
  } catch (e) {
    console.error("Failed to load smart reminders:", e);
  }
}

function showSmartScheduleFeedback(text, isError = false) {
  const fb = typeof document !== "undefined" ? document.getElementById("smart-sched-feedback") : null;
  if (!fb) return;
  fb.textContent = text;
  fb.style.color = isError ? "var(--red)" : "#4ade80";
  fb.style.display = "inline";
  setTimeout(() => {
    fb.style.display = "none";
  }, 4000);
}

root.saveSmartReminders = async function() {
  const enabled = typeof document !== "undefined" ? Boolean(document.getElementById("smart-reminder-toggle")?.checked) : false;
  const sip_interval_min = typeof document !== "undefined" ? parseInt(document.getElementById("smart-interval-select")?.value || "40") : 40;
  const gentle_mode = typeof document !== "undefined" ? document.getElementById("smart-gentle-mode-select")?.value || "default" : "default";
  const gentle_repeat_min = typeof document !== "undefined" ? parseInt(document.getElementById("smart-repeat-select")?.value || "3") : 3;
  const escalation_delay_min = typeof document !== "undefined" ? parseInt(document.getElementById("smart-escalation-select")?.value || "15") : 15;
  const escalated_mode = typeof document !== "undefined" ? document.getElementById("smart-escalated-mode-select")?.value || "rainbow" : "rainbow";
  const snooze_min = typeof document !== "undefined" ? parseInt(document.getElementById("smart-snooze-select")?.value || "10") : 10;
  const auto_off_min = typeof document !== "undefined" ? parseInt(document.getElementById("smart-autooff-select")?.value || "60") : 60;
  const behind_only = typeof document !== "undefined" ? Boolean(document.getElementById("smart-behind-only-checkbox")?.checked) : false;

  try {
    const res = await fetch("./api/smart-reminders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled,
        sip_interval_min,
        gentle_mode,
        gentle_repeat_min,
        escalation_delay_min,
        escalated_mode,
        snooze_min,
        auto_off_min,
        behind_only,
      })
    });
    if (res.ok) {
      const data = await res.json();
      root.renderSmartBadge(data);
      showSmartScheduleFeedback("✔ Smart glow settings saved!");
    } else {
      showSmartScheduleFeedback("Failed to save settings", true);
    }
  } catch (e) {
    showSmartScheduleFeedback("Connection error", true);
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
root.renderBattery = renderBattery;
root.getLatestBattery = () => latestBattery;
root.renderPacing = renderPacing;
root.getLatestPacing = () => latestPacing;

