const $ = id => document.getElementById(id);

const API = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json', ...opts.headers }, ...opts });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
};

const STATE = {
  projects: [],
  presets: [],
  conversations: [],
  activeProjectId: '',
  activePresetId: '',
  activeConversationId: '',
  messages: [],
  galleryImages: [],
  sidebarPanel: 'projects',
  view: 'chat',
  loading: false,
};

function t(text) {
  const toast = $('toast');
  toast.textContent = text;
  toast.className = 'show';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { toast.className = ''; }, 2500);
}
function toastErr(text) {
  const toast = $('toast');
  toast.textContent = text;
  toast.className = 'show error';
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { toast.className = ''; }, 4000);
}

function setStatus(text, loading) {
  $('status-text').textContent = text;
  $('status-dot').className = 'dot' + (loading ? '' : ' on');
}

function showModal(title, html) {
  $('modal').innerHTML = `<h2>${title}</h2>${html}`;
  $('modal-overlay').classList.add('on');
  const first = $('modal').querySelector('input, textarea, select');
  if (first) first.focus();
}
function hideModal() {
  $('modal-overlay').classList.remove('on');
}
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if ($('lightbox').style.display === 'flex') {
    $('lightbox').style.display = 'none';
  } else if ($('modal-overlay').classList.contains('on')) {
    hideModal();
  }
});

$('modal-overlay').addEventListener('click', (e) => {
  if (e.target === $('modal-overlay')) hideModal();
});

async function loadProjects() {
  try {
    STATE.projects = await API('/api/projects');
  } catch (e) {
    STATE.projects = [];
  }
  renderSidebar();
}

async function loadPresets() {
  try {
    STATE.presets = await API('/api/presets');
  } catch (e) {
    STATE.presets = [];
  }
  renderSidebar();
}

async function loadConversations() {
  if (!STATE.activeProjectId) {
    STATE.conversations = [];
    renderConversationList();
    return;
  }
  STATE.conversations = await API(`/api/conversations/${STATE.activeProjectId}`);
  if (!STATE.activeConversationId || !STATE.conversations.some(c => c.id === STATE.activeConversationId)) {
    STATE.activeConversationId = STATE.conversations.length > 0 ? STATE.conversations[0].id : '';
    await loadMessages();
  }
  renderConversationList();
}

async function loadMessages() {
  if (!STATE.activeConversationId) {
    STATE.messages = [];
    renderMessages();
    return;
  }
  STATE.messages = await API(`/api/messages/${STATE.activeConversationId}`);
  renderMessages();
}

async function switchConversation(cid) {
  if (STATE.activeConversationId === cid) return;
  STATE.activeConversationId = cid;
  await loadMessages();
  renderConversationList();
}

async function newConversation() {
  if (!STATE.activeProjectId) return toastErr('select a project first');
  try {
    const conv = await API(`/api/projects/${STATE.activeProjectId}/conversations`, {
      method: 'POST',
      body: JSON.stringify({ title: 'New chat' }),
    });
    STATE.activeConversationId = conv.id;
    STATE.messages = [];
    renderMessages();
    await loadConversations();
    t('new chat started');
  } catch (e) {
    toastErr(e.message);
  }
}

async function deleteConversation(cid) {
  if (!confirm('Delete this conversation and all its messages?')) return;
  try {
    await API(`/api/conversations/${cid}`, { method: 'DELETE' });
    if (STATE.activeConversationId === cid) {
      STATE.activeConversationId = '';
      STATE.messages = [];
      renderMessages();
    }
    await loadConversations();
    t('conversation deleted');
  } catch (e) {
    toastErr(e.message);
  }
}

async function loadGallery() {
  if (!STATE.activeProjectId) {
    STATE.galleryImages = [];
    renderGallery();
    return;
  }
  STATE.galleryImages = await API(`/api/gallery/${STATE.activeProjectId}`);
  renderGallery();
}

// Force the next renderGallery() to rebuild (e.g. after mutations that change
// content but could reuse the same id list).
function invalidateGallery() { _gallerySig = null; }

function renderConversationList() {
  // The chat list lives inside the projects panel; re-render only when that
  // panel is visible so other panels don't clobber it.
  if (STATE.sidebarPanel !== 'projects') return;
  if (!$('conversation-list')) return;
  renderSidebar();
}

