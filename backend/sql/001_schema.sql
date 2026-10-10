-- The Validation Machine: system of record (TigerData / Postgres + TimescaleDB + pgvector)
create extension if not exists timescaledb;
create extension if not exists vector;
create extension if not exists pgcrypto;

create table if not exists users (
    id            text primary key,
    display_name  text not null,
    role          text not null check (role in ('author','qa','system_owner','tester','agent','runner')),
    is_human      boolean not null default true,
    password_hash text
);

create table if not exists systems (
    id                 uuid primary key default gen_random_uuid(),
    name               text not null,
    repo_path          text not null,
    intended_use       text not null,
    sop                text,
    gxp_impact         text,
    gamp_category      int,
    category_rationale text,
    status             text not null default 'draft'
                       check (status in ('draft','in_validation','validated','change_pending')),
    created_at         timestamptz not null default now()
);

-- Every deliverable (plan, spec, risk assessment, protocol, summary report, change record)
-- is a versioned artifact. Approved content is frozen; a change means a new version.
create table if not exists artifacts (
    id           uuid primary key default gen_random_uuid(),
    system_id    uuid not null references systems(id),
    kind         text not null check (kind in ('VP','URS','FS','DS','RA','IQ','OQ','PQ','VSR','CHANGE')),
    version      int  not null default 1,
    title        text not null,
    content      text not null,
    content_hash text not null,
    author_id    text not null references users(id),
    status       text not null default 'draft' check (status in ('draft','approved','superseded')),
    created_at   timestamptz not null default now(),
    unique (system_id, kind, version)
);

create table if not exists requirements (
    id          uuid primary key default gen_random_uuid(),
    system_id   uuid not null references systems(id),
    artifact_id uuid not null references artifacts(id),
    req_id      text not null,
    level       text not null check (level in ('URS','FS','DS')),
    statement   text not null,
    source_ref  text,
    parent_id   uuid references requirements(id),
    unique (artifact_id, req_id)
);

create table if not exists risks (
    id             uuid primary key default gen_random_uuid(),
    system_id      uuid not null references systems(id),
    artifact_id    uuid not null references artifacts(id),
    requirement_id uuid not null references requirements(id),
    severity       text not null check (severity in ('low','medium','high')),
    probability    text not null check (probability in ('low','medium','high')),
    detectability  text not null check (detectability in ('low','medium','high')),
    rating         text not null check (rating in ('low','medium','high')),
    rationale      text not null,
    test_depth     text not null,
    citation       text
);

create table if not exists test_cases (
    id          uuid primary key default gen_random_uuid(),
    system_id   uuid not null references systems(id),
    artifact_id uuid not null references artifacts(id),
    test_id     text not null,
    title       text not null,
    steps       text not null,
    expected    text not null,
    automation  text not null default 'pytest' check (automation in ('pytest','manual')),
    code        text,
    unique (artifact_id, test_id)
);

create table if not exists test_requirements (
    test_case_id   uuid not null references test_cases(id),
    requirement_id uuid not null references requirements(id),
    primary key (test_case_id, requirement_id)
);

create table if not exists executions (
    id                 uuid primary key default gen_random_uuid(),
    system_id          uuid not null references systems(id),
    artifact_id        uuid not null references artifacts(id),
    runner_id          text not null references users(id),
    target_fingerprint text not null,
    environment        jsonb not null,
    retest_of          uuid references executions(id),
    started_at         timestamptz not null default now(),
    finished_at        timestamptz
);

create table if not exists evidence (
    id           uuid primary key default gen_random_uuid(),
    execution_id uuid not null references executions(id),
    test_case_id uuid not null references test_cases(id),
    kind         text not null,
    path         text not null,
    sha256       text not null,
    captured_by  text not null references users(id),
    captured_at  timestamptz not null default now()
);

create table if not exists step_results (
    id           uuid primary key default gen_random_uuid(),
    execution_id uuid not null references executions(id),
    test_case_id uuid not null references test_cases(id),
    outcome      text not null check (outcome in ('pass','fail','error')),
    actual       text not null,
    evidence_id  uuid not null references evidence(id),
    recorded_by  text not null references users(id),
    recorded_at  timestamptz not null default now()
);

create table if not exists deviations (
    id                  uuid primary key default gen_random_uuid(),
    system_id           uuid not null references systems(id),
    step_result_id      uuid not null references step_results(id),
    test_case_id        uuid not null references test_cases(id),
    description         text not null,
    investigation       text,
    proposed_fix        text,
    resolution          text,
    status              text not null default 'open' check (status in ('open','resolved')),
    retest_execution_id uuid references executions(id),
    opened_at           timestamptz not null default now(),
    resolved_by         text references users(id),
    resolved_at         timestamptz
);

create table if not exists signatures (
    id           uuid primary key default gen_random_uuid(),
    artifact_id  uuid not null references artifacts(id),
    signer_id    text not null references users(id),
    signer_name  text not null,
    meaning      text not null check (meaning in ('reviewed','approved')),
    content_hash text not null,
    signed_at    timestamptz not null default now()
);

create table if not exists findings (
    id                 uuid primary key default gen_random_uuid(),
    system_id          uuid not null references systems(id),
    severity           text not null check (severity in ('critical','major','minor')),
    document           text not null,
    location           text,
    finding            text not null,
    recommended_action text not null,
    found_by           text not null references users(id),
    created_at         timestamptz not null default now()
);

create table if not exists controls (
    id        uuid primary key default gen_random_uuid(),
    system_id uuid not null references systems(id),
    control   text not null,
    rule      text not null,
    evidence  text,
    status    text not null check (status in ('met','partial','gap','not_applicable')),
    rationale text not null
);

create table if not exists changes (
    id               uuid primary key default gen_random_uuid(),
    system_id        uuid not null references systems(id),
    kind             text not null check (kind in ('code_diff','config','vendor_release')),
    description      text not null,
    diff             text not null,
    impact           jsonb,
    rationale        text,
    artifact_id      uuid references artifacts(id),
    created_at       timestamptz not null default now()
);

-- The tool's own audit trail: append-only, hash-chained, time-partitioned.
create table if not exists audit_events (
    recorded_at timestamptz not null default clock_timestamp(),
    id          bigint generated always as identity,
    actor       text not null,
    action      text not null,
    entity_type text not null,
    entity_id   text not null,
    detail      jsonb not null default '{}',
    prev_hash   text,
    hash        text,
    primary key (recorded_at, id)
);
select create_hypertable('audit_events', 'recorded_at', if_not_exists => true);

-- Audit trail exported from the *target* system, reviewed for anomalies (Problem 5).
create table if not exists target_audit_events (
    recorded_at timestamptz not null,
    event_time  timestamptz not null,
    system_id   uuid not null,
    actor       text not null,
    action      text not null,
    record_id   text not null,
    detail      jsonb not null default '{}'
);
select create_hypertable('target_audit_events', 'recorded_at', if_not_exists => true);

create table if not exists reg_chunks (
    id        bigint generated always as identity primary key,
    source    text not null,
    section   text not null,
    body      text not null,
    embedding vector(768)
);
