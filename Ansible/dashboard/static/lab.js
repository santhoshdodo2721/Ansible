(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let hosts = [], statuses = {}, selected = new Set(), filter = 'all', busy = false;
  const checking = new Set();
  let initialSelection = new URLSearchParams(location.search).get('hosts');
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const labels = {ready:'Ready',needs_setup:'Needs setup',unreachable:'Unreachable',checking:'Checking',unknown:'Not checked'};
  async function api(path, data, timeout = 120000) {
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(path, {method:data ? 'POST':'GET',headers:data ? {'Content-Type':'application/json'}:{},body:data ? JSON.stringify(data):undefined,signal:controller.signal,cache:'no-store'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.message || `Request failed (${response.status})`);
      return result;
    } catch (error) { if (error.name === 'AbortError') throw new Error('Request timed out. Check the connection and retry.'); throw error; }
    finally { clearTimeout(timer); }
  }
  function notice(text) { $('notice').hidden = !text; $('notice').textContent = text; }
  function visible() {
    const query = $('search').value.trim().toLowerCase();
    return hosts.filter(h => (filter === 'all' || h.os === filter) && [h.name,h.ip,h.group,h.user].join(' ').toLowerCase().includes(query));
  }
  function state(host) { return checking.has(host.name) ? 'checking' : statuses[host.name]?.state || 'unknown'; }
  function render() {
    $('total').textContent = hosts.length;
    $('ready').textContent = hosts.filter(h => statuses[h.name]?.ready).length;
    $('setup').textContent = hosts.filter(h => statuses[h.name]?.state === 'needs_setup').length;
    $('unreachable').textContent = hosts.filter(h => statuses[h.name]?.state === 'unreachable').length;
    $('selection').textContent = `${selected.size} selected`;
    $('target-count').textContent = `${selected.size} target${selected.size === 1 ? '' : 's'}`;
    const rows = visible();
    $('systems').innerHTML = rows.length ? rows.map(h => `<tr class="${selected.has(h.name) ? 'selected':''}"><td><input type="checkbox" data-host="${escape(h.name)}" aria-label="Select ${escape(h.name)}" ${selected.has(h.name)?'checked':''}></td><td><span class="host-name">${escape(h.name)}</span><span class="host-meta">${h.os === 'windows' ? 'Windows':'Linux'} · ${escape(h.group)}</span></td><td><span class="address">${escape(h.ip)}</span><div class="connection-meta">${h.os === 'windows'?'WinRM':'SSH'} :${h.port} · ${escape(h.user || 'No user configured')}</div></td><td><span class="badge ${state(h)}" title="${escape(statuses[h.name]?.message || '')}"><i class="dot ${state(h)==='ready'?'green':state(h)==='needs_setup'?'amber':'gray'}"></i>${labels[state(h)]}</span></td><td><div class="row-tools"><button data-test="${escape(h.name)}" ${checking.has(h.name)?'disabled':''}>Test</button><button data-edit="${escape(h.name)}">Configure</button><button data-detail="${escape(h.name)}">Details</button></div></td></tr>`).join('') : '<tr><td class="empty" colspan="5">No systems found. Add a system to connect your first lab machine.</td></tr>';
    if (document.body.classList.contains('fleet-view')) {
      $('ready').textContent = hosts.filter(h => statuses[h.name]?.reachable === true).length;
      $('unreachable').textContent = hosts.filter(h => statuses[h.name]?.reachable === false).length;
      $('fleet-cards').innerHTML = rows.length ? rows.map(h => {
        const connected = statuses[h.name]?.reachable;
        const mode = checking.has(h.name) ? 'checking' : connected === true ? 'online' : connected === false ? 'offline' : 'unknown';
        return `<article class="fleet-card pc-tile ${mode} ${selected.has(h.name)?'selected':''}"><header><span class="platform-tag">${h.os === 'windows' ? 'WINDOWS' : 'LINUX'}</span><input type="checkbox" data-host="${escape(h.name)}" aria-label="Select ${escape(h.name)}" ${selected.has(h.name)?'checked':''}></header><svg class="animated-pc" viewBox="0 0 180 150" role="img" aria-label="${escape(h.name)} ${mode}"><ellipse class="pc-shadow" cx="90" cy="138" rx="65" ry="6"/><g class="pc-monitor"><rect class="pc-frame" x="23" y="12" width="134" height="92" rx="7"/><rect class="pc-screen" x="31" y="20" width="118" height="73" rx="3"/><g class="pc-content"><path d="M47 40l9 7-9 7"/><path class="pc-cursor" d="M64 55h17"/><path class="pc-lines" d="M47 67h65M47 77h43"/></g><g class="pc-sleep"><path d="M74 41h18L74 61h18M104 35h12l-12 13h12"/></g><circle class="pc-led" cx="139" cy="99" r="2"/></g><path class="pc-stand" d="M79 104h22l5 18H74z"/><rect class="pc-stand" x="62" y="121" width="56" height="6" rx="3"/><rect class="pc-keyboard" x="39" y="132" width="102" height="7" rx="3"/></svg><h2>${escape(h.name)}</h2><span class="host-meta">${escape(h.ip)}</span><span class="pc-status"><i class="dot"></i>${mode === 'online' ? 'Online' : mode === 'offline' ? 'Offline' : mode === 'checking' ? 'Checking…' : 'Unknown'}</span><div class="row-tools"><button data-test="${escape(h.name)}" ${checking.has(h.name)?'disabled':''}>Refresh</button><button data-edit="${escape(h.name)}">Configure</button><button data-detail="${escape(h.name)}">Details</button></div></article>`;
      }).join('') : '<p class="empty">No matching systems.</p>';
      $('manage-selected').href = selected.size ? '/?hosts=' + encodeURIComponent([...selected].join(',')) : '/';
      $('manage-selected').textContent = selected.size ? `Manage ${selected.size} selected in console →` : 'Open console →';
    }
    $('select-all').checked = rows.length > 0 && rows.every(h => selected.has(h.name));
    $('select-all').indeterminate = rows.some(h => selected.has(h.name)) && !$('select-all').checked;
    document.querySelectorAll('[data-action], #package-form button, #command-form button').forEach(b => b.disabled = busy || !selected.size);
  }
  async function load() {
    const data = await api('/api/machines');
    hosts = data.hosts;
    if (initialSelection) { selected = new Set(initialSelection.split(',')); initialSelection = null; }
    selected = new Set([...selected].filter(name => hosts.some(h => h.name === name)));
    render();
  }
  function output(text, append = false) { $('output').textContent = append ? $('output').textContent + '\n\n' + text : text; }
  async function check(names) {
    const targets = names || hosts.map(h => h.name);
    if (!targets.length || targets.some(n => checking.has(n))) return;
    targets.forEach(n => checking.add(n));
    $('refresh').disabled = true;
    render();
    try {
      const data = await api('/api/check', {hosts:targets,connectivity_only:document.body.classList.contains('fleet-view')}, Math.max(35000, Math.ceil(targets.length / 20) * 25000));
      Object.assign(statuses, data.statuses);
      $('checked-at').textContent = `Checked ${new Date().toLocaleTimeString()}`;
      const fleetPage = document.body.classList.contains('fleet-view');
      const failures = targets.filter(n => fleetPage ? statuses[n]?.reachable === false : !statuses[n]?.ready);
      notice(fleetPage ? '' : failures.length ? `${failures.length} connection${failures.length === 1?'':'s'} need attention. Open Details for the reason, or Configure to update login settings.` : '');
      if (targets.length === 1) showDetail(targets[0]);
    } catch (error) {
      targets.forEach(n => { statuses[n] = {state:'unknown',message:error.message,detail:'The controller could not complete the check.'}; });
      notice(error.message);
    } finally { targets.forEach(n => checking.delete(n)); $('refresh').disabled = checking.size > 0; render(); }
  }
  const detailDialog = document.createElement('dialog');
  detailDialog.className = 'connection-details';
  detailDialog.innerHTML = '<div class="dialog-heading"><h2 id="details-title">Connection details</h2><button type="button" aria-label="Close connection details">×</button></div><p id="details-summary" class="help"></p><pre id="details-output" tabindex="0"></pre><div class="dialog-footer"><button type="button" id="details-configure">Configure connection</button></div>';
  document.body.appendChild(detailDialog);
  let detailHost = null;
  detailDialog.querySelector('[aria-label="Close connection details"]').addEventListener('click', () => detailDialog.close());
  $('details-configure').addEventListener('click', () => { detailDialog.close(); openConnection(detailHost); });
  function showDetail(name) {
    const h = hosts.find(h => h.name === name), s = statuses[name];
    if (!h) return;
    detailHost = name;
    const summary = s?.message || 'Connection has not been checked.';
    const detail = s?.detail || 'Use Test to verify network reachability and authentication.';
    $('details-title').textContent = `${name} · ${h.ip}:${h.port}`;
    $('details-summary').textContent = summary;
    $('details-output').textContent = detail;
    output(`${name} · ${h.ip}:${h.port}\n\n${summary}\n\n${detail}`);
    $('job-state').textContent = `Connection details · ${name}`;
    if (!detailDialog.open) detailDialog.showModal();
  }
  document.querySelector('.systems-panel').addEventListener('change', e => { const name = e.target.dataset.host; if (!name) return; e.target.checked ? selected.add(name):selected.delete(name); render(); });
  document.querySelector('.systems-panel').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; if (b.dataset.test) check([b.dataset.test]); if (b.dataset.edit) openConnection(b.dataset.edit); if (b.dataset.detail) showDetail(b.dataset.detail); });
  $('select-all').addEventListener('change', e => { visible().forEach(h => e.target.checked ? selected.add(h.name):selected.delete(h.name)); render(); });
  $('search').addEventListener('input', render);
  document.querySelectorAll('[data-filter]').forEach(b => b.addEventListener('click', () => { filter = b.dataset.filter; document.querySelectorAll('[data-filter]').forEach(t => t.classList.toggle('active',t === b)); render(); }));
  $('refresh').addEventListener('click', () => check());
  $('pc-size')?.addEventListener('input', e => $('fleet-cards').style.setProperty('--pc-tile-size', e.target.value + 'px'));
  $('fleet-cards').addEventListener('dblclick', e => { const card = e.target.closest('.pc-tile'); if (card && !e.target.closest('button,input')) showDetail(card.querySelector('[data-host]').dataset.host); });
  $('activity-link').addEventListener('click', () => $('activity').scrollIntoView({behavior:'smooth'}));
  $('clear-output').addEventListener('click', () => output('Output cleared.'));
  const form = $('connection-form');
  function platformFields(resetPort = false) {
    const windows = form.elements.os.value === 'windows';
    document.querySelectorAll('.linux-field,.linux-guide').forEach(e => e.hidden = windows);
    document.querySelectorAll('.windows-field,.windows-guide').forEach(e => e.hidden = !windows);
    form.elements.group.placeholder = windows ? 'windows_lab':'linux_lab';
    if (resetPort) { form.elements.port.value = windows ? 5986:22; form.elements.scheme.value = 'https'; }
  }
  function openConnection(name) {
    form.reset();
    $('form-message').textContent = '';
    const h = hosts.find(h => h.name === name);
    $('dialog-title').textContent = h ? `Configure ${h.name}`:'Add system';
    $('password-status').textContent = h?.has_password ? 'A login password is saved. It is hidden here. Enter a new password to replace it.' : 'No login password is saved. Enter the remote account password for password login.';
    form.elements.name.readOnly = !!h;
    $('remove-system').hidden = !h;
    if (h) {
      ['name','os','ip','port','user','group','key_file','scheme'].forEach(k => { form.elements[k].value = h[k] ?? ''; });
      form.elements.validate_cert.checked = h.validate_cert !== false;
      form.elements.password.placeholder = h.has_password ? 'Saved password retained when blank':'Enter password, or leave blank for key authentication';
    }
    platformFields();
    $('connection-dialog').showModal();
    (h ? form.elements.ip : form.elements.name).focus();
  }
  $('add-system').addEventListener('click', () => openConnection());
  ['close-dialog','cancel-dialog'].forEach(id => $(id).addEventListener('click', () => $('connection-dialog').close()));
  $('remove-system').addEventListener('click', async () => {
    const name = form.elements.name.value;
    if (!confirm(`Remove ${name} from this workspace? The remote system will not be changed.`)) return;
    $('remove-system').disabled = true;
    try {
      await api('/api/remove',{hosts:[name]});
      selected.delete(name); delete statuses[name];
      await load(); $('connection-dialog').close();
    } catch(error) { $('form-message').textContent = error.message; }
    finally { $('remove-system').disabled = false; }
  });
  form.elements.password.addEventListener('input', () => {
    if (form.elements.password.value) form.elements.clear_password.checked = false;
  });
  form.elements.clear_password.addEventListener('change', () => {
    if (form.elements.clear_password.checked) form.elements.password.value = '';
  });
  form.elements.os.addEventListener('change', () => platformFields(true));
  form.elements.scheme.addEventListener('change', () => { form.elements.port.value = form.elements.scheme.value === 'https' ? 5986:5985; });
  form.addEventListener('submit', async e => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    data.validate_cert = form.elements.validate_cert.checked;
    data.clear_password = form.elements.clear_password.checked;
    data.save_offline = e.submitter?.id === 'save-offline';
    $('save-connection').disabled = true;
    $('save-offline').disabled = true;
    $('form-message').textContent = 'Saving connection…';
    try {
      const saved = await api('/api/connections',data);
      await load();
      form.elements.password.value = '';
      form.elements.sudo_password.value = '';
      $('connection-dialog').close();
      selected.add(data.name); render();
      if (saved.verified) await check([data.name]);
      else { delete statuses[data.name]; notice(saved.message + ' Check connections after joining the system’s network.'); render(); }
    } catch(error) { $('form-message').textContent = error.message; }
    finally { $('save-connection').disabled = false; $('save-offline').disabled = false; }
  });
  async function operate(action, value = '') {
    if (busy || !selected.size) return;
    const targets = [...selected];
    if (action === 'ping') { await check(targets); return; }
    if (['restart','shutdown'].includes(action) && !confirm(`${action === 'restart'?'Restart':'Shut down'} these systems?\n\n${targets.join('\n')}`)) return;
    busy = true; render();
    $('job-state').textContent = `Running ${action} · ${targets.join(', ')}`;
    output(`[${new Date().toLocaleTimeString()}] ${action.toUpperCase()}\nTargets: ${targets.join(', ')}${value?'\nInput: '+value:''}\n\nWaiting for remote output…`);
    try {
      const data = await api('/api/operate',{hosts:targets,action,value,become:$('become').checked}, Math.max(200000,Math.ceil(targets.length/20)*190000));
      output(data.results.map(r => `── ${r.host} · ${r.success?'SUCCESS':'FAILED'} ──\n${r.output}`).join('\n\n'));
      $('job-state').textContent = `${data.success?'Completed':'Completed with errors'} · ${action} · ${new Date().toLocaleTimeString()}`;
      if (['restart','shutdown'].includes(action)) { targets.forEach(n => delete statuses[n]); render(); }
    } catch(error) { output(error.message); $('job-state').textContent = 'Operation failed'; }
    finally { busy = false; render(); }
  }
  document.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => operate(b.dataset.action)));
  $('package-form').addEventListener('submit', e => { e.preventDefault(); operate('install',$('package').value.trim()); });
  $('command-form').addEventListener('submit', e => { e.preventDefault(); operate('command',$('command').value.trim()); });
  if (document.body.classList.contains('fleet-view')) setInterval(() => { if (!document.hidden && !checking.size) check(); }, 30000);
  load().then(() => check()).catch(error => { notice(error.message); $('systems').innerHTML = '<tr><td colspan="5" class="empty">Inventory could not be loaded. Reload to retry.</td></tr>'; });
})();