function renderSidebar() {
  const c = $('sidebar-content');
  if (STATE.sidebarPanel === 'projects') {
    c.innerHTML = `<div class="section">
  <div class="section-header"><h3>Projects</h3><button class="add-btn" id="new-project-btn">+ new</button></div>
  <div id="project-list">
    ${STATE.projects.map(p => `<div class="list-item${STATE.activeProjectId === p.id ? ' active' : ''}" data-pid="${p.id}">
      <span>${esc(p.name)}</span>
      <span style="font-size:10px;color:var(--dim);margin:0 6px">${p.asset_count || 0} a · ${p.generation_count || 0} imgs</span>
      <div class="actions">
        <button class="edit-proj" data-pid="${p.id}">edit</button>
        <button class="del-proj" data-pid="${p.id}" style="color:#ff6b6f">x</button>
      </div>
    </div>`).join('')}
    ${STATE.projects.length === 0 ? '<div style="color:var(--dim);font-size:12px;padding:8px">no projects yet</div>' : ''}
  </div>
</div>
<div class="section">
  <div class="section-header"><h3>Chats</h3>${STATE.activeProjectId ? '<button class="add-btn" id="new-conversation-btn">+ new</button>' : ''}</div>
  <div id="conversation-list" style="font-size:12px">
    ${STATE.activeProjectId
      ? (STATE.conversations.length === 0
        ? '<div style="color:var(--dim);padding:8px 0">no chats yet — send a message to start one</div>'
        : STATE.conversations.map(cv => `<div class="list-item${STATE.activeConversationId === cv.id ? ' active' : ''}" data-cid="${cv.id}">
            <span title="${esc(cv.title || 'chat')}">${esc((cv.title || 'chat').slice(0, 26))}</span>
            <div class="actions">
              <button class="del-conv" data-cid="${cv.id}" aria-label="delete conversation" style="color:#ff6b6f">x</button>
            </div>
          </div>`).join(''))
      : '<div style="color:var(--dim);padding:8px 0">select a project</div>'}
  </div>
</div>
<div class="section">
  <div class="section-header"><h3>Assets</h3></div>
  <div id="mini-assets" style="font-size:11px;color:var(--dim)">
    ${STATE.activeProjectId ? '<button class="ghost sm" id="upload-asset-btn">+ upload</button>' : 'select a project'}
  </div>
</div>
<div class="section">
  <div class="section-header"><h3>Jobs</h3></div>
  <div id="mini-jobs" style="font-size:11px;color:var(--dim)">
    ${STATE.activeProjectId ? '<button class="ghost sm" id="refresh-jobs-btn">refresh</button>' : 'select a project'}
  </div>
</div>`;
  } else {
    c.innerHTML = `<div class="section">
  <div class="section-header"><h3>Presets</h3><button class="add-btn" id="new-preset-btn">+ new</button></div>
  <div id="preset-list">
    ${STATE.presets.map(p => `<div class="list-item${STATE.activePresetId === p.id ? ' active' : ''}" data-pid="${p.id}">
      <span>${esc(p.name)}</span>
      <span style="font-size:10px;color:var(--dim);margin:0 6px">${p.image_model || '-'} / ${p.logic_model || '-'}</span>
      <div class="actions">
        <button class="activate-preset" data-pid="${p.id}">use</button>
        <button class="edit-preset" data-pid="${p.id}">edit</button>
        <button class="del-preset" data-pid="${p.id}" style="color:#ff6b6f">x</button>
      </div>
    </div>`).join('')}
    ${STATE.presets.length === 0 ? '<div style="color:var(--dim);font-size:12px;padding:8px">no presets yet</div>' : ''}
  </div>
</div>`;
  }
  bindSidebarEvents();
}

