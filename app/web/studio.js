/* Prompt Studio — front end.
 *
 * Plain ES2020, no build step, no framework. Three ideas keep it honest:
 *
 * 1. Every list is rendered from what the API returned. Nothing is invented client
 *    side, so an empty list is an empty state and not a placeholder row.
 * 2. Capability-gated controls: the studios only render a parameter if the *model*
 *    documents it, so a control that would fail at submit time is never drawn.
 * 3. Errors are shown as they arrive, with the server's code and hint — never
 *    swallowed into a silent no-op.
 */

'use strict';

const $ = (id) => document.getElementById(id);
const api = {
  async call(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let payload = null;
    try { payload = await res.json(); } catch (e) { payload = null; }
    if (!res.ok) {
      const err = (payload && payload.error) || { message: 'HTTP ' + res.status };
      const e = new Error(err.message || 'request failed');
      e.code = err.code;
      e.hint = err.hint;
      e.details = err.details;
      e.status = res.status;
      throw e;
    }
    return payload;
  },
  get: (p) => api.call('GET', p),
  post: (p, b) => api.call('POST', p, b),
  patch: (p, b) => api.call('PATCH', p, b),
  del: (p) => api.call('DELETE', p),
};

/* ------------------------------------------------------------------ state */
const state = {
  view: 'image',
  models: [],
  connections: [],
  presets: [],
  prompts: [],
  projects: [],
  assets: [],
  activeConnection: null,
  jobTimers: new Map(),
  lastJob: { image: null, video: null },
  failures: new Set(),
};

/* ------------------------------------------------------------------ dom */
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const key of Object.keys(attrs || {})) {
    const value = attrs[key];
    if (value === null || value === undefined || value === false) continue;
    if (key === 'text') node.textContent = value;
    else if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of [].concat(children === undefined ? [] : children)) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'object' ? child : document.createTextNode(String(child)));
  }
  return node;
}

function mount(target, children) {
  target.replaceChildren.apply(target, [].concat(children).filter(Boolean));
}

function chip(text, kind, extra) {
  return el('span', { class: 'chip ' + (kind || '') + (extra ? ' ' + extra : ''), text });
}

function emptyState(title, detail) {
  return el('div', { class: 'empty' }, [el('strong', { text: title }), detail || '']);
}

function banner(message, kind) {
  return el('div', { class: 'banner ' + (kind || 'bad') }, [
    el('div', { class: 'msg', text: message }),
  ]);
}

function errorBanner(error) {
  const text = error.message + (error.hint ? ' — ' + error.hint : '');
  const parts = [el('div', { class: 'msg', text })];
  if (error.code) parts.push(el('code', { text: error.code }));
  return el('div', { class: 'banner bad' }, parts);
}

function showError(target, error) {
  mount(target, [errorBanner(error)]);
}

function toast(message, kind, ttl) {
  const node = el('div', { class: 'toast ' + (kind || '') }, [
    el('span', { text: message }),
    el('button', { class: 'close', text: '×', onclick: () => node.remove(), 'aria-label': 'Dismiss' }),
  ]);
  $('toasts').appendChild(node);
  // A degraded-deployment notice explains why lists are empty, so it stays longer than
  // an ordinary confirmation and can be dismissed.
  node.timer = setTimeout(() => node.remove(), ttl || 5000);
}

