-- Compliance behaviours enforced by the database, so no API path or agent can bypass them.
-- Idempotent: safe to re-run.

-- ---------------------------------------------------------------------------
-- Roles. The API holds one connection and does SET LOCAL ROLE per transaction:
--   vm_agent  : LLM agents. May draft documents. Cannot write results, evidence or signatures.
--   vm_runner : deterministic test runner. The only role that can write results and evidence.
--   vm_app    : actions taken by an authenticated human (sign, resolve, register).
-- ---------------------------------------------------------------------------
do $$
declare r text;
begin
    foreach r in array array['vm_agent','vm_runner','vm_app'] loop
        if not exists (select 1 from pg_roles where rolname = r) then
            execute format('create role %I nologin', r);
        end if;
        execute format('grant %I to current_user', r);
    end loop;
end $$;

grant usage on schema public to vm_agent, vm_runner, vm_app;
grant select on all tables in schema public to vm_agent, vm_runner, vm_app;
grant usage on all sequences in schema public to vm_agent, vm_runner, vm_app;
revoke all on audit_events from vm_agent, vm_runner, vm_app;
grant select on audit_events to vm_agent, vm_runner, vm_app;

grant insert on artifacts, requirements, risks, test_cases, test_requirements, findings, controls to vm_agent;
grant delete on controls, findings to vm_agent;
grant update (gxp_impact, gamp_category, category_rationale) on systems to vm_agent;
grant update (investigation, proposed_fix) on deviations to vm_agent;
grant update (impact, rationale, artifact_id) on changes to vm_agent;

grant insert on executions, evidence, step_results, deviations to vm_runner;
grant update (finished_at) on executions to vm_runner;

grant insert on systems, signatures, changes, target_audit_events to vm_app;
grant update (status) on systems to vm_app;
grant update (resolution, status, retest_execution_id, resolved_by, resolved_at) on deviations to vm_app;

-- ---------------------------------------------------------------------------
-- Audit trail: append-only and hash-chained.
-- ---------------------------------------------------------------------------
create or replace function vm_audit_hash(prev text, ts timestamptz, actor text, action text,
                                         entity_type text, entity_id text, detail jsonb)
returns text language sql immutable as $$
    select encode(digest(coalesce(prev, '') || '|' || extract(epoch from ts)::text || '|' || actor || '|' ||
                         action || '|' || entity_type || '|' || entity_id || '|' || detail::text, 'sha256'), 'hex')
$$;

create or replace function vm_audit_chain() returns trigger language plpgsql as $$
begin
    perform pg_advisory_xact_lock(727001);
    new.recorded_at := clock_timestamp();
    select hash into new.prev_hash from audit_events order by recorded_at desc, id desc limit 1;
    new.hash := vm_audit_hash(new.prev_hash, new.recorded_at, new.actor, new.action,
                              new.entity_type, new.entity_id, new.detail);
    return new;
end $$;

drop trigger if exists audit_chain on audit_events;
create trigger audit_chain before insert on audit_events
    for each row execute function vm_audit_chain();

create or replace function vm_actor() returns text language sql stable as $$
    select coalesce(nullif(current_setting('vm.actor', true), ''), 'system')
$$;

-- The only way to write to the audit trail.
create or replace function vm_audit(p_action text, p_entity_type text, p_entity_id text, p_detail jsonb default '{}')
returns void language sql security definer set search_path = public as $$
    insert into audit_events (actor, action, entity_type, entity_id, detail)
    values (vm_actor(), p_action, p_entity_type, p_entity_id, coalesce(p_detail, '{}'))
$$;
grant execute on function vm_audit(text, text, text, jsonb) to vm_agent, vm_runner, vm_app;

create or replace function vm_audit_row() returns trigger language plpgsql security definer
set search_path = public as $$
begin
    perform vm_audit(lower(tg_op), tg_table_name, new.id::text,
                     to_jsonb(new) - 'content' - 'code' - 'diff' - 'sop' - 'password_hash' - 'steps');
    return new;
end $$;