function bindSidebarEvents() {
  document.querySelectorAll('#conversation-list .list-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      switchConversation(el.dataset.cid);
    });
  });

  document.querySelectorAll('.del-conv').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      deleteConversation(b.dataset.cid);
    });
  });

  const newConvBtn = $('new-conversation-btn');
  if (newConvBtn) {
    newConvBtn.addEventListener('click', newConversation);
  }

  document.querySelectorAll('.list-item[data-pid]').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('button')) return;
      const pid = el.dataset.pid;
      if (STATE.sidebarPanel === 'projects') {
        selectProject(pid);
      } else {
        STATE.activePresetId = pid;
        updatePresetBadge();
        renderSidebar();
      }
    });
  });

  document.querySelectorAll('.edit-proj').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      const pid = b.dataset.pid;
      const proj = STATE.projects.find(p => p.id === pid);
      if (!proj) return;
      showModal('Edit Project', `<div class="field"><label>Name</label><input id="eproj-name" value="${esc(proj.name)}"></div>
<div class="field"><label>Description</label><textarea id="eproj-desc">${esc(proj.description || '')}</textarea></div>
<div class="field"><label>Instructions (for AI)</label><textarea id="eproj-instr">${esc(proj.instructions || '')}</textarea></div>
<div class="field"><label>Active Preset</label><select id="eproj-preset">
  <option value="">none</option>
  ${STATE.presets.map(p => `<option value="${p.id}"${proj.active_preset_id === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}
</select></div>
<div class="btns"><button onclick="hideModal()">cancel</button><button class="green" id="save-eproj" data-pid="${pid}">save</button></div>`);
      document.getElementById('save-eproj').addEventListener('click', async () => {
        const body = {
          name: $('eproj-name').value.trim(),
          description: $('eproj-desc').value.trim(),
          instructions: $('eproj-instr').value.trim(),
          active_preset_id: $('eproj-preset').value,
        };
        await API(`/api/projects/${pid}`, { method: 'PUT', body: JSON.stringify(body) });
        hideModal();
        await loadProjects();
        if (pid === STATE.activeProjectId) await selectProject(pid);
        t('project updated');
      });
    });
  });

  document.querySelectorAll('.del-proj').forEach(b => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this project?')) return;
      const pid = b.dataset.pid;
      await API(`/api/projects/${pid}`, { method: 'DELETE' });
      if (pid === STATE.activeProjectId) {
        STATE.activeProjectId = '';
        STATE.activeConversationId = '';
        STATE.messages = [];
        STATE.galleryImages = [];
        renderMessages();
        renderGallery();
        updateBadges();
      }
      await loadProjects();
      t('project deleted');
    });
  });

  const newProjBtn = $('new-project-btn');
  if (newProjBtn) {
    newProjBtn.addEventListener('click', () => {
      showModal('New Project', `<div class="field"><label>Name</label><input id="nproj-name" placeholder="My Project"></div>
<div class="field"><label>Description</label><textarea id="nproj-desc" placeholder="Describe your project..."></textarea></div>
<div class="btns"><button onclick="hideModal()">cancel</button><button class="green" id="save-nproj">create</button></div>`);
      document.getElementById('save-nproj').addEventListener('click', async () => {
        const name = $('nproj-name').value.trim();
        if (!name) return toastErr('name required');
        const proj = await API('/api/projects', { method: 'POST', body: JSON.stringify({ name, description: $('nproj-desc').value.trim() }) });
        hideModal();
        await loadProjects();
        await selectProject(proj.id);
        t('project created');
      });
    });
  }

  const newPresetBtn = $('new-preset-btn');
  if (newPresetBtn) {
    newPresetBtn.addEventListener('click', () => showPresetModal());
  }

  document.querySelectorAll('.edit-preset').forEach(b => {
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      const pid = b.dataset.pid;
      const preset = STATE.presets.find(p => p.id === pid);
      if (!preset) return;
      showPresetModal(preset);
    });
  });

  document.querySelectorAll('.del-preset').forEach(b => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm('Delete this preset?')) return;
      const pid = b.dataset.pid;
      await API(`/api/presets/${pid}`, { method: 'DELETE' });
      if (pid === STATE.activePresetId) {
        STATE.activePresetId = '';
        updatePresetBadge();
      }
      await loadPresets();
      t('preset deleted');
    });
  });

  document.querySelectorAll('.activate-preset').forEach(b => {
    b.addEventListener('click', async (e) => {
      e.stopPropagation();
      const pid = b.dataset.pid;
      STATE.activePresetId = pid;
      if (STATE.activeProjectId) {
        await API(`/api/projects/${STATE.activeProjectId}`, { method: 'PUT', body: JSON.stringify({ active_preset_id: pid }) });
        await loadProjects();
      }
      updatePresetBadge();
      renderSidebar();
      t('preset activated');
    });
  });

  const uploadBtn = $('upload-asset-btn');
  if (uploadBtn) {
    uploadBtn.addEventListener('click', () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/*';
      input.addEventListener('change', async () => {
        const file = input.files[0];
        if (!file) return;
        if (file.size > 10 * 1024 * 1024) { toastErr('file too large (>10MB)'); return; }
        const reader = new FileReader();
        reader.onload = async () => {
          const b64 = reader.result.split(',')[1];
          const ext = file.name.split('.').pop().toLowerCase();
          await API(`/api/projects/${STATE.activeProjectId}/assets`, {
            method: 'POST',
            body: JSON.stringify({ name: file.name, data: b64, file_type: ext, description: '' }),
          });
          await loadProjects();
          t('asset uploaded');
        };
        reader.readAsDataURL(file);
      });
      input.click();
    });
  }

  const refreshJobsBtn = $('refresh-jobs-btn');
  if (refreshJobsBtn) {
    refreshJobsBtn.addEventListener('click', async () => {
      const jobs = await API(`/api/jobs/${STATE.activeProjectId}`);
      const jobsDiv = $('mini-jobs');
      if (jobs.length === 0) {
        jobsDiv.innerHTML = 'no jobs';
      } else {
        jobsDiv.innerHTML = jobs.map(j => `<div style="font-size:10px;padding:3px 0;border-bottom:1px solid var(--border)">${esc(j.prompt).slice(0,40)} <span style="color:${j.status==='completed'?'var(--green)':j.status==='failed'?'#ff6b6f':'var(--muted)'}">${j.status}</span></div>`).join('');
      }
    });
  }
}

function showPresetModal(preset) {
  const isEdit = !!preset;
  const title = isEdit ? 'Edit Preset' : 'New Preset';
  const d = preset || {};
  showModal(title, `<div class="field"><label>Name</label><input id="pre-name" value="${esc(d.name || '')}"></div>
<div class="field-group">
  <h4>Logic / Reasoning Model</h4>
  <div class="row">
    <div class="field"><label>Provider</label><input id="pre-logic-provider" placeholder="openai" value="${esc(d.logic_provider || '')}"></div>
    <div class="field"><label>Model</label><input id="pre-logic-model" placeholder="gpt-4o" value="${esc(d.logic_model || '')}"></div>
  </div>
  <div class="field"><label>API URL</label><input id="pre-logic-url" placeholder="https://api.openai.com/v1" value="${esc(d.logic_url || '')}"></div>
  <div class="field"><label>API Key</label><input id="pre-logic-key" type="password" placeholder="${esc(d.logic_key || 'sk-...')}" autocomplete="off"></div>
</div>
<div class="field-group">
  <h4>Image Generation Model</h4>
  <div class="row">
    <div class="field"><label>Provider</label><input id="pre-image-provider" placeholder="openai" value="${esc(d.image_provider || '')}"></div>
    <div class="field"><label>Model</label><input id="pre-image-model" placeholder="dall-e-3" value="${esc(d.image_model || '')}"></div>
  </div>
  <div class="field"><label>API URL</label><input id="pre-image-url" placeholder="https://api.openai.com/v1" value="${esc(d.image_url || '')}"></div>
  <div class="field"><label>API Key</label><input id="pre-image-key" type="password" placeholder="${esc(d.image_key || 'sk-...')}" autocomplete="off"></div>
</div>
<div class="field-group">
  <h4>Defaults</h4>
  <div class="row">
    <div class="field"><label>Aspect Ratio</label>
      <select id="pre-ratio">
        ${['1:1','3:4','4:3','16:9','9:16'].map(r => `<option value="${r}"${(d.default_aspect_ratio || '1:1') === r ? ' selected' : ''}>${r}</option>`).join('')}
      </select>
    </div>
    <div class="field"><label>Resolution</label><input id="pre-res" value="${esc(d.default_resolution || '1024x1024')}"></div>
    <div class="field"><label>Count</label><input id="pre-count" type="number" min="1" max="4" value="${d.default_count || 1}"></div>
  </div>
  <div class="field"><label>System Prompt</label><textarea id="pre-sysprompt" placeholder="AI assistant instructions...">${esc(d.system_prompt || '')}</textarea></div>
</div>
<div class="btns">
  <button onclick="hideModal()">cancel</button>
  ${isEdit ? `<button class="danger" id="test-preset-btn" data-pid="${d.id}">test</button>` : ''}
  <button class="green" id="save-pre">${isEdit ? 'save' : 'create'}</button>
</div>`);

  document.getElementById('save-pre').addEventListener('click', async () => {
    try {
      const body = {
        name: $('pre-name').value.trim(),
        logic_provider: $('pre-logic-provider').value.trim(),
        logic_url: $('pre-logic-url').value.trim(),
        logic_key: $('pre-logic-key').value.trim(),
        logic_model: $('pre-logic-model').value.trim(),
        image_provider: $('pre-image-provider').value.trim(),
        image_url: $('pre-image-url').value.trim(),
        image_key: $('pre-image-key').value.trim(),
        image_model: $('pre-image-model').value.trim(),
        default_aspect_ratio: $('pre-ratio').value,
        default_resolution: $('pre-res').value.trim(),
        default_count: parseInt($('pre-count').value, 10) || 1,
        system_prompt: $('pre-sysprompt').value.trim(),
      };
      if (!body.name) return toastErr('name required');
      if (isEdit) {
        // Empty key field means "keep the stored key" — never round-trip the
        // masked placeholder back into the database.
        if (!body.logic_key) delete body.logic_key;
        if (!body.image_key) delete body.image_key;
        await API(`/api/presets/${d.id}`, { method: 'PUT', body: JSON.stringify(body) });
      } else {
        await API('/api/presets', { method: 'POST', body: JSON.stringify(body) });
      }
      hideModal();
      await loadPresets();
      t(isEdit ? 'preset updated' : 'preset created');
    } catch (e) {
      toastErr(e.message);
    }
  });

  if (isEdit) {
    const testBtn = document.getElementById('test-preset-btn');
    if (testBtn) {
      testBtn.addEventListener('click', async () => {
        testBtn.disabled = true;
        testBtn.textContent = 'testing...';
        const results = [];
        // Read what the user typed, not the captured preset object —
        // otherwise "test" validates stale credentials.
        const iu = $('pre-image-url').value.trim();
        const ik = $('pre-image-key').value.trim() || preset.image_key;
        const im = $('pre-image-model').value.trim();
        const lu = $('pre-logic-url').value.trim();
        const lk = $('pre-logic-key').value.trim() || preset.logic_key;
        const lm = $('pre-logic-model').value.trim();
        if (iu) {
          const r = await API('/api/test-model', {
            method: 'POST',
            body: JSON.stringify({ api_url: iu, api_key: ik, model: im, type: 'image' }),
          });
          results.push(`Image: ${r.ok ? 'OK' : 'FAIL - ' + r.error}`);
        }
        if (lu) {
          const r = await API('/api/test-model', {
            method: 'POST',
            body: JSON.stringify({ api_url: lu, api_key: lk, model: lm, type: 'logic' }),
          });
          results.push(`Logic: ${r.ok ? 'OK' : 'FAIL - ' + r.error}`);
        }
        testBtn.disabled = false;
        testBtn.textContent = 'test';
        results.length ? t(results.join(' | ')) : toastErr('nothing to test — enter api urls first');
      });
    }
  }
}

let _selToken = 0;

async function selectProject(pid) {
  const tok = ++_selToken;
  STATE.activeProjectId = pid;
  STATE.activeConversationId = '';
  STATE.messages = [];
  STATE.galleryImages = [];
  invalidateGallery();
  renderMessages();
  renderGallery();
  try {
    const proj = await API(`/api/projects/${pid}`);
    if (tok !== _selToken) return;
    STATE.activePresetId = proj.active_preset_id || '';
  } catch (e) {
    if (tok !== _selToken) return;
    STATE.activePresetId = '';
  }
  updateBadges();
  await loadProjects();
  if (tok !== _selToken) return;
  await loadConversations();
  if (tok !== _selToken) return;
  await loadGallery();
  if (tok !== _selToken) return;
  renderSidebar();
}

function updateBadges() {
  const proj = STATE.projects.find(p => p.id === STATE.activeProjectId);
  $('project-badge').textContent = proj ? proj.name : 'no project';
  $('project-badge').style.background = proj ? '' : 'var(--bg)';
  updatePresetBadge();
}

function updatePresetBadge() {
  const preset = STATE.presets.find(p => p.id === STATE.activePresetId);
  $('preset-badge').textContent = preset ? preset.name : 'no preset';
}

function renderMessages() {
  const container = $('chat-messages');
  if (STATE.messages.length === 0) {
    container.innerHTML = `<div class="empty-state" style="padding:40px">
<div style="font-size:32px;margin-bottom:10px">✨</div>
<div style="font-size:14px;color:var(--text);margin-bottom:4px">Start a conversation</div>
<div style="font-size:12px">Describe what you want to create. The AI will help plan and generate images.</div>
</div>`;
    return;
  }

  container.innerHTML = STATE.messages.map(m => {
    const isUser = m.role === 'user';
    let content = esc(m.content);
    let extra = '';
    if (m.role === 'assistant') {
      try {
        const plan = JSON.parse(m.content);
        if (plan.action === 'generate' && plan.generations) {
          content = '<strong>Generation Plan:</strong><br>' + plan.generations.map((g, i) => (i + 1) + '. "' + esc(g.prompt || '') + '" ' + (g.aspectRatio || '') + ' ' + (g.count ? 'x' + g.count : '')).join('<br>');
        } else if (plan.action === 'chat' && plan.message) {
          content = esc(plan.message);
        }
      } catch (e) {}

      const isError = m.content.startsWith('[Error:') || m.content.startsWith('[No logic');
      if (isError) {
        content = esc(m.content);
        extra = ' msg-error';
      }
    }
    return `<div class="msg ${m.role}${extra}">
<div class="msg-avatar">${isUser ? 'U' : 'AI'}</div>
<div class="msg-content">${content}</div>
</div>`;
  }).join('');
  container.scrollTop = container.scrollHeight;
}

let _gallerySig = null;

function renderGallery() {
  const g = $('gallery-grid');
  // Rebuilding innerHTML recreates every <img> and resets scroll; skip when
  // the visible list is unchanged (polling can call this repeatedly).
  const sig = STATE.galleryImages.length === 0 ? '\u2205' : STATE.galleryImages.map(i => i.id).join(',');
  if (sig === _gallerySig) return;
  _gallerySig = sig;

  if (STATE.galleryImages.length === 0) {
    g.innerHTML = '<div class="empty-state"><div style="font-size:32px;margin-bottom:10px">🎨</div><div>no images yet</div><div style="font-size:12px;margin-top:4px">generate images from chat</div></div>';
    return;
  }

  g.innerHTML = STATE.galleryImages.map(img => {
    // Never embed provider-supplied URLs in HTML/JS string context — route
    // every image through our local, origin-safe serving endpoint.
    const src = `/api/gallery-image/${img.id}`;
    const prompt = esc(img.prompt || '');
    return `<div class="card" data-iid="${img.id}">
<img src="${esc(src)}" loading="lazy" alt="${prompt.slice(0, 120) || 'generated image'}">
<div class="card-actions">
  <button data-act="download" data-iid="${img.id}" aria-label="download image" title="download">d</button>
  <button data-act="delete" data-iid="${img.id}" aria-label="delete image" title="delete" style="color:#ff6b6f">x</button>
</div>
<div class="meta">
  <span title="${prompt}">${prompt.slice(0, 40)}</span>
  <span class="model-badge">${esc(img.image_model || '')}</span>
</div>
</div>`;
  }).join('');
}

window.showLightbox = (src) => {
  $('lbimg').src = src;
  $('lightbox').style.display = 'flex';
};

window.downloadImg = (src) => {
  const a = document.createElement('a');
  a.href = src;
  a.download = 'img-' + Date.now() + '.png';
  a.click();
};

window.delImg = async (iid) => {
  if (!confirm('Delete this image?')) return;
  await API(`/api/gallery/${iid}`, { method: 'DELETE' });
  invalidateGallery();
  await loadGallery();
  t('image deleted');
};

// Delegated gallery actions (buttons carry data-act/data-iid, so provider
// URLs never appear in JS string context).
$('gallery-grid').addEventListener('click', (e) => {
  const img = e.target.closest('img');
  const btn = e.target.closest('button[data-act]');
  if (btn) {
    const iid = btn.dataset.iid;
    if (btn.dataset.act === 'delete') return delImg(iid);
    if (btn.dataset.act === 'download') return downloadImg(`/api/gallery-image/${iid}`);
    return;
  }
  if (img) showLightbox(img.src);
});

async function sendMessage() {
  const input = $('prompt');
  const content = input.value.trim();
  if (!content || STATE.loading) return;
  if (!STATE.activeProjectId) return toastErr('select a project first');

  STATE.loading = true;
  $('send-btn').classList.add('loading');
  setStatus('thinking...', true);

  let queuedJobs = 0;
  try {
    if (!STATE.activeConversationId) {
      const conv = await API(`/api/projects/${STATE.activeProjectId}/conversations`, {
        method: 'POST',
        body: JSON.stringify({ title: content.slice(0, 60) }),
      });
      STATE.activeConversationId = conv.id;
    }

    const result = await API(`/api/conversations/${STATE.activeConversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
    await loadMessages();
    if (result.plan_error) {
      toastErr('model response rejected: ' + result.plan_error);
    } else if (result.jobs && result.jobs.length > 0) {
      queuedJobs = result.jobs.length;
      t(`queued ${result.jobs.length} generation job(s)...`);
      await loadGallery();
      pollJobs();
    }
    input.value = '';
  } catch (e) {
    toastErr('request failed: ' + e.message);
  }

  STATE.loading = false;
  $('send-btn').classList.remove('loading');
  setStatus(queuedJobs ? `${queuedJobs} job(s) generating...` : 'ready', queuedJobs > 0);
}