function fmtBytes(n) {
  if (!n && n !== 0) return '';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return Math.round(n / 1024) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtWhen(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d)) return '—';
  const mins = Math.round((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  if (mins < 1440) return Math.round(mins / 60) + 'h ago';
  return d.toLocaleDateString();
}

function statusChip(status) {
  const kind = status === 'succeeded' ? 'ok'
    : status === 'failed' ? 'bad'
    : status === 'cancelled' ? ''
    : 'warn';
  return chip(status, kind);
}

/* ------------------------------------------------------------------ routing */
const VIEWS = ['image', 'video', 'jobs', 'projects', 'library', 'prompts',
               'presets', 'characters', 'models', 'usage'];

const LOADERS = {
  image: loadStudioModels,
  video: loadStudioModels,
  jobs: loadJobs,
  projects: loadProjects,
  library: loadLibrary,
  prompts: loadPrompts,
  presets: loadPresets,
  characters: loadCharacters,
  models: loadModels,
  usage: loadUsage,
};

function go(view) {
  if (!VIEWS.includes(view)) view = 'image';
  state.view = view;
  for (const name of VIEWS) {
    $('view-' + name).hidden = name !== view;
  }
  for (const button of document.querySelectorAll('.navitem')) {
    if (button.dataset.view === view) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  }
  $('main').scrollTop = 0;
  try { history.replaceState(null, '', '#' + view); } catch (e) { /* file:// */ }
  const loader = LOADERS[view];
  if (loader) loader().catch((error) => toast(error.message, 'bad'));
}

/* ------------------------------------------------------------------ health */
async function loadHealth() {
  try {
    const health = await api.get('/api/health');
    // "degraded" is a real answer, not a failure: the API is up but the deployment is
    // missing something it needs (a durable database, blob storage, a cron secret).
    // On a serverless platform the data is then per-instance, so a list can legitimately
    // come back empty a moment after something was created. Saying so is the difference
    // between "this is broken" and "this deployment is not configured yet".
    const degraded = health.status === 'degraded';
    $('healthDot').className = degraded ? 'dot warn' : 'dot ok';
    $('healthText').textContent = degraded
      ? 'degraded — data is not durable'
      : health.counts.connections + ' conn · ' + health.counts.models + ' models · ' +
        health.counts.jobs + ' jobs';
    if (degraded && !state.failures.has('degraded')) {
      state.failures.add('degraded');
      toast('Deployment is degraded: ' + (health.problems || []).join(' '), 'warn', 12000);
    }
    $('versionLine').textContent = 'schema v' + health.schema_version + ' · ' + health.env;
    state.failures.delete('health');
  } catch (error) {
    $('healthDot').className = 'dot bad';
    $('healthText').textContent = 'server unreachable';
    if (!state.failures.has('health')) {
      state.failures.add('health');
      toast('The API is not responding: ' + error.message, 'bad');
    }
  }
}

/* ================================================================== studios */
async function loadStudioModels() {
  const res = await api.get('/api/models');
  state.models = res.data;
  renderStudio('image', res.data.filter((m) => m.modality === 'image'));
  renderStudio('video', res.data.filter((m) => m.modality === 'video'));
}

const PREFIX = { image: 'img', video: 'vid' };

function renderStudio(kind, models) {
  const p = PREFIX[kind];
  const select = $(p + 'Model');
  const previous = select.value;
  select.replaceChildren();

  if (!models.length) {
    select.appendChild(el('option', { value: '', text: 'No ' + kind + ' model registered' }));
    mount($(p + 'Caps'), []);
    mount($(p + 'Params'), []);
    mount($(p + 'DemoNote'), []);
    const note = el('div', { class: 'banner info' }, [
      el('div', { class: 'msg' }, [
        'Add a connection and register a ', kind, ' model first — ',
        el('a', { href: '#connections', text: 'open Connections',
                  onclick: (e) => { e.preventDefault(); go('connections'); } }),
        '.',
      ]),
    ]);
    mount($(p + 'Params'), [note]);
    $(p + 'Generate').disabled = true;
    return;
  }

  for (const model of models) {
    select.appendChild(el('option', {
      value: model.id,
      text: model.display_name + ' · ' + model.connection_name,
    }));
  }
  if (models.some((m) => m.id === previous)) select.value = previous;
  $(p + 'Generate').disabled = false;
  $(p + 'ModelCount') && ($(p + 'ModelCount').textContent =
    models.length + (models.length === 1 ? ' model' : ' models'));
  renderStudioFields(kind);
}

function studioModel(kind) {
  const id = $(PREFIX[kind] + 'Model').value;
  return state.models.find((m) => m.id === id) || null;
}

function renderStudioFields(kind) {
  const p = PREFIX[kind];
  const model = studioModel(kind);
  mount($(p + 'Caps'), []);
  mount($(p + 'Params'), []);
  mount($(p + 'DemoNote'), []);

  if (!model) return;

  mount($(p + 'Caps'), model.capabilities.map((c) => chip(c)));

  if (model.metadata && model.metadata.offline) {
    mount($(p + 'DemoNote'), [
      el('div', { class: 'banner warn' }, [
        el('div', { class: 'msg',
          text: 'Offline demo model: outputs are placeholders stamped "DEMO IMAGE", and no cost is reported.' }),
      ]),
    ]);
  }

  // Only the parameters this model documents. No control is drawn speculatively.
  const fields = (model.parameters || []).filter((spec) =>
    spec.name !== 'seed' && spec.name !== 'negative_prompt' &&
    !(spec.name === 'count' && spec.maximum === 1));

  const rows = [];
  for (const spec of fields) {
    const id = p + '-p-' + spec.name;
    let control;
    if (spec.type === 'enum' && spec.choices && spec.choices.length) {
      control = el('select', { id });
      for (const choice of spec.choices) {
        control.appendChild(el('option', { value: String(choice), text: String(choice) }));
      }
      if (spec.default !== null && spec.default !== undefined) control.value = String(spec.default);
    } else if (spec.type === 'int') {
      control = el('input', { id, type: 'number', inputmode: 'numeric' });
      if (spec.minimum !== null && spec.minimum !== undefined) control.min = spec.minimum;
      if (spec.maximum !== null && spec.maximum !== undefined) control.max = spec.maximum;
      control.value = spec.default !== null && spec.default !== undefined
        ? spec.default : (spec.minimum || 1);
    } else {
      control = el('input', { id, type: 'text' });
      if (spec.default !== null && spec.default !== undefined) control.value = spec.default;
    }
    rows.push(el('label', { class: 'field' }, [
      el('span', { class: 'lbl', text: spec.name.replace(/_/g, ' ') }),
      control,
      spec.minimum !== null && spec.maximum !== null && spec.minimum !== undefined
        ? el('span', { class: 'hint', text: 'between ' + spec.minimum + ' and ' + spec.maximum })
        : null,
    ]));
  }
  mount($(p + 'Params'), rows);

  // Negative prompt and seed are real parameters on several models, so they get a
  // collapsible block rather than being dropped.
  const advanced = ['negative_prompt', 'seed'].filter((name) =>
    (model.parameters || []).some((spec) => spec.name === name));
  if (advanced.length) {
    const details = el('details', { class: 'mt3' }, [
      el('summary', { class: 'small muted', style: 'cursor:pointer', text: 'Advanced' }),
    ]);
    for (const name of advanced) {
      const spec = model.parameters.find((s) => s.name === name);
      const id = p + '-p-' + name;
      const control = el('input', { id, type: spec.type === 'int' ? 'number' : 'text' });
      details.appendChild(el('label', { class: 'field mt3' }, [
        el('span', { class: 'lbl', text: name.replace(/_/g, ' ') }), control,
      ]));
    }
    $(p + 'Params').appendChild(details);
  }
}

function collectParams(kind) {
  const model = studioModel(kind);
  const params = {};
  if (!model) return params;
  for (const spec of model.parameters || []) {
    const input = $(PREFIX[kind] + '-p-' + spec.name);
    if (!input || input.value === '') continue;
    params[spec.name] = spec.type === 'int' ? Number(input.value) : input.value;
  }
  return params;
}

async function submitJob(kind) {
  const p = PREFIX[kind];
  const model = studioModel(kind);
  const status = $(p + 'Status');
  status.textContent = '';
  if (!model) { status.textContent = 'Pick a model first.'; return; }
  const prompt = $(p + 'Prompt').value.trim();
  if (!prompt) { status.textContent = 'Write a prompt first.'; return; }

  $(p + 'Generate').disabled = true;
  status.textContent = 'Submitting…';
  try {
    const endpoint = kind === 'image' ? '/api/generations/images'
                                     : '/api/generations/videos';
    const res = await api.post(endpoint, {
      model_pk: model.id, prompt, params: collectParams(kind),
    });
    status.textContent = 'Job ' + res.data.id.slice(4, 10) + ' submitted';
    watchJob(res.data.id, kind);
    loadJobs().catch(() => {});
  } catch (error) {
    status.textContent = '';
    showError($(p + 'Result'), error);
    if (error.code === 'duplicate_submission' && error.details && error.details.job_id) {
      watchJob(error.details.job_id, kind);
    }
  } finally {
    $(p + 'Generate').disabled = false;
  }
}

function watchJob(jobId, kind) {
  const p = PREFIX[kind];
  const target = $(p + 'Result');
  clearInterval(state.jobTimers.get(jobId));
  state.lastJob[kind] = jobId;

  const paint = async () => {
    try {
      const res = await api.get('/api/jobs/' + jobId);
      renderJob(res.data, target, $(p + 'JobActions'));
      const terminal = ['succeeded', 'failed', 'cancelled'].includes(res.data.status);
      if (terminal) {
        clearInterval(state.jobTimers.get(jobId));
        state.jobTimers.delete(jobId);
        if (res.data.status === 'succeeded') {
          loadHealth().catch(() => {});
          loadJobs().catch(() => {});
        }
      }
    } catch (error) {
      clearInterval(state.jobTimers.get(jobId));
      state.jobTimers.delete(jobId);
      showError(target, error);
    }
  };
  paint();
  state.jobTimers.set(jobId, setInterval(paint, 1100));
}

function renderJob(job, target, actionsTarget) {
  mount(actionsTarget || el('div'), []);

  const statusKind = job.status === 'succeeded' ? 'done'
    : job.status === 'failed' ? 'failed' : '';
  const parts = [];

  parts.push(el('div', { class: 'job-status' }, [
    statusChip(job.status),
    chip(job.kind, 'accent'),
    el('span', { class: 'tiny muted mono', text: job.id }),
    job.provider === 'mock' ? chip('offline demo', 'warn') : null,
    el('span', { class: 'spacer' }),
    el('span', { class: 'tiny muted', text: job.progress + '%' }),
  ]));

  parts.push(el('div', { class: 'progress ' + statusKind }, [
    el('div', { style: 'width:' + Math.max(2, job.progress) + '%' }),
  ]));

  if (job.status === 'failed' && job.error) {
    parts.push(el('div', { class: 'banner bad' }, [
      el('div', { class: 'msg', text: job.error.message }),
      el('code', { text: job.error.code }),
    ]));
  }

  if (job.prompt) {
    parts.push(el('div', { class: 'mt3' }, [
      el('div', { class: 'tiny muted', text: 'Prompt' }),
      el('div', { class: 'small', text: job.prompt }),
    ]));
  }

  if (job.outputs && job.outputs.length) {
    const grid = el('div', { class: 'media-grid mt4' });
    for (const output of job.outputs) {
      if (!output.asset_id) continue;
      const url = '/api/assets/' + output.asset_id + '/raw';
      const isVideo = (output.mime || '').startsWith('video/');
      grid.appendChild(el('div', { class: 'media' }, [
        el('div', { class: 'overlay' }, [
          el('a', { class: 'btn', href: url, download: '', text: 'Download' }),
        ]),
        isVideo
          ? el('video', { class: 'thumb', src: url, controls: '' })
          : el('a', { href: url, target: '_blank', rel: 'noopener' },
              [el('img', { class: 'thumb', src: url, alt: 'generated output', loading: 'lazy' })]),
        el('div', { class: 'meta' }, [
          el('span', { class: 'name', text: output.width ? output.width + '×' + output.height : output.mime }),
          el('span', { class: 'size', text: fmtTime(output.created_at) }),
        ]),
      ]));
    }
    parts.push(grid);
  }

  if (job.events && job.events.length) {
    const list = el('ul', { class: 'timeline mt4' });
    for (const event of job.events) {
      list.appendChild(el('li', {}, [
        el('span', { class: 't', text: fmtTime(event.created_at) }),
        el('span', { class: 'what' }, [
          el('b', { text: event.to_status }),
          event.detail ? ' · ' + event.detail : '',
        ]),
      ]));
    }
    parts.push(el('div', { class: 'mt3' }, [
      el('div', { class: 'tiny muted', style: 'margin-bottom:6px', text: 'History' }), list,
    ]));
  }

  if (job.usage) {
    parts.push(el('div', { class: 'inline mt3' }, [
      chip(job.usage.quantity + ' output(s)'),
      chip('cost: ' + (job.usage.estimated_cost === null
        ? 'unknown' : job.usage.estimated_cost + ' ' + job.usage.currency),
        job.usage.estimated_cost === null ? 'warn' : 'ok'),
    ]));
  }

  const canCancel = ['queued', 'running', 'cancelling'].includes(job.status);
  const canRetry = ['failed', 'cancelled'].includes(job.status);
  if (actionsTarget) {
    const buttons = [];
    if (canCancel) buttons.push(el('button', { class: 'btn sm', text: 'Cancel',
      onclick: () => jobAction(job.id, 'cancel', target) }));
    if (canRetry) buttons.push(el('button', { class: 'btn sm', text: 'Retry',
      onclick: () => jobAction(job.id, 'retry', target) }));
    mount(actionsTarget, buttons);
  }

  mount(target, parts);
}

async function jobAction(jobId, action, target) {
  try {
    await api.post(action === 'cancel'
      ? '/api/jobs/' + jobId + '/cancel'
      : '/api/jobs/' + jobId + '/retry');
    toast(action === 'cancel' ? 'Cancellation requested' : 'Job requeued', 'ok');
    const res = await api.get('/api/jobs/' + jobId);
    const kind = res.data.kind;
    watchJob(jobId, kind);
    loadJobs().catch(() => {});
  } catch (error) {
    showError(target, error);
  }
}

/* ================================================================== jobs */
async function loadJobs() {
  const status = $('jobsFilter').value;
  const query = status ? '?status=' + encodeURIComponent(status) : '';
  const res = await api.get('/api/jobs' + query);
  const list = $('jobsList');

  const open = res.jobs.filter((j) => ['queued', 'running', 'cancelling'].includes(j.status)).length;
  const badge = $('jobsCount');
  badge.hidden = open === 0;
  badge.textContent = String(open);

  if (!res.jobs.length) {
    mount(list, [emptyState('No jobs yet',
      status ? 'Nothing with that status.' : 'Generate something and it will appear here.')]);
  } else {
    mount(list, res.jobs.map((job) => el('div', { class: 'row-item' }, [
      statusChip(job.status),
      chip(job.kind),
      el('div', { class: 'grow' }, [
        el('div', { class: 'title', text: job.prompt || '(no prompt)' }),
        el('div', { class: 'sub' }, [
          job.id + ' · ' + fmtWhen(job.created_at) +
          (job.error ? ' · ' + job.error.code : ''),
        ]),
      ]),
      el('div', { class: 'actions' }, [
        el('button', { class: 'btn sm', text: 'Open', onclick: () => openJob(job) }),
      ]),
    ])));
  }
  $('jobsMeta').textContent = res.total + ' job(s)' + (status ? ' · filtered by ' + status : '');
}

async function openJob(job) {
  go(job.kind);
  watchJob(job.id, job.kind);
}

/* ================================================================== projects */
async function loadProjects() {
  const res = await api.get('/api/projects');
  state.projects = res.data;
  const list = $('projectsList');
  if (!res.data.length) {
    mount(list, [emptyState('No projects', 'Group your generations by creating one.')]);
    return;
  }

  const rows = res.data.map((project) => el('div', { class: 'row-item' }, [
    el('div', { class: 'grow' }, [
      el('div', { class: 'title', text: project.name }),
      el('div', { class: 'sub' }, [
        project.job_count + ' job(s) · ' + project.asset_count + ' asset(s) · updated ' + fmtWhen(project.updated_at),
      ]),
      project.description ? el('div', { class: 'sub', text: project.description }) : null,
    ]),
    el('div', { class: 'actions' }, [
      el('button', { class: 'btn sm', text: 'Assets', onclick: () => openProjectAssets(project) }),
      el('button', { class: 'btn sm', text: 'Rename', onclick: () => editProject(project) }),
      el('button', { class: 'btn sm danger', text: 'Delete', onclick: () => deleteProject(project) }),
    ]),
  ]));
  mount(list, rows);
}

async function openProjectAssets(project) {
  go('library');
  $('libFilter').value = '';
  await loadLibrary(project.id);
}

async function editProject(project) {
  const values = await formDialog(project ? 'Rename project' : 'New project', [
    { name: 'name', label: 'Name', value: project ? project.name : '', required: true },
    { name: 'description', label: 'Description', value: project ? project.description : '', multiline: true },
  ]);
  if (!values) return;
  try {
    if (project) await api.patch('/api/projects/' + project.id, values);
    else await api.post('/api/projects', values);
    toast(project ? 'Project updated' : 'Project created', 'ok');
    await loadProjects();
  } catch (error) { toast(error.message, 'bad'); }
}

async function deleteProject(project) {
  if (!confirm('Delete "' + project.name + '"? The jobs and assets are kept; only the grouping is removed.')) return;
  try {
    await api.del('/api/projects/' + project.id);
    toast('Project deleted — its work is still in the library', 'ok');
    await loadProjects();
  } catch (error) { toast(error.message, 'bad'); }
}

/* ================================================================== library */
async function loadLibrary(projectId) {
  const media = $('libFilter').value;
  const params = new URLSearchParams({ limit: '80' });
  if (media) params.set('media_type', media);
  if (projectId) params.set('project_id', projectId);
  const res = await api.get('/api/assets?' + params.toString());
  state.assets = res.assets;
  const grid = $('libGrid');

  if (!res.assets.length) {
    mount(grid, [emptyState('Nothing here yet',
      'Generated outputs land here automatically; you can also upload an image or a clip.')]);
    $('libMeta').textContent = '';
    return;
  }

  mount(grid, res.assets.map((asset) => {
    const url = '/api/assets/' + asset.id + '/raw';
    const isVideo = asset.media_type === 'video';
    return el('div', { class: 'media' }, [
      el('div', { class: 'overlay' }, [
        el('a', { class: 'btn', href: url, download: '', text: 'Download' }),
        el('button', { class: 'btn', text: 'Delete', onclick: () => deleteAsset(asset) }),
      ]),
      isVideo
        ? el('video', { class: 'thumb', src: url, controls: '', preload: 'metadata' })
        : el('a', { href: url, target: '_blank', rel: 'noopener' },
            [el('img', { class: 'thumb', src: url, alt: asset.name, loading: 'lazy' })]),
      el('div', { class: 'meta' }, [
        el('span', { class: 'name', title: asset.name, text: asset.name }),
        el('span', { class: 'size', text: fmtBytes(asset.bytes) }),
      ]),
    ]);
  }));
  $('libMeta').textContent = res.total + ' item(s)' +
    (media ? ' · ' + media + ' only' : '') + (projectId ? ' · one project' : '');
}

async function deleteAsset(asset) {
  if (!confirm('Delete "' + asset.name + '"? This removes the file.')) return;
  try {
    await api.del('/api/assets/' + asset.id);
    toast('Deleted', 'ok');
    await loadLibrary();
  } catch (error) { toast(error.message, 'bad'); }
}

async function uploadFile(file) {
  try {
    const base64 = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(new Error('could not read the file'));
      reader.readAsDataURL(file);
    });
    await api.post('/api/assets/upload', {
      filename: file.name, mime: file.type, data: base64,
    });
    toast('Uploaded ' + file.name, 'ok');
    await loadLibrary();
  } catch (error) {
    toast(error.message + (error.hint ? ' — ' + error.hint : ''), 'bad');
  }
}

