const API = "./api";
const POLL_INTERVAL = 10000;
let pollTimer = null;
let currentGoal = 1800;

// --- Navigation ---
document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".page").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`page-${btn.dataset.page}`).classList.add("active");

    if (btn.dataset.page === "history") loadHistory();
  });
});

// --- Visibility-based polling ---
function startPolling() {
  stopPolling();
  pollTimer = setInterval(loadToday, POLL_INTERVAL);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopPolling();
  } else {
    loadToday();
    startPolling();
  }
});

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

    const el = document.getElementById("status");
    if (status.state === "connected") {
      el.className = "status online";
      el.textContent = `connected — ${timeAgo(status.last_seen)}`;
    } else if (status.state === "scanning") {
      el.className = "status online";
      el.textContent = `scanning — ${status.detail || timeAgo(status.last_seen)}`;
    } else if (status.online) {
      el.className = "status online";
      el.textContent = `synced ${timeAgo(status.last_seen)}`;
    } else {
      el.className = "status offline";
      el.textContent = `offline — ${status.state || "unknown"} ${timeAgo(status.last_seen)}`;
    }

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
            const t = new Date(s.timestamp).toLocaleTimeString();
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
          const dt = new Date(d.date);
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
    const key = cursor2.toISOString().slice(0, 10);
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
function timeAgo(iso) {
  if (!iso) return "never";
  const diff = (Date.now() - new Date(iso + "Z").getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// --- Init ---
loadToday();
startPolling();

// --- Interactive Actions ---
window.updateSliderText = function(val) {
  const el = document.getElementById("slider-val");
  if (el) el.innerText = val;
};

window.logSip = async function(ml) {
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
      if (document.getElementById("page-history")?.classList.contains("active")) {
        loadHistory();
      }
    }
  } catch (e) {
    console.error("Log sip error", e);
  }
};

window.logCustomSip = async function() {
  const val = parseInt(document.getElementById("custom-slider").value);
  await window.logSip(val);
};

window.deleteSip = async function(id, timestamp) {
  if (!confirm("Are you sure you want to delete this sip entry? It will update Home Assistant and subtract the amount from your physical bottle display.")) return;
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
      if (document.getElementById("page-history")?.classList.contains("active")) {
        loadHistory();
      }
    }
  } catch (e) {
    console.error("Delete sip error", e);
  }
};

window.flashLED = async function() {
  await fetch("./commands/flash", { method: "POST" });
  alert("Flash command queued!");
};

window.setLED = async function() {
  const mode = document.getElementById("led-select").value;
  await fetch("./commands/led", { method: "POST", body: JSON.stringify({ mode: mode, color: "blue" }) });
};

window.setGoal = async function(ml) {
  try {
    currentGoal = Number(ml);
    const displayEl = document.getElementById("display-goal");
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
      if (document.getElementById("page-history")?.classList.contains("active")) {
        loadHistory();
      }
    }
  } catch (e) {
    console.error("Set goal error", e);
  }
};

window.promptCustomGoal = async function() {
  const curr = document.getElementById("display-goal")?.innerText || "1800";
  const val = prompt("Enter daily hydration goal in mL:", curr);
  if (val && !isNaN(val) && parseInt(val) > 0) {
    await window.setGoal(parseInt(val));
  }
};

// --- Active Day Schedule & Reminders ---
let scheduleLoaded = false;

window.updateToggleText = function(checked) {
  const lbl = document.getElementById("sched-toggle-label");
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

    const wakeInput = document.getElementById("sched-wake-input");
    const sleepInput = document.getElementById("sched-sleep-input");
    const intervalSelect = document.getElementById("sched-interval-select");
    const toggle = document.getElementById("sched-reminder-toggle");

    if (wakeInput && data.wake_time) wakeInput.value = data.wake_time;
    if (sleepInput && data.sleep_time) sleepInput.value = data.sleep_time;
    if (intervalSelect && data.interval_min) intervalSelect.value = String(data.interval_min);
    if (toggle) {
      toggle.checked = Boolean(data.reminder_on);
      window.updateToggleText(toggle.checked);
    }
  } catch (e) {
    console.error("Failed to load schedule:", e);
  }
}

function showScheduleFeedback(text, isError = false) {
  const fb = document.getElementById("sched-feedback");
  if (!fb) return;
  fb.textContent = text;
  fb.style.color = isError ? "var(--red)" : "#4ade80";
  fb.style.display = "inline";
  setTimeout(() => {
    fb.style.display = "none";
  }, 4000);
}

window.saveSchedule = async function() {
  const wake = document.getElementById("sched-wake-input")?.value || "08:00";
  const sleep = document.getElementById("sched-sleep-input")?.value || "20:00";
  const interval = parseInt(document.getElementById("sched-interval-select")?.value || "60");
  const on = Boolean(document.getElementById("sched-reminder-toggle")?.checked);

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

window.syncClock = async function() {
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
