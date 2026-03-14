/* HA-Cluster Dashboard – async data loading & auto-refresh */

const REFRESH_INTERVAL = 30000;
let refreshTimer = null;
let activeLogTab = "haproxy_gateway";

// -- Boot --
document.addEventListener("DOMContentLoaded", () => {
  loadAll();
  startAutoRefresh();
});

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(loadAll, REFRESH_INTERVAL);
}

function loadAll() {
  loadCluster();
  loadHAProxyStats();
  loadContainers();
  loadLogs(activeLogTab);
}

// -- Cluster overview --

async function loadCluster() {
  const el = document.getElementById("cluster-body");
  if (!el) return;
  try {
    const resp = await fetch("/api/cluster");
    const data = await resp.json();

    // Update header info
    const headerNode = document.getElementById("header-node");
    const headerPrio = document.getElementById("header-prio");
    if (headerNode) headerNode.textContent = data.node;
    if (headerPrio) {
      headerPrio.textContent = data.prio === 1 ? "Master" : "Follower";
      headerPrio.className = "badge " + (data.prio === 1 ? "badge-master" : "badge-follower");
    }

    if (!data.nodes || data.nodes.length === 0) {
      el.innerHTML = '<tr><td colspan="8" class="meta">Keine MESH_NODES konfiguriert.</td></tr>';
      return;
    }

    el.innerHTML = data.nodes.map(n => {
      const geo = n.geo || {};
      const cert = n.cert || {};
      const reachable = n.geo || n.cert;
      const errors = (n.errors || []).join("; ");

      const reachIcon = reachable
        ? '<span class="indicator indicator-green"></span>'
        : '<span class="indicator indicator-red"></span>';

      const certRole = cert.cert_is_master ? "Master" : "Follower";
      const certRoleBadge = cert.cert_is_master
        ? '<span class="badge badge-master">Master</span>'
        : '<span class="badge badge-follower">Follower</span>';

      return `<tr>
        <td>${n.ip}</td>
        <td>${geo.node_name || "—"}</td>
        <td>${geo.node_prio || "—"}</td>
        <td>${formatTimestamp(geo.validated_at)}</td>
        <td><code>${truncate(cert.version, 12) || "—"}</code></td>
        <td>${formatTimestamp(cert.validated_since)}</td>
        <td>${cert.version ? certRoleBadge : "—"}</td>
        <td>${reachIcon}</td>
        <td class="meta">${errors || ""}</td>
      </tr>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<tr><td colspan="8" class="status-down">Fehler: ${e.message}</td></tr>`;
  }
}

// -- HAProxy stats --

async function loadHAProxyStats() {
  const el = document.getElementById("haproxy-stats");
  if (!el) return;
  try {
    const resp = await fetch("/api/haproxy/stats");
    const data = await resp.json();

    if (data.error) {
      el.innerHTML = `<p class="status-down">HAProxy nicht erreichbar: ${data.error}</p>`;
      return;
    }

    // Stat boxes
    const statsHtml = `
      <div class="grid-4">
        <div class="stat-box card">
          <div class="value">${fmtNum(data.active_connections)}</div>
          <div class="label">Aktive Verbindungen</div>
        </div>
        <div class="stat-box card">
          <div class="value">${fmtNum(data.total_connections)}</div>
          <div class="label">Verbindungen gesamt</div>
        </div>
        <div class="stat-box card">
          <div class="value">${fmtNum(data.request_rate)}</div>
          <div class="label">Requests gesamt</div>
        </div>
        <div class="stat-box card">
          <div class="value">${data.backends.length}</div>
          <div class="label">Backends</div>
        </div>
      </div>
    `;

    // Backend table
    let backendRows = "";
    if (data.backends.length > 0) {
      backendRows = data.backends.map(b => {
        const upIcon = b.up
          ? '<span class="indicator indicator-green"></span>UP'
          : '<span class="indicator indicator-red"></span>DOWN';
        const sessions = b.current_sessions != null ? b.current_sessions : "—";
        const resp2xx = (b.responses && b.responses["2xx"]) || 0;
        const resp4xx = (b.responses && b.responses["4xx"]) || 0;
        const resp5xx = (b.responses && b.responses["5xx"]) || 0;
        return `<tr>
          <td>${b.name}</td>
          <td>${upIcon}</td>
          <td>${sessions}</td>
          <td class="status-up">${fmtNum(resp2xx)}</td>
          <td class="status-warn">${fmtNum(resp4xx)}</td>
          <td class="status-down">${fmtNum(resp5xx)}</td>
        </tr>`;
      }).join("");
    }

    const backendTable = data.backends.length > 0 ? `
      <table>
        <thead><tr>
          <th>Backend</th><th>Status</th><th>Sessions</th>
          <th>2xx</th><th>4xx</th><th>5xx</th>
        </tr></thead>
        <tbody>${backendRows}</tbody>
      </table>
    ` : '<p class="meta">Keine Backend-Daten verfuegbar.</p>';

    el.innerHTML = statsHtml + backendTable;
  } catch (e) {
    el.innerHTML = `<p class="status-down">Fehler: ${e.message}</p>`;
  }
}

// -- Docker containers --

async function loadContainers() {
  const el = document.getElementById("containers-list");
  if (!el) return;
  try {
    const resp = await fetch("/api/containers");
    const containers = await resp.json();

    if (containers.length === 0) {
      el.innerHTML = '<p class="meta">Keine Container gefunden (Docker-Socket erreichbar?).</p>';
      return;
    }

    el.innerHTML = containers.map(c => {
      let stateClass = "indicator-gray";
      if (c.state === "running") stateClass = "indicator-green";
      else if (c.state === "exited" || c.state === "dead") stateClass = "indicator-red";
      else if (c.state === "restarting") stateClass = "indicator-yellow";

      return `<div class="container-card">
        <span class="indicator ${stateClass}"></span>
        <div>
          <div class="name">${c.name}</div>
          <div class="detail">${c.status} &middot; ${c.image}</div>
        </div>
      </div>`;
    }).join("");
  } catch (e) {
    el.innerHTML = `<p class="status-down">Fehler: ${e.message}</p>`;
  }
}

// -- Log viewer --

function selectLogTab(container) {
  activeLogTab = container;
  document.querySelectorAll(".log-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.container === container);
  });
  loadLogs(container);
}

async function loadLogs(container) {
  const el = document.getElementById("log-output");
  if (!el) return;
  el.textContent = "Lade Logs...";
  try {
    const resp = await fetch(`/api/logs/${container}?lines=200`);
    const data = await resp.json();
    el.textContent = data.logs || "(keine Logs)";
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    el.textContent = `Fehler: ${e.message}`;
  }
}

// -- Deploy actions --

async function deployGeo() {
  if (!confirm("Geo-Listen jetzt deployen?")) return;
  const btn = document.getElementById("btn-deploy-geo");
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch("/api/geo/deploy", { method: "POST" });
    const text = await resp.text();
    alert(resp.ok ? "Geo-Deploy gestartet." : `Fehler: ${text}`);
  } catch (e) {
    alert(`Fehler: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function deployCert() {
  if (!confirm("Zertifikat jetzt deployen?")) return;
  const btn = document.getElementById("btn-deploy-cert");
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch("/api/cert/deploy", { method: "POST" });
    const text = await resp.text();
    alert(resp.ok ? "Cert-Deploy gestartet." : `Fehler: ${text}`);
  } catch (e) {
    alert(`Fehler: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// -- Helpers --

function formatTimestamp(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    return d.toLocaleString("de-DE", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    });
  } catch { return ts; }
}

function truncate(s, len) {
  if (!s) return "";
  return s.length > len ? s.substring(0, len) + "..." : s;
}

function fmtNum(n) {
  if (n == null || n === undefined) return "—";
  return Number(n).toLocaleString("de-DE");
}

function manualRefresh() {
  loadAll();
  startAutoRefresh();
}