/* ================================================================== prompts */
async function loadPrompts() {
  const res = await api.get('/api/prompts');
  state.prompts = res.data;
  const list = $('promptsList');
  if (!res.data.length) {
    mount(list, [emptyState('No saved prompts',
      'Save the ones you reuse; favourites are offered in the studios.')]);
    return;
  }
  mount(list, res.data.map((prompt) => el('div', { class: 'row-item' }, [
    el('button', {
      class: 'btn sm icon', title: prompt.favorite ? 'Unfavourite' : 'Favourite',
      text: prompt.favorite ? '★' : '☆',
      style: prompt.favorite ? 'color:#b8860b' : '',
      onclick: () => toggleFavorite(prompt),
    }),
    el('div', { class: 'grow' }, [
      el('div', { class: 'title', text: prompt.title || '(untitled)' }),
      el('div', { class: 'sub', text: prompt.prompt }),
    ]),
    el('div', { class: 'actions' }, [
      el('button', { class: 'btn sm', text: 'Use', onclick: () => usePrompt(prompt) }),
      el('button', { class: 'btn sm', text: 'Edit', onclick: () => editPrompt(prompt) }),
      el('button', { class: 'btn sm danger', text: 'Delete', onclick: () => deletePrompt(prompt) }),
    ]),
  ])));
}