do $$
declare t text;
begin
    foreach t in array array['systems','artifacts','requirements','risks','test_cases','executions','evidence',
                             'step_results','deviations','signatures','findings','controls','changes'] loop
        execute format('drop trigger if exists audit_row on %I', t);
        execute format('create trigger audit_row after insert or update on %I
                        for each row execute function vm_audit_row()', t);
    end loop;
end $$;

create or replace function vm_verify_audit_chain()
returns table (ok boolean, checked bigint, first_bad_id bigint) language plpgsql as $$
declare
    r record;
    prev text := null;
    n bigint := 0;
begin
    for r in select * from audit_events order by recorded_at, id loop
        n := n + 1;
        if r.prev_hash is distinct from prev
           or r.hash is distinct from vm_audit_hash(r.prev_hash, r.recorded_at, r.actor, r.action,
                                                    r.entity_type, r.entity_id, r.detail) then
            return query select false, n, r.id;
            return;
        end if;
        prev := r.hash;
    end loop;
    return query select true, n, null::bigint;
end $$;

-- ---------------------------------------------------------------------------
-- Append-only records: evidence, results, signatures and the audit trail.
-- ---------------------------------------------------------------------------
create or replace function vm_forbid_mutation() returns trigger language plpgsql as $$
begin
    raise exception '% on % is not permitted: this record is append-only (ALCOA+)', tg_op, tg_table_name
        using errcode = 'P0001';
end $$;

do $$
declare t text;
begin
    foreach t in array array['audit_events','evidence','step_results','signatures'] loop
        execute format('drop trigger if exists forbid_mutation on %I', t);
        execute format('create trigger forbid_mutation before update or delete on %I
                        for each row execute function vm_forbid_mutation()', t);
    end loop;
end $$;

-- ---------------------------------------------------------------------------
-- Approval: an artifact is approved only when the required human signatures exist
-- on its exact content. VP and VSR need QA and the system owner; everything else needs QA.
-- ---------------------------------------------------------------------------
create or replace function vm_is_approvable(p_artifact uuid) returns boolean language sql stable as $$
    select exists (
               select 1 from signatures s join users u on u.id = s.signer_id join artifacts a on a.id = s.artifact_id
               where s.artifact_id = p_artifact and s.meaning = 'approved' and u.role = 'qa'
                 and s.content_hash = a.content_hash)
       and (
               (select kind from artifacts where id = p_artifact) not in ('VP','VSR')
            or exists (
               select 1 from signatures s join users u on u.id = s.signer_id join artifacts a on a.id = s.artifact_id
               where s.artifact_id = p_artifact and s.meaning = 'approved' and u.role = 'system_owner'
                 and s.content_hash = a.content_hash))
$$;

create or replace function vm_artifact_guard() returns trigger language plpgsql as $$
begin
    if old.status <> 'draft' and (new.content, new.content_hash, new.title, new.author_id, new.kind, new.version)
                 is distinct from (old.content, old.content_hash, old.title, old.author_id, old.kind, old.version) then
        raise exception 'artifact % is % and its content is frozen; create a new version', old.id, old.status;
    end if;
    if new.status = 'approved' and old.status <> 'approved' and not vm_is_approvable(new.id) then
        raise exception 'artifact % cannot be approved: required e-signatures are missing', new.id;
    end if;
    if old.status = 'approved' and new.status = 'draft' then
        raise exception 'an approved artifact cannot return to draft; create a new version';
    end if;
    return new;
end $$;

drop trigger if exists artifact_guard on artifacts;
create trigger artifact_guard before update on artifacts
    for each row execute function vm_artifact_guard();

create or replace function vm_artifact_insert_guard() returns trigger language plpgsql as $$
begin
    if new.status <> 'draft' then
        raise exception 'artifacts must be created as draft';
    end if;
    new.created_at := clock_timestamp();
    return new;
end $$;

drop trigger if exists artifact_insert_guard on artifacts;
create trigger artifact_insert_guard before insert on artifacts
    for each row execute function vm_artifact_insert_guard();

create or replace function vm_signature_guard() returns trigger language plpgsql as $$
declare
    a artifacts%rowtype;
    u users%rowtype;
begin
    select * into a from artifacts where id = new.artifact_id;
    select * into u from users where id = new.signer_id;
    if not u.is_human then
        raise exception 'only a human can sign: % is not a human identity', u.id;
    end if;
    if u.id = a.author_id then
        raise exception 'segregation of duties: % authored this artifact and cannot sign it', u.id;
    end if;
    if a.status <> 'draft' then
        raise exception 'artifact % is % and can no longer be signed', a.id, a.status;
    end if;
    if new.content_hash <> a.content_hash then
        raise exception 'signature does not match the current content of the artifact';
    end if;
    if new.meaning = 'approved' and u.role not in ('qa','system_owner') then
        raise exception 'role % cannot approve; approval requires QA or the system owner', u.role;
    end if;
    if exists (select 1 from signatures where artifact_id = new.artifact_id and signer_id = new.signer_id
                                           and meaning = new.meaning) then
        raise exception '% has already signed this artifact as %', u.id, new.meaning;
    end if;
    new.signer_name := u.display_name;
    new.signed_at := clock_timestamp();   -- signatures cannot be back-dated
    return new;
end $$;

drop trigger if exists signature_guard on signatures;
create trigger signature_guard before insert on signatures
    for each row execute function vm_signature_guard();

create or replace function vm_signature_apply() returns trigger language plpgsql security definer
set search_path = public as $$
declare a artifacts%rowtype;
begin
    if vm_is_approvable(new.artifact_id) then
        select * into a from artifacts where id = new.artifact_id;
        update artifacts set status = 'superseded'
         where system_id = a.system_id and kind = a.kind and kind <> 'CHANGE' and status = 'approved' and id <> a.id;
        update artifacts set status = 'approved' where id = a.id;
    end if;
    return new;
end $$;

drop trigger if exists signature_apply on signatures;
create trigger signature_apply after insert on signatures
    for each row execute function vm_signature_apply();

-- ---------------------------------------------------------------------------
-- Pre-approval before execution, and results only from the runner.
-- ---------------------------------------------------------------------------
create or replace function vm_execution_guard() returns trigger language plpgsql as $$
declare a artifacts%rowtype;
begin
    select * into a from artifacts where id = new.artifact_id;
    if a.kind not in ('IQ','OQ','PQ') then
        raise exception 'only IQ/OQ/PQ protocols can be executed, not %', a.kind;
    end if;
    new.started_at := clock_timestamp();
    if a.status <> 'approved' or not exists (
            select 1 from signatures where artifact_id = a.id and meaning = 'approved'
                                       and signed_at < new.started_at) then
        raise exception 'protocol % v% is not approved: execution before approval is not permitted', a.kind, a.version;
    end if;
    if (select role from users where id = new.runner_id) is distinct from 'runner' then
        raise exception 'executions must be attributed to a runner identity';
    end if;
    return new;
end $$;

drop trigger if exists execution_guard on executions;
create trigger execution_guard before insert on executions
    for each row execute function vm_execution_guard();

create or replace function vm_result_guard() returns trigger language plpgsql as $$
begin
    if (select role from users where id = new.recorded_by) is distinct from 'runner' then
        raise exception 'results can only be recorded by the runner, not %', new.recorded_by;
    end if;
    if not exists (select 1 from evidence e where e.id = new.evidence_id and e.execution_id = new.execution_id
                                              and e.test_case_id = new.test_case_id) then
        raise exception 'a result must cite evidence captured in the same execution for the same test';
    end if;
    new.recorded_at := clock_timestamp();
    return new;
end $$;

drop trigger if exists result_guard on step_results;
create trigger result_guard before insert on step_results
    for each row execute function vm_result_guard();

create or replace function vm_evidence_guard() returns trigger language plpgsql as $$
begin
    new.captured_at := clock_timestamp();
    return new;
end $$;

drop trigger if exists evidence_guard on evidence;
create trigger evidence_guard before insert on evidence
    for each row execute function vm_evidence_guard();

-- ---------------------------------------------------------------------------
-- Deviations close only on a passing retest, by a human.
-- ---------------------------------------------------------------------------
create or replace function vm_deviation_guard() returns trigger language plpgsql as $$
begin
    if new.status = 'resolved' and old.status <> 'resolved' then
        if new.resolved_by is null or not (select is_human from users where id = new.resolved_by) then
            raise exception 'a deviation must be resolved by a human';
        end if;
        if new.resolution is null or new.retest_execution_id is null or not exists (
                select 1 from step_results where execution_id = new.retest_execution_id
                                             and test_case_id = new.test_case_id and outcome = 'pass') then
            raise exception 'a deviation needs a resolution and a passing retest before it can be closed';
        end if;
        new.resolved_at := clock_timestamp();
    end if;
    return new;
end $$;

drop trigger if exists deviation_guard on deviations;
create trigger deviation_guard before update on deviations
    for each row execute function vm_deviation_guard();

-- ---------------------------------------------------------------------------
-- A system is never "validated" without an approved Validation Summary Report.
-- ---------------------------------------------------------------------------
create or replace function vm_system_guard() returns trigger language plpgsql as $$
begin
    if new.status = 'validated' and old.status <> 'validated' then
        if not exists (select 1 from artifacts where system_id = new.id and kind = 'VSR' and status = 'approved') then
            raise exception 'system cannot be marked validated: no approved Validation Summary Report';
        end if;
        if exists (select 1 from deviations where system_id = new.id and status = 'open') then
            raise exception 'system cannot be marked validated: open deviations remain';
        end if;
    end if;
    return new;
end $$;

drop trigger if exists system_guard on systems;
create trigger system_guard before update on systems
    for each row execute function vm_system_guard();
