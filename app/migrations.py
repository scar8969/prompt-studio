"""Schema, in portable SQL (spec §16).

One set of statements runs on both SQLite (local) and Postgres (Vercel). The only
dialect differences are marked with `{{SERIAL}}`, `TIMESTAMPTZ` and `DEFAULT NOW()`,
which `app/db.py` adapts — so the schema cannot drift between the two.

Append only. A new column or table is a new entry; nothing is rewritten, because a
deployed database has already run the earlier ones.
"""

SCRIPTS = [
    # 1 — connections, models, capabilities, projects
    """
CREATE TABLE connections (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    secret_encrypted TEXT NOT NULL DEFAULT '',
    key_hint TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'openai-compatible',
    status TEXT NOT NULL DEFAULT 'untested',
    last_tested_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    uses_platform_credentials INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE models (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL DEFAULT 'local',
    model_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    modality TEXT NOT NULL DEFAULT 'image',
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (connection_id, model_id)
);
CREATE TABLE model_capabilities (
    model_pk TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    capability TEXT NOT NULL,
    PRIMARY KEY (model_pk, capability)
);
CREATE TABLE model_parameters (
    model_pk TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    default_value TEXT,
    minimum DOUBLE PRECISION,
    maximum DOUBLE PRECISION,
    choices_json TEXT NOT NULL DEFAULT '[]',
    required INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (model_pk, name)
);
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_connections_owner ON connections(owner_id);
CREATE INDEX idx_models_connection ON models(connection_id);
CREATE INDEX idx_models_modality ON models(modality, enabled);
CREATE INDEX idx_projects_owner ON projects(owner_id);
""",
    # 2 — media, jobs, outputs
    """
CREATE TABLE assets (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    mime TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    width INTEGER,
    height INTEGER,
    duration_ms INTEGER,
    path TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'upload',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE generation_jobs (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    connection_id TEXT REFERENCES connections(id) ON DELETE SET NULL,
    model_pk TEXT REFERENCES models(id) ON DELETE SET NULL,
    kind TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    negative_prompt TEXT NOT NULL DEFAULT '',
    params_json TEXT NOT NULL DEFAULT '{}',
    reference_asset_ids TEXT NOT NULL DEFAULT '[]',
    request_fingerprint TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    provider_job_id TEXT NOT NULL DEFAULT '',
    submitted_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE outputs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
    owner_id TEXT NOT NULL DEFAULT 'local',
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    asset_id TEXT REFERENCES assets(id) ON DELETE SET NULL,
    remote_url TEXT NOT NULL DEFAULT '',
    mime TEXT NOT NULL DEFAULT '',
    width INTEGER,
    height INTEGER,
    duration_ms INTEGER,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_assets_owner ON assets(owner_id, created_at);
CREATE INDEX idx_assets_project ON assets(project_id);
CREATE INDEX idx_jobs_status ON generation_jobs(status, created_at);
CREATE INDEX idx_jobs_owner ON generation_jobs(owner_id, created_at);
CREATE INDEX idx_jobs_fingerprint ON generation_jobs(request_fingerprint, created_at);
CREATE INDEX idx_outputs_job ON outputs(job_id, position);
""",
    # 3 — the creative workspace
    """
CREATE TABLE presets (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    modality TEXT NOT NULL DEFAULT 'image',
    configuration_json TEXT NOT NULL DEFAULT '{}',
    visibility TEXT NOT NULL DEFAULT 'private',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE saved_prompts (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    favorite INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE characters (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE character_references (
    id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'face',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE usage_records (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL DEFAULT 'local',
    workspace_id TEXT NOT NULL DEFAULT 'default',
    generation_job_id TEXT REFERENCES generation_jobs(id) ON DELETE SET NULL,
    provider TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    usage_type TEXT NOT NULL DEFAULT 'generation',
    quantity DOUBLE PRECISION NOT NULL DEFAULT 1,
    estimated_cost DOUBLE PRECISION,
    currency TEXT NOT NULL DEFAULT 'USD',
    cost_source TEXT NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE job_events (
    id {{SERIAL}} PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES generation_jobs(id) ON DELETE CASCADE,
    from_status TEXT NOT NULL DEFAULT '',
    to_status TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_presets_owner ON presets(owner_id, modality);
CREATE INDEX idx_saved_prompts_owner ON saved_prompts(owner_id, updated_at);
CREATE INDEX idx_characters_owner ON characters(owner_id);
CREATE INDEX idx_usage_job ON usage_records(generation_job_id);
CREATE INDEX idx_usage_owner ON usage_records(owner_id, created_at);
CREATE INDEX idx_job_events_job ON job_events(job_id, id);
""",
    # 4 - job leases, so work can be claimed where there are no background threads
    """
ALTER TABLE generation_jobs ADD COLUMN lease_token TEXT NOT NULL DEFAULT '';
ALTER TABLE generation_jobs ADD COLUMN lease_until TIMESTAMPTZ;
CREATE INDEX idx_jobs_lease ON generation_jobs(status, lease_until);
""",
]