function usePrompt(prompt) {
  const kind = state.view === 'video' ? 'video' : 'image';
  go(kind);
  $(PREFIX[kind] + 'Prompt').value = prompt.prompt;
  toast('Prompt loaded into the ' + kind + ' studio', 'ok');
}

async function toggleFavorite(prompt) {
  try {
    await api.patch('/api/prompts/' + prompt.id, { favorite: !prompt.favorite });
    await loadPrompts();
  } catch (error) { toast(error.message, 'bad'); }
}

async function editPrompt(prompt) {
  const values = await formDialog(prompt ? 'Edit prompt' : 'New prompt', [
    { name: 'title', label: 'Title', value: prompt ? prompt.title : '' },
    { name: 'prompt', label: 'Prompt', value: prompt ? prompt.prompt : '', multiline: true, required: true },
  ]);
  if (!values) return;
  try {
    if (prompt) await api.patch('/api/prompts/' + prompt.id, values);
    else await api.post('/api/prompts', values);
    toast(prompt ? 'Prompt updated' : 'Prompt saved', 'ok');
    await loadPrompts();
  } catch (error) { toast(error.message, 'bad'); }
}

async function deletePrompt(prompt) {
  if (!confirm('Delete this saved prompt?')) return;
  try {
    await api.del('/api/prompts/' + prompt.id);
    toast('Deleted', 'ok');
    await loadPrompts();
  } catch (error) { toast(error.message, 'bad'); }
}