function pollJobs() {
  if (STATE._poll) return;
  STATE._poll = setInterval(async () => {
    if (!STATE.activeProjectId) { clearInterval(STATE._poll); STATE._poll = null; return; }
    const jobs = await API(`/api/jobs/${STATE.activeProjectId}`).catch(() => []);
    const pending = jobs.filter(j => j.status === 'queued' || j.status === 'running');
    const failed = jobs.filter(j => j.status === 'failed' && !j._reported);
    if (failed.length) {
      failed.forEach(j => { j._reported = true; });
      toastErr(`generation failed: ${(failed[0].error || 'unknown error').slice(0, 120)}`);
    }
    if (jobs.length === 0 || pending.length === 0) {
      clearInterval(STATE._poll);
      STATE._poll = null;
      if (STATE.view === 'gallery') await loadGallery();
      setStatus('ready', false);
      t('generation finished');
    } else {
      setStatus(`${pending.length} job(s) generating...`, true);
    }
  }, 3000);
}

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Modal templates use inline onclick, so these must live on window.
window.hideModal = hideModal;

async function init() {
  await loadProjects();
  await loadPresets();
  updateBadges();
  // Open straight into a project so the studio never lands on an empty screen.
  if (!STATE.activeProjectId && STATE.projects.length > 0) {
    await selectProject(STATE.projects[0].id);
  }
}
window.init = init;
window.loadProjects = loadProjects;
window.loadPresets = loadPresets;
window.selectProject = selectProject;
window.switchConversation = switchConversation;
window.newConversation = newConversation;
window.deleteConversation = deleteConversation;
window.renderConversationList = renderConversationList;
window.STATE = STATE;

$('lightbox').addEventListener('click', () => { $('lightbox').style.display = 'none'; });

$('send-btn').addEventListener('click', sendMessage);
$('prompt').addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') sendMessage();
});

$('switch-gallery').addEventListener('click', () => {
  STATE.view = 'gallery';
  $('chat-area').style.display = 'none';
  $('gallery-area').classList.add('on');
  loadGallery();
});
$('switch-chat').addEventListener('click', () => {
  STATE.view = 'chat';
  $('chat-area').style.display = '';
  $('gallery-area').classList.remove('on');
});

document.querySelectorAll('.snav-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.snav-btn').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    STATE.sidebarPanel = b.dataset.panel;
    renderSidebar();
  });
});

$('menu-btn').addEventListener('click', () => {
  $('sidebar').classList.toggle('mobile');
});

init();