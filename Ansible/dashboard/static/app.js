(function () {
  "use strict";

  const linuxList = document.getElementById("linux-list");
  const windowsList = document.getElementById("windows-list");
  const linuxCount = document.getElementById("linux-count");
  const windowsCount = document.getElementById("windows-count");
  const fleetEmpty = document.getElementById("fleet-empty");
  const selectionCount = document.getElementById("selection-count");
  const termBody = document.getElementById("terminal-body");
  const toastStack = document.getElementById("toast-stack");
  const pipelineEl = document.getElementById("pipeline");
  const pipelineRetry = document.getElementById("pipeline-retry");

  let groups = {};



  // ============ TOASTS ============

  function toast(message, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = message;
    toastStack.appendChild(el);
    setTimeout(() => {
      el.classList.add("leaving");
      setTimeout(() => el.remove(), 250);
    }, 3200);
  }

  // ============ TERMINAL LOGGING ============

  function logLine(text, cls, delay) {
    const el = document.createElement("div");
    el.className = "term-line " + (cls || "term-output");
    el.textContent = text;
    if (delay) el.style.animationDelay = delay + "ms";
    termBody.appendChild(el);
    termBody.scrollTop = termBody.scrollHeight;
  }

  function logBlock(text, cls) {
    text.split("\n").forEach((line, idx) => {
      if (line.trim() !== "") logLine(line, cls, idx * 25);
    });
  }

  document.getElementById("clear-terminal").addEventListener("click", () => {
    termBody.innerHTML = "";
    logLine("Console cleared.", "term-system");
  });

  // ============ FLEET / SELECTION ============

  function selectedHosts() {
    return Array.from(document.querySelectorAll(".fleet-item input[type=checkbox]:checked")).map(
      (cb) => cb.value
    );
  }

  function bump(el) {
    el.classList.remove("bump");
    // force reflow so the animation can restart
    void el.offsetWidth;
    el.classList.add("bump");
  }

  function updateSelectionCount() {
    selectionCount.textContent = selectedHosts().length;
    bump(selectionCount);
  }

  function renderFleet() {
    linuxList.innerHTML = "";
    windowsList.innerHTML = "";

    const linuxHosts = groups["linux_lab"] || [];
    const windowsHosts = groups["windows_lab"] || [];

    linuxHosts.forEach((h, idx) => linuxList.appendChild(fleetItem(h, idx)));
    windowsHosts.forEach((h, idx) => windowsList.appendChild(fleetItem(h, idx)));

    linuxCount.textContent = linuxHosts.length;
    windowsCount.textContent = windowsHosts.length;
    bump(linuxCount);
    bump(windowsCount);

    const total = Object.values(groups).reduce((sum, arr) => sum + arr.length, 0);
    fleetEmpty.hidden = total > 0;

    updateSelectionCount();
  }

  function fleetItem(hostname, idx) {
    const li = document.createElement("li");
    li.className = "fleet-item";
    li.style.animationDelay = idx * 40 + "ms";
    const id = "host-" + hostname.replace(/[^\w-]/g, "_");
    li.innerHTML =
      '<input type="checkbox" id="' + id + '" value="' + hostname + '">' +
      '<span class="status-dot"></span>' +
      '<label class="host-name" for="' + id + '">' + hostname + "</label>";
    li.querySelector("input").addEventListener("change", updateSelectionCount);
    return li;
  }

  async function loadHosts(silent) {
    try {
      const res = await fetch("/api/hosts");
      groups = await res.json();
      renderFleet();
    } catch (e) {
      if (!silent) toast("Could not load inventory: " + e, "err");
    }
  }

  document.getElementById("select-all").addEventListener("click", () => {
    document.querySelectorAll(".fleet-item input[type=checkbox]").forEach((cb) => (cb.checked = true));
    updateSelectionCount();
  });
  document.getElementById("select-none").addEventListener("click", () => {
    document.querySelectorAll(".fleet-item input[type=checkbox]").forEach((cb) => (cb.checked = false));
    updateSelectionCount();
  });

  // ============ ADD SYSTEM MODAL ============

  const addToggle = document.getElementById("add-toggle");
  const addDrawer = document.getElementById("add-form");
  const addFormInner = document.getElementById("add-form-inner") || addDrawer;
  const addFormMsg = document.getElementById("add-form-msg");
  const osToggle = document.getElementById("os-toggle");
  let selectedOs = "linux";

  function openAddModal() {
    addDrawer.hidden = false;
    setTimeout(() => document.getElementById("host-name").focus(), 50);
  }
  function closeAddModal() {
    addDrawer.hidden = true;
    addFormMsg.textContent = "";
  }

  addToggle.addEventListener("click", openAddModal);
  document.getElementById("add-cancel").addEventListener("click", closeAddModal);

  // Second cancel button inside the modal body
  const cancelBtn2 = document.getElementById("add-cancel-btn");
  if (cancelBtn2) cancelBtn2.addEventListener("click", closeAddModal);

  // Close on backdrop click
  addDrawer.addEventListener("click", (e) => {
    if (e.target === addDrawer) closeAddModal();
  });

  // Close on Escape
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !addDrawer.hidden) closeAddModal();
  });

  osToggle.addEventListener("click", (e) => {
    const btn = e.target.closest(".os-btn, .os-card");
    if (!btn) return;
    selectedOs = btn.dataset.os;
    osToggle.querySelectorAll(".os-btn, .os-card").forEach((b) => b.classList.toggle("active", b === btn));
    const groupField = document.getElementById("host-group");
    if (!groupField.dataset.userEdited) {
      groupField.placeholder = selectedOs === "windows" ? "windows_lab" : "linux_lab";
    }
  });

  document.getElementById("host-group").addEventListener("input", (e) => {
    e.target.dataset.userEdited = "1";
  });

  addFormInner.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("host-name").value.trim();
    const ip = document.getElementById("host-ip").value.trim();
    const ansible_user = document.getElementById("host-user").value.trim();
    const group = document.getElementById("host-group").value.trim();

    if (!name || !ip) {
      addFormInner.classList.remove("shake");
      void addFormInner.offsetWidth;
      addFormInner.classList.add("shake");
      addFormMsg.textContent = "Name and IP address are required.";
      addFormMsg.className = "add-form-msg err";
      return;
    }

    addFormMsg.textContent = "Adding…";
    addFormMsg.className = "add-form-msg";

    try {
      const res = await fetch("/api/add_host", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, ip, os_type: selectedOs, ansible_user, group }),
      });
      const data = await res.json();
      addFormMsg.textContent = data.message;
      addFormMsg.className = "add-form-msg " + (data.success ? "ok" : "err");

      if (data.success) {
        groups = data.groups;
        renderFleet();
        logLine("Added " + name + " to inventory.", "term-ok");
        toast(data.message, "ok");
        addFormInner.reset();
        document.getElementById("host-group").dataset.userEdited = "";
        setTimeout(closeAddModal, 700);
      } else {
        addFormInner.classList.remove("shake");
        void addFormInner.offsetWidth;
        addFormInner.classList.add("shake");
        toast(data.message, "err");
      }
    } catch (err) {
      addFormMsg.textContent = "Request failed: " + err;
      addFormMsg.className = "add-form-msg err";
      toast("Request failed: " + err, "err");
    }
  });

  // ============ REMOVE MACHINES ============
  const removeToggle = document.getElementById("remove-toggle");
  if (removeToggle) {
    removeToggle.addEventListener("click", async () => {
      const hosts = selectedHosts();
      if (hosts.length === 0) {
        toast("Select at least one machine to remove.", "warn");
        return;
      }
      if (!confirm(`Are you sure you want to remove ${hosts.length} machine(s)?\n\n${hosts.join(", ")}`)) {
        return;
      }
      
      try {
        const res = await fetch("/api/remove_hosts", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ hosts }),
        });
        const data = await res.json();
        if (data.success) {
          groups = data.groups;
          renderFleet();
          toast(data.message, "ok");
          logLine(data.message, "term-ok");
        } else {
          toast(data.message, "err");
        }
      } catch (err) {
        toast("Request failed: " + err, "err");
      }
    });
  }

  // ============ QUICK ACTIONS ============

  document.querySelectorAll(".btn.action").forEach((btn) => {
    btn.addEventListener("mousemove", (e) => {
      const rect = btn.getBoundingClientRect();
      btn.style.setProperty("--gx", ((e.clientX - rect.left) / rect.width) * 100 + "%");
      btn.style.setProperty("--gy", ((e.clientY - rect.top) / rect.height) * 100 + "%");
    });
    btn.addEventListener("click", () => runAction(btn.dataset.action, btn));
  });

  async function runAction(action, btn) {
    const hosts = selectedHosts();
    if (hosts.length === 0) {
      toast("Select at least one machine first.", "warn");
      return;
    }
    const packageName = document.getElementById("package-name").value.trim();
    if (action === "install" && !packageName) {
      toast("Enter a package name before running Install.", "warn");
      return;
    }

    logLine(action + " -> " + hosts.join(", "), "term-cmd");
    try {
      const res = await fetch("/api/action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, hosts, package: packageName }),
      });
      const data = await res.json();
      logBlock(data.output || "(no output)", data.success ? "term-output" : "term-fail");
      logLine(data.success ? "done." : "finished with errors.", data.success ? "term-ok" : "term-fail");

      if (btn) {
        btn.classList.remove("pulse-success", "pulse-fail");
        void btn.offsetWidth;
        btn.classList.add(data.success ? "pulse-success" : "pulse-fail");
      }
      toast(action + (data.success ? " completed" : " failed") + " on " + hosts.length + " machine(s)", data.success ? "ok" : "err");
    } catch (err) {
      logLine("Request failed: " + err, "term-fail");
      toast("Request failed: " + err, "err");
    }
  }

  // ============ PIPELINE TRACKER ============

  const STAGE_ORDER = ["facts", "generate", "lint", "dryrun", "apply"];

  function resetPipeline() {
    pipelineEl.hidden = false;
    pipelineRetry.hidden = true;
    pipelineEl.querySelectorAll(".pipeline-step").forEach((el) => el.removeAttribute("data-state"));
    pipelineEl.querySelectorAll(".pipeline-wire").forEach((el) => el.classList.remove("filled"));
  }

  function setStage(stage, state) {
    const el = pipelineEl.querySelector('.pipeline-step[data-stage="' + stage + '"]');
    if (!el) return;
    if (state) {
      el.setAttribute("data-state", state);
    } else {
      el.removeAttribute("data-state");
    }
    if (state === "done") fillWireUpTo(stage);
  }

  function fillWireUpTo(stage) {
    const idx = STAGE_ORDER.indexOf(stage);
    const wires = pipelineEl.querySelectorAll(".pipeline-wire");
    if (idx >= 0 && wires[idx]) wires[idx].classList.add("filled");
  }

  function applySmartLog(log) {
    let attempts = 0;
    log.forEach((step) => {
      switch (step.step) {
        case "facts":
          setStage("facts", "done");
          setStage("generate", "active");
          break;
        case "generate":
          attempts += 1;
          if (attempts > 1) {
            pipelineRetry.hidden = false;
            pipelineRetry.textContent = "retry " + (attempts - 1);
          }
          setStage("generate", "active");
          break;
        case "lint_fail":
          setStage("lint", "fail");
          setTimeout(() => setStage("lint", undefined), 0);
          break;
        case "dry_run":
          setStage("generate", "done");
          setStage("lint", "done");
          setStage("dryrun", "active");
          break;
        case "dry_run_pass":
          setStage("dryrun", "done");
          setStage("apply", "active");
          break;
        case "partial":
          setStage("dryrun", "done");
          setStage("apply", "active");
          break;
        case "attempt_fail":
          setStage("dryrun", "fail");
          break;
        case "applied":
          setStage("apply", "done");
          break;
        case "applied_fail":
          setStage("apply", "fail");
          break;
        case "verify":
          break;
        case "final_fail":
          STAGE_ORDER.forEach((s) => {
            const el = pipelineEl.querySelector('.pipeline-step[data-stage="' + s + '"]');
            if (el && !el.getAttribute("data-state")) setStage(s, "fail");
          });
          break;
      }
    });
  }

  // ============ SMART TASK ============

  document.getElementById("smart-run").addEventListener("click", async () => {
    const prompt = document.getElementById("smart-prompt").value.trim();
    if (!prompt) {
      toast("Describe a task before running Smart task.", "warn");
      return;
    }
    const hosts = selectedHosts();
    if (!hosts.length) {
      toast("Select at least one machine first.", "warn");
      return;
    }
    const autoApply = document.getElementById("auto-apply").checked;

    resetPipeline();
    setStage("facts", "active");
    logLine("smart task -> " + prompt, "term-cmd");
    logLine("targets -> " + hosts.join(", "), "term-system");
    logLine("generating and validating a playbook, this can take a moment…", "term-system");

    try {
      const res = await fetch("/api/smart_action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, hosts, auto_apply: autoApply }),
      });
      const data = await res.json();

      applySmartLog(data.log || []);

      (data.log || []).forEach((step) => {
        const cls =
          step.step.includes("fail") ? "term-fail" :
          step.step.includes("pass") || step.step === "applied" ? "term-ok" :
          "term-system";
        logBlock("[" + step.step + "] " + step.text, cls);
      });

      if (!data.success) {
        STAGE_ORDER.forEach((s) => {
          const el = pipelineEl.querySelector('.pipeline-step[data-stage="' + s + '"]');
          if (el && !el.getAttribute("data-state")) setStage(s, "fail");
        });
      }

      logLine(data.success ? "smart task finished." : "smart task did not fully succeed.", data.success ? "term-ok" : "term-fail");
      toast(data.success ? "Smart task completed" : "Smart task needs a look", data.success ? "ok" : "err");
    } catch (err) {
      logLine("Request failed: " + err, "term-fail");
      toast("Request failed: " + err, "err");
    }
  });

  // ============ RAW TERMINAL COMMAND ============

  document.getElementById("terminal-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("terminal-input");
    const command = input.value.trim();
    if (!command) return;

    const hosts = selectedHosts();
    if (hosts.length === 0) {
      toast("Select at least one machine before running a command.", "warn");
      return;
    }

    logLine(command + "  (on " + hosts.join(", ") + ")", "term-cmd");
    input.value = "";
    input.disabled = true;

    try {
      const res = await fetch("/api/terminal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command, hosts }),
      });
      const data = await res.json();
      logBlock(data.output || "(no output)", data.success ? "term-output" : "term-fail");
    } catch (err) {
      logLine("Request failed: " + err, "term-fail");
      toast("Request failed: " + err, "err");
    } finally {
      input.disabled = false;
      input.focus();
    }
  });

  // ============ INIT ============

  const fastfetchLogo = `
       .-------.    
      /       /|    Lab Ops Console v2.0
     /_______/ |    --------------------
     | o o o | |    OS: Web Environment
     |       | |    Shell: Dashboard UI
     | o o o | /    Uptime: Just booted
     '-------'      Theme: Flat Dark
  `;

  loadHosts();
  setTimeout(() => {
    logBlock(fastfetchLogo, "term-output");
    logLine("Ready. Select machines on the left, then run an action, a smart task, or type a raw command below.", "term-system");
  }, 200);
})();