/* ================================================================== presets */
async function loadPresets() {
  const res = await api.get('/api/presets');
  state.presets = res.data;
  const list = $('presetsList');
  if (!res.data.length) {
    mount(list, [emptyState('No presets',
      'A preset stores parameter values for one modality, so you can reapply them.')]);
    return;
  }
  mount(list, res.data.map((preset) => el('div', { class: 'row-item' }, [
    chip(preset.modality, 'accent'),
    el('div', { class: 'grow' }, [
      el('div', { class: 'title', text: preset.name }),
      el('div', { class: 'sub mono' },
        [Object.entries(preset.configuration || {}).map(([k, v]) => k + '=' + v).join('  ') || 'no settings']),
    ]),
    el('div', { class: 'actions' }, [
      el('button', { class: 'btn sm', text: 'Apply', onclick: () => applyPreset(preset) }),
      el('button', { class: 'btn sm', text: 'Edit', onclick: () => editPreset(preset) }),
      el('button', { class: 'btn sm danger', text: 'Delete', onclick: () => deletePreset(preset) }),
    ]),
  ])));
}

function applyPreset(preset) {
  const kind = preset.modality === 'video' ? 'video' : 'image';
  go(kind);
  const p = PREFIX[kind];
  // `go()` loads models asynchronously and re-renders the controls, which would wipe
  // values written now. Applying after that render has settled is what makes the
  // preset actually land — doing it immediately looked like it silently failed.
  const apply = () => {
    let applied = 0;
    let skipped = 0;
    for (const [name, value] of Object.entries(preset.configuration || {})) {
      const input = $(p + '-p-' + name);
      if (!input) { skipped += 1; continue; }
      input.value = String(value);
      // A select needs its change event so anything listening stays in step.
      input.dispatchEvent(new Event('change', { bubbles: true }));
      applied += 1;
    }
    toast('Applied ' + applied + ' setting(s)' +
      (skipped ? ', ' + skipped + ' not supported by this model' : ''),
      skipped ? 'warn' : 'ok');
  };
  setTimeout(apply, 350);
}

