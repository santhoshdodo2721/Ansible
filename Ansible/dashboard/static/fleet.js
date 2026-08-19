(function () {
  "use strict";

  const grid = document.getElementById("fleet-grid");
  const loading = document.getElementById("fleet-loading");
  const statTotal = document.getElementById("stat-total");
  const statOnline = document.getElementById("stat-online");
  const statOffline = document.getElementById("stat-offline");

  let allHosts = [];
  let pingResults = {};

  // SVG for a simple monitor/PC graphic
  function pcSvg(os, online) {
    const bodyColor = online ? (os === "windows" ? "#2563a0" : "#b8860b") : "#a9a29a";
    const screenColor = online ? (os === "windows" ? "#dae6f2" : "#f5e6c8") : "#e8e4dd";
    return `
      <svg viewBox="0 0 80 64" fill="none" class="pc-icon">
        <!-- monitor body -->
        <rect x="8" y="4" width="64" height="42" rx="3" fill="${bodyColor}" opacity="0.9"/>
        <!-- screen -->
        <rect x="12" y="8" width="56" height="34" rx="1.5" fill="${screenColor}"/>
        <!-- screen content lines -->
        ${online ? `
          <rect x="18" y="16" width="28" height="2" rx="1" fill="${bodyColor}" opacity="0.35"/>
          <rect x="18" y="22" width="20" height="2" rx="1" fill="${bodyColor}" opacity="0.25"/>
          <rect x="18" y="28" width="32" height="2" rx="1" fill="${bodyColor}" opacity="0.2"/>
          <circle cx="56" cy="16" r="4" fill="${bodyColor}" opacity="0.15"/>
        ` : `
          <line x1="32" y1="19" x2="48" y2="31" stroke="#a9a29a" stroke-width="1.5" opacity="0.5"/>
          <line x1="48" y1="19" x2="32" y2="31" stroke="#a9a29a" stroke-width="1.5" opacity="0.5"/>
        `}
        <!-- stand -->
        <rect x="32" y="46" width="16" height="4" rx="1" fill="${bodyColor}" opacity="0.6"/>
        <rect x="26" y="50" width="28" height="3" rx="1.5" fill="${bodyColor}" opacity="0.4"/>
        <!-- power LED -->
        <circle cx="40" cy="44" r="1.5" fill="${online ? '#28c840' : '#666'}"/>
      </svg>`;
  }

  function createCard(host, status) {
    const online = status === true;
    const unknown = status === undefined || status === null;
    const div = document.createElement("div");
    div.className = "machine-card" + (online ? " online" : unknown ? " checking" : " offline");
    div.innerHTML = `
      ${pcSvg(host.os, online)}
      <div class="machine-info">
        <span class="machine-name">${host.name}</span>
        <span class="machine-ip">${host.ip}</span>
      </div>
      <div class="machine-meta">
        <span class="machine-os">${host.os}</span>
        <span class="machine-status">
          <span class="status-indicator"></span>
          ${unknown ? "checking…" : online ? "online" : "offline"}
        </span>
      </div>
    `;
    return div;
  }

  function renderGrid() {
    grid.innerHTML = "";

    if (allHosts.length === 0) {
      grid.innerHTML = '<div class="fleet-empty-state">No machines in inventory.<br><a href="/">Go to Console</a> to add one.</div>';
      return;
    }

    // Group hosts
    const groups = {};
    allHosts.forEach(h => {
      const key = h._group || "other";
      if (!groups[key]) groups[key] = [];
      groups[key].push(h);
    });

    Object.entries(groups).forEach(([groupName, hosts]) => {
      const section = document.createElement("div");
      section.className = "machine-group";

      const header = document.createElement("div");
      header.className = "group-header";
      header.innerHTML = `<span class="group-name">${groupName}</span><span class="group-count">${hosts.length}</span>`;
      section.appendChild(header);

      const cardGrid = document.createElement("div");
      cardGrid.className = "card-grid";

      hosts.forEach((h, i) => {
        const card = createCard(h, pingResults[h.name]);
        card.style.animationDelay = (i * 50) + "ms";
        cardGrid.appendChild(card);
      });

      section.appendChild(cardGrid);
      grid.appendChild(section);
    });

    updateStats();
  }

  function updateStats() {
    const total = allHosts.length;
    const online = allHosts.filter(h => pingResults[h.name] === true).length;
    const offline = total - online;
    statTotal.textContent = total;
    statOnline.textContent = online;
    statOffline.textContent = offline;
  }

  async function loadHosts() {
    try {
      const res = await fetch("/api/hosts_detailed");
      const groups = await res.json();
      allHosts = [];
      Object.entries(groups).forEach(([groupName, hosts]) => {
        hosts.forEach(h => {
          h._group = groupName;
          allHosts.push(h);
        });
      });
      loading.style.display = "none";
      renderGrid();
    } catch (e) {
      loading.textContent = "Failed to load inventory.";
    }
  }

  async function pingAll() {
    // Set all to checking state
    pingResults = {};
    renderGrid();

    const btn = document.getElementById("btn-refresh");
    btn.classList.add("spinning");

    try {
      const res = await fetch("/api/ping_all");
      const data = await res.json();
      pingResults = data.results || {};
    } catch (e) {
      // leave as unknown
    }

    btn.classList.remove("spinning");
    renderGrid();
  }

  document.getElementById("btn-refresh").addEventListener("click", pingAll);

  // Init
  loadHosts().then(() => pingAll());
})();