async function editPreset(preset) {
  const fields = [
    { name: 'name', label: 'Name', value: preset ? preset.name : '', required: true },
    { name: 'modality', label: 'Modality', value: preset ? preset.modality : 'image',
      choices: ['image', 'video'] },
    { name: 'configuration', label: 'Settings (JSON)', value: preset ? JSON.stringify(preset.configuration || {}, null, 1) : '{\n "aspect_ratio": "16:9"\n}',
      multiline: true, json: true },
  ];
  const values = await formDialog(preset ? 'Edit preset' : 'New preset', fields);
  if (!values) return;
  try {
    if (preset) await api.patch('/api/presets/' + preset.id, values);
    else await api.post('/api/presets', values);
    toast(preset ? 'Preset updated' : 'Preset created', 'ok');
    await loadPresets();
  } catch (error) { toast(error.message, 'bad'); }
}

async function deletePreset(preset) {
  if (!confirm('Delete preset "' + preset.name + '"?')) return;
  try {
    await api.del('/api/presets/' + preset.id);
    toast('Deleted', 'ok');
    await loadPresets();
  } catch (error) { toast(error.message, 'bad'); }
}

/* ================================================================== characters */
async function loadCharacters() {
  const res = await api.get('/api/characters');
  const list = $('charactersList');
  if (!res.data.length) {
    mount(list, [emptyState('No characters',
      'A character holds reference assets for a recurring subject.')]);
    return;
  }
  mount(list, res.data.map((character) => el('div', { class: 'row-item' }, [
    el('div', { class: 'grow' }, [
      el('div', { class: 'title', text: character.name }),
      el('div', { class: 'sub', text: character.description || 'no description' }),
      el('div', { class: 'chips mt3' },
        [(character.references || []).length + ' reference(s)'].map((t) => chip(t))),
    ]),
    el('div', { class: 'actions' }, [
      el('button', { class: 'btn sm', text: 'Edit', onclick: () => editCharacter(character) }),
      el('button', { class: 'btn sm danger', text: 'Delete', onclick: () => deleteCharacter(character) }),
    ]),
  ])));
}

async function editCharacter(character) {
  const refs = character && character.references && character.references.length
    ? character.references.map((r) => r.asset_id).join(', ') : '';
  const values = await formDialog(character ? 'Edit character' : 'New character', [
    { name: 'name', label: 'Name', value: character ? character.name : '', required: true },
    { name: 'description', label: 'Description', value: character ? character.description : '', multiline: true },
    { name: 'reference_asset_ids', label: 'Reference asset ids (comma separated)',
      value: refs, hint: 'Copy an id from the Library; only assets you own are accepted.' },
  ]);
  if (!values) return;
  const payload = { name: values.name, description: values.description };
  payload.references = (values.reference_asset_ids || '')
    .split(',').map((s) => s.trim()).filter(Boolean)
    .map((id) => ({ asset_id: id, role: 'face' }));
  try {
    if (character) await api.patch('/api/characters/' + character.id, payload);
    else await api.post('/api/characters', payload);
    toast(character ? 'Character updated' : 'Character created', 'ok');
    await loadCharacters();
  } catch (error) { toast(error.message, 'bad'); }
}

async function deleteCharacter(character) {
  if (!confirm('Delete character "' + character.name + '"?')) return;
  try {
    await api.del('/api/characters/' + character.id);
    toast('Deleted', 'ok');
    await loadCharacters();
  } catch (error) { toast(error.message, 'bad'); }
}

/* ================================================================== models */
// Credentials are environment variables now, so this view reads and never writes.
// It answers "which models do I have?" — a model appears here only because its key,
// name and URL were set in the deployment's environment.
async function loadModels() {
  const list = $('modelList');
  let models = [];
  try {
    const res = await api.get('/api/models?include_disabled=true');
    models = res.data || [];
  } catch (error) {
    mount(list, [emptyState('Could not read the models', error.message)]);
    return;
  }

  if (!models.length) {
    mount(list, [emptyState('No models configured',
      'Set MODEL_API_KEY, MODEL and MODEL_API_URL for the models you want in this ' +
      "deployment's environment, then reload.")]);
    return;
  }

  mount(list, models.map((model) => el('div', { class: 'row-item' }, [
    el('div', { class: 'grow' }, [
      el('div', { class: 'title', text: model.display_name || model.model_id }),
      el('div', { class: 'sub mono', text: model.api_url || '(no endpoint)' }),
      el('div', { class: 'sub' }, [
        chip(model.modality),
        ' ' + (model.capabilities || []).join(', '),
        model.env_prefix ? ' · ' + model.env_prefix : '',
      ]),
    ]),
    el('span', { class: 'chip ' + (model.enabled ? 'ok' : 'bad'),
                 text: model.enabled ? 'enabled' : 'disabled' }),
  ])));
}

async function syncModels() {
  try {
    const res = await api.post('/api/sync', {});
    const r = res.data;
    toast(r.configured + ' model(s) configured, ' + r.disabled + ' disabled', 'ok');
    await loadModels();
    await loadHealth();
  } catch (error) { toast(error.message, 'bad'); }
}

/* ================================================================== usage */
async function loadUsage() {
  const days = $('usageDays').value;
  const target = $('usageBody');
  try {
    const res = await api.get('/api/analytics?days=' + days);
    const data = res.data;
    const parts = [];

    const counts = data.jobs_by_status || {};
    const summary = el('div', { class: 'chips', style: 'margin-bottom:14px' });
    const order = ['succeeded', 'running', 'queued', 'failed', 'cancelled'];
    for (const status of order) {
      if (counts[status]) summary.appendChild(chip(status + ' ' + counts[status],
        status === 'succeeded' ? 'ok' : status === 'failed' ? 'bad' : ''));
    }
    if (!summary.children.length) summary.appendChild(chip('no jobs in this window'));
    parts.push(summary);

    const costCard = el('div', { class: 'card' }, [
      el('header', {}, [el('h2', { text: 'Cost' })]),
      el('div', { class: 'body' }, [
        data.estimated_cost === null
          ? el('div', { class: 'banner warn' }, [
              el('div', { class: 'msg' }, [
                el('strong', { text: 'No cost is shown. ' }), data.cost_note,
              ]),
            ])
          : el('div', { class: 'banner warn' }, [
              el('div', { class: 'msg', text:
                'Estimated ' + data.estimated_cost + ' ' + data.currency + ' across ' +
                data.cost_known_for + ' of ' + data.cost_records + ' record(s). ' + data.cost_note }),
            ]),
        data.avg_latency_seconds !== null
          ? el('div', { class: 'small muted', text: 'Average job time: ' + data.avg_latency_seconds + ' s' })
          : null,
      ]),
    ]);
    parts.push(costCard);

    if ((data.by_model || []).length) {
      const rows = data.by_model.map((row) => el('div', { class: 'row-item' }, [
        el('div', { class: 'grow' }, [
          el('div', { class: 'title mono', text: row.model_id }),
          el('div', { class: 'sub', text: row.jobs + ' job(s) · ' + row.quantity + ' output(s)' }),
        ]),
        row.estimated_cost === null
          ? chip('cost unknown', 'warn')
          : chip(row.estimated_cost + ' ' + data.currency, 'ok'),
      ]));
      parts.push(el('div', { class: 'card mt4' }, [
        el('header', {}, [el('h2', { text: 'By model' })]),
        el('div', { class: 'rows' }, rows),
      ]));
    }

    mount(target, parts);
  } catch (error) {
    showError(target, error);
  }
}

/* ================================================================== assistant */
async function openAssistant() {
  const dialog = $('dlgAssistant');
  if (!dialog.open) dialog.showModal();
  mount($('assistantThread'), []);
  const status = $('assistantState');
  try {
    const res = await api.get('/api/assistant');
    if (res.data.available) {
      mount(status, [banner('Using ' + res.data.model + '.', 'info')]);
      $('assistantSend').disabled = false;
    } else {
      mount(status, [banner(res.data.reason, 'warn')]);
      $('assistantSend').disabled = true;
    }
  } catch (error) {
    showError(status, error);
    $('assistantSend').disabled = true;
  }
}

async function askAssistant() {
  const input = $('assistantInput');
  const message = input.value.trim();
  if (!message) return;
  const thread = $('assistantThread');
  thread.appendChild(el('div', { class: 'banner info' }, [el('div', { class: 'msg', text: message })]));
  input.value = '';
  $('assistantSend').disabled = true;
  try {
    const res = await api.post('/api/assistant', { message });
    if (res.data.source === 'model') {
      thread.appendChild(el('div', { class: 'banner ok' }, [
        el('div', { class: 'msg', text: res.data.reply }),
      ]));
    } else {
      thread.appendChild(el('div', { class: 'banner warn' }, [
        el('div', { class: 'msg', text: res.data.message }),
      ]));
    }
  } catch (error) {
    thread.appendChild(errorBanner(error));
  } finally {
    $('assistantSend').disabled = false;
    thread.scrollTop = thread.scrollHeight;
  }
}

/* ================================================================== generic dialog */
const dlgState = { resolve: null, fields: [], body: null };

function readDialog() {
  const values = {};
  for (const field of dlgState.fields) {
    const control = dlgState.body.querySelector('[data-field="' + field.name + '"]');
    let value = control ? control.value.trim() : '';
    if (field.json && value) {
      try { value = JSON.parse(value); }
      catch (e) { return { error: field.label + ' is not valid JSON: ' + e.message }; }
    }
    if (field.required && (value === '' || value === null)) {
      return { error: field.label + ' is required.' };
    }
    values[field.name] = value;
  }
  return { values };
}

function settleDialog(values) {
  const resolve = dlgState.resolve;
  dlgState.resolve = null;
  const dialog = $('dlgForm');
  if (dialog.open) dialog.close();
  if (resolve) resolve(values);
}

/**
 * A small modal form. Resolves with the entered values, or null if cancelled.
 *
 * The buttons are wired directly rather than relying on the form's implicit
 * `method="dialog"` submit and the `close` event. That indirection is shorter but was
 * silently unreliable: the dialog closed and nothing resolved, so a click looked like
 * a no-op. Explicit handlers make the outcome observable.
 */
function formDialog(title, fields) {
  const dialog = $('dlgForm');
  $('dlgTitle').textContent = title;
  const body = $('dlgFields');
  mount(body, []);

  for (const field of fields) {
    let control;
    const id = 'dlgf-' + field.name;
    if (field.choices) {
      control = el('select', { id });
      for (const choice of field.choices) {
        control.appendChild(el('option', { value: choice, text: choice }));
      }
      if (field.value) control.value = field.value;
    } else if (field.multiline) {
      control = el('textarea', { id });
      control.value = field.value || '';
    } else {
      control = el('input', { id, type: field.password ? 'password' : 'text' });
      if (field.password) control.setAttribute('autocomplete', 'off');
      control.value = field.value || '';
    }
    control.dataset.field = field.name;
    // Enter submits (except in a textarea, where it is a newline).
    if (!field.multiline) {
      control.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); submitDialog(); }
      });
    }
    body.appendChild(el('label', { class: 'field' }, [
      el('span', { class: 'lbl', text: field.label }),
      control,
      field.hint ? el('span', { class: 'hint', text: field.hint }) : null,
    ]));
  }

  dlgState.fields = fields;
  dlgState.body = body;
  dialog.returnValue = '';
  dialog.showModal();
  const first = body.querySelector('input, textarea, select');
  if (first) { first.focus(); if (first.select) first.select(); }

  return new Promise((resolve) => { dlgState.resolve = resolve; });
}

function submitDialog() {
  const result = readDialog();
  if (result.error) { toast(result.error, 'bad'); return; }
  settleDialog(result.values);
}

/* ================================================================== wiring */
document.addEventListener('click', (event) => {
  const nav = event.target.closest('.navitem');
  if (nav) { go(nav.dataset.view); return; }
  const closer = event.target.closest('[data-close]');
  if (closer) { closer.closest('dialog').close(); }
});

$('dlgSave').addEventListener('click', (event) => {
  event.preventDefault();
  submitDialog();
});
document.querySelector('#dlgForm button[value="cancel"]').addEventListener('click', (event) => {
  event.preventDefault();
  settleDialog(null);
});
$('dlgForm').addEventListener('keydown', (event) => {
  if (event.key === 'Escape') { event.preventDefault(); settleDialog(null); }
});

$('modelRefresh').addEventListener('click', () => loadModels().catch((e) => toast(e.message, 'bad')));
$('modelSync').addEventListener('click', () => syncModels());

for (const kind of ['image', 'video']) {
  const p = PREFIX[kind];
  $(p + 'Model').addEventListener('change', () => renderStudioFields(kind));
  $(p + 'Generate').addEventListener('click', () => submitJob(kind));
}
$('imgPrompt').addEventListener('input', () => {
  $('imgCharCount').textContent = String($('imgPrompt').value.length);
});
$('imgRandom').addEventListener('click', () => {
  const ideas = [
    'a lighthouse at dawn, wide shot, warm light on wet stone',
    'a fox in fresh snow, low angle, cold blue light',
    'an empty tram at night, fluorescent light, one long exposure',
    'a paper kite above a rooftop, harsh midday sun, wide shot',
    'a diver entering dark water, side light, slow motion',
  ];
  $('imgPrompt').value = ideas[Math.floor(Math.random() * ideas.length)];
  $('imgCharCount').textContent = String($('imgPrompt').value.length);
});

$('imgPromptBtn').addEventListener('click', () => editPrompt(null));
$('vidPromptBtn').addEventListener('click', () => editPrompt(null));
$('imgPresetBtn').addEventListener('click', () => { go('presets'); });
$('vidPresetBtn').addEventListener('click', () => { go('presets'); });

$('jobsFilter').addEventListener('change', () => loadJobs().catch((e) => toast(e.message, 'bad')));
$('jobsRefresh').addEventListener('click', () => loadJobs().catch((e) => toast(e.message, 'bad')));

$('projectNew').addEventListener('click', () => editProject(null));
$('promptNew').addEventListener('click', () => editPrompt(null));
$('presetNew').addEventListener('click', () => editPreset(null));
$('characterNew').addEventListener('click', () => editCharacter(null));

$('libUploadBtn').addEventListener('click', () => $('libFile').click());
$('libFile').addEventListener('change', (event) => {
  const file = event.target.files && event.target.files[0];
  if (file) uploadFile(file);
  event.target.value = '';
});
$('libFilter').addEventListener('change', () => loadLibrary().catch((e) => toast(e.message, 'bad')));

$('usageDays').addEventListener('change', () => loadUsage());
$('usageRefresh').addEventListener('click', () => loadUsage());

$('assistantBtn').addEventListener('click', openAssistant);
$('assistantSend').addEventListener('click', askAssistant);
$('assistantInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) askAssistant();
});

// Keyboard shortcuts: g then i/v/j/l, and ? for nothing yet — kept minimal on purpose.
document.addEventListener('keydown', (event) => {
  if (event.target.matches('input, textarea, select')) return;
  if (event.key === 'Escape') return;
});

window.addEventListener('hashchange', () => {
  const view = location.hash.replace('#', '');
  if (view && view !== state.view) go(view);
});

/* ================================================================== boot */
(async function boot() {
  await loadHealth();
  const startView = location.hash.replace('#', '') || 'image';
  go(VIEWS.includes(startView) ? startView : 'image');
  loadModels().catch(() => {});
  setInterval(loadHealth, 15000);
  setInterval(() => {
    if (state.view === 'jobs') loadJobs().catch(() => {});
  }, 5000);
})();
