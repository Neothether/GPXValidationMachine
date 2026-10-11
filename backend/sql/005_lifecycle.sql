-- Lifecycle: release tied to the software version, release blockers, accepted deviations, finding
-- dispositions, manual test execution, package snapshots, and the remaining record types.
-- Idempotent. Functions defined here replace the earlier versions in 002_enforcement.sql.

-- ---------------------------------------------------------------------------
-- Columns, record types, identities
-- ---------------------------------------------------------------------------
alter table systems add column if not exists current_fingerprint text;
alter table systems add column if not exists archived boolean not null default false;
alter table changes add column if not exists regressed_at timestamptz;
alter table changes add column if not exists fingerprint_after text;
alter table changes add column if not exists withdrawn_at timestamptz;
alter table changes add column if not exists withdrawn_by text references users(id);
alter table findings add column if not exists disposition text;
alter table findings add column if not exists disposition_note text;
alter table findings add column if not exists disposed_by text references users(id);
alter table findings add column if not exists disposed_at timestamptz;

alter table findings drop constraint if exists findings_disposition_check;
alter table findings add constraint findings_disposition_check check (disposition in ('addressed','accepted'));

-- SUPPLIER = supplier assessment, ATR = audit trail review (with CAPA), PR = periodic review
alter table artifacts drop constraint if exists artifacts_kind_check;
alter table artifacts add constraint artifacts_kind_check
    check (kind in ('VP','URS','FS','DS','RA','IQ','OQ','PQ','VSR','CHANGE','SUPPLIER','ATR','PR'));

alter table deviations drop constraint if exists deviations_status_check;
alter table deviations add constraint deviations_status_check check (status in ('open','resolved','accepted'));

insert into users (id, display_name, role, is_human) values
    ('agent:supplier', 'Supplier Assessment Agent', 'agent', false),
    ('agent:audit_review', 'Audit Trail Review Generator', 'agent', false),
    ('agent:periodic_review', 'Periodic Review Generator', 'agent', false),
    ('agent:inspection', 'Inspection Readiness Agent', 'agent', false),
    ('ci:pipeline', 'CI Pipeline', 'agent', false)
on conflict (id) do nothing;

-- Records left behind by automated tests are hidden from the inventory.
update systems set archived = true where name in ('enforcement-test', 'cycle-test') and not archived;

create table if not exists releases (
    id              uuid primary key default gen_random_uuid(),
    system_id       uuid not null references systems(id),
    fingerprint     text not null,
    vsr_artifact_id uuid not null references artifacts(id),
    released_by     text not null references users(id),
    released_at     timestamptz not null default clock_timestamp()
);

-- A point-in-time copy of the whole validation package, kept per software version.
create table if not exists package_snapshots (
    id          uuid primary key default gen_random_uuid(),
    system_id   uuid not null references systems(id),
    label       text not null,
    fingerprint text,
    html        text not null,
    sha256      text not null,
    created_by  text not null references users(id),
    created_at  timestamptz not null default clock_timestamp()
);

-- ---------------------------------------------------------------------------
-- Roles and privileges
-- ---------------------------------------------------------------------------
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'vm_tester') then
        create role vm_tester nologin;      -- a human tester recording a manual test step
    end if;
    execute 'grant vm_tester to current_user';
end $$;

grant usage on schema public to vm_tester;
grant select on all tables in schema public to vm_tester;
revoke all on sessions from vm_tester;
grant insert on executions, evidence, step_results, deviations to vm_tester;
grant update (finished_at) on executions to vm_tester;

grant select on releases, package_snapshots to vm_agent, vm_runner, vm_app, vm_tester;
grant insert on package_snapshots to vm_app;
-- repo_path: a pipeline checks the repository out somewhere else; the fingerprint, not the path, identifies the version
grant update (current_fingerprint, archived, repo_path) on systems to vm_app;
grant update (regressed_at, fingerprint_after, withdrawn_at, withdrawn_by) on changes to vm_app;
grant update (disposition, disposition_note, disposed_by, disposed_at) on findings to vm_app;

do $$
declare t text;
begin
    foreach t in array array['releases','package_snapshots'] loop
        execute format('drop trigger if exists forbid_mutation on %I', t);
        execute format('create trigger forbid_mutation before update or delete on %I
                        for each row execute function vm_forbid_mutation()', t);
        execute format('drop trigger if exists audit_row on %I', t);
        execute format('create trigger audit_row after insert on %I
                        for each row execute function vm_audit_row()', t);
    end loop;
end $$;

create or replace function vm_audit_row() returns trigger language plpgsql security definer
set search_path = public as $$
begin
    perform vm_audit(lower(tg_op), tg_table_name, new.id::text,
                     to_jsonb(new) - 'content' - 'code' - 'diff' - 'sop' - 'password_hash' - 'steps' - 'html' - 'actual');
    return new;
end $$;

-- ---------------------------------------------------------------------------
-- Release blockers: every reason the system may not be released, for the software as it is now.
-- ---------------------------------------------------------------------------
create or replace function vm_release_blockers(p_system uuid, p_include_summary boolean default true)
returns table (code text, ref text, detail text) language plpgsql stable as $$
#variable_conflict use_column
declare
    s            systems%rowtype;
    exec_fp      text;
    exec_at      timestamptz;
    chg_fp       text;
    chg_at       timestamptz;
    expected     text;
    vsr          artifacts%rowtype;
    latest_event timestamptz;
begin
    select * into s from systems where id = p_system;

    -- Documents
    return query
    select 'document_missing'::text, k, k || ' has not been drafted'
      from unnest(array['URS','FS','RA','OQ']) k
     where not exists (select 1 from v_current_artifacts a where a.system_id = p_system and a.kind = k);

    return query
    select 'document_unapproved'::text, a.kind || ' v' || a.version, a.title || ' is not approved'
      from v_current_artifacts a
     where a.system_id = p_system and a.kind in ('VP','URS','FS','DS','RA','IQ','OQ','PQ') and a.status <> 'approved';

    return query
    select g.gap, g.ref, g.detail from v_gaps g
     where g.system_id = p_system and g.gap in ('untested_requirement','unassessed_risk');

    -- Software version: what is on disk now must be the version last tested or assessed.
    select e.target_fingerprint, e.started_at into exec_fp, exec_at
      from executions e where e.system_id = p_system order by e.started_at desc limit 1;
    select c.fingerprint_after, c.regressed_at into chg_fp, chg_at
      from changes c where c.system_id = p_system and c.regressed_at is not null and c.withdrawn_at is null
     order by c.regressed_at desc limit 1;
    expected := case when chg_at is not null and (exec_at is null or chg_at >= exec_at) then chg_fp else exec_fp end;

    if exec_fp is null then
        return query select 'not_tested'::text, 'software'::text, 'No protocol has been executed'::text;
    elsif s.current_fingerprint is null then
        return query select 'version_unknown'::text, 'software'::text,
                            'The current software fingerprint has not been recorded'::text;
    elsif s.current_fingerprint <> expected then
        return query select 'software_changed'::text, 'software'::text,
            format('The software now has fingerprint %s, but the version last tested or assessed is %s. Register the change and assess its impact.',
                   left(s.current_fingerprint, 12), left(expected, 12));
    end if;

    -- Tests: each needs a passing result that applies to the current software version. A result recorded
    -- against an earlier version still applies only if every later change was regressed without that test in scope.
    return query
    with tc as (
        select t.id, t.test_id from test_cases t join v_current_artifacts a on a.id = t.artifact_id
         where t.system_id = p_system and a.kind in ('IQ','OQ','PQ')),
    st as (
        select tc.id, tc.test_id, lr.id as result_id, lr.outcome, lr.recorded_at, e.target_fingerprint,
               (select d.status from deviations d where d.test_case_id = tc.id order by d.opened_at desc limit 1) as dev_status
          from tc left join v_latest_results lr on lr.test_case_id = tc.id
          left join executions e on e.id = lr.execution_id)
    select x.code, x.ref, x.detail from (
        select 'test_not_executed'::text as code, string_agg(st.test_id, ', ' order by st.test_id) as ref,
               count(*) || ' test(s) have no recorded result' as detail, count(*) as n
          from st where st.result_id is null
        union all
        select 'test_failing', string_agg(st.test_id, ', ' order by st.test_id),
               count(*) || ' test(s) fail and have no accepted deviation', count(*)
          from st where st.result_id is not null and st.outcome <> 'pass' and coalesce(st.dev_status, '') <> 'accepted'
        union all
        select 'result_outdated', string_agg(st.test_id, ', ' order by st.test_id),
               count(*) || ' test(s) were last executed against an earlier software version and no regressed change covers them. Re-execute them.',
               count(*)
          from st
         where st.result_id is not null
           and st.target_fingerprint is distinct from s.current_fingerprint
           and (not exists (select 1 from changes c where c.system_id = p_system and c.withdrawn_at is null
                                                      and c.created_at > st.recorded_at)
                or exists (select 1 from changes c where c.system_id = p_system and c.withdrawn_at is null
                                                     and c.created_at > st.recorded_at
                                                     and (c.regressed_at is null
                                                          or c.impact->'regression_scope' @>
                                                             jsonb_build_array(jsonb_build_object('test_case_id', st.id::text)))))
    ) x where x.n > 0;

    -- Deviations, changes, review findings
    return query
    select 'deviation_open'::text, t.test_id, left(d.description, 160)
      from deviations d join test_cases t on t.id = d.test_case_id
     where d.system_id = p_system and d.status = 'open';

    return query
    select 'change_not_regressed'::text, left(c.description, 60),
           'The regression scope of this change has not been executed'::text
      from changes c where c.system_id = p_system and c.withdrawn_at is null and c.regressed_at is null;

    return query
    select 'change_unapproved'::text, left(c.description, 60), 'The change record is not approved'::text
      from changes c left join artifacts a on a.id = c.artifact_id
     where c.system_id = p_system and c.withdrawn_at is null and (a.id is null or a.status <> 'approved');

    return query
    select 'finding_open'::text, f.document || coalesce(' ' || f.location, ''), left(f.finding, 160)
      from findings f
     where f.system_id = p_system and f.severity in ('critical','major') and f.disposition is null
       and f.document <> 'Traceability matrix';      -- those are live gaps, already listed above

    -- Summary report: must be newer than the last thing it summarises, and approved.
    select * into vsr from v_current_artifacts a where a.system_id = p_system and a.kind = 'VSR';
    select greatest(
        (select max(sr.recorded_at) from step_results sr join executions e on e.id = sr.execution_id
          where e.system_id = p_system),
        (select max(c.regressed_at) from changes c where c.system_id = p_system and c.withdrawn_at is null),
        (select max(d.resolved_at) from deviations d where d.system_id = p_system)) into latest_event;
    if vsr.id is null then
        if p_include_summary then
            return query select 'summary_missing'::text, 'VSR'::text,
                                'The Validation Summary Report has not been drafted'::text;
        end if;
    elsif latest_event is not null and vsr.created_at < latest_event then
        return query select 'summary_outdated'::text, 'VSR v' || vsr.version,
            'The summary report was drafted before the latest results, change or deviation closure. Draft a new version.'::text;
    elsif p_include_summary and vsr.status <> 'approved' then
        return query select 'summary_unapproved'::text, 'VSR v' || vsr.version,
                            'The Validation Summary Report is not approved by QA and the System Owner'::text;
    end if;
end $$;

-- ---------------------------------------------------------------------------
-- Release: only QA or the system owner, only with no blockers; the released version is recorded.
-- ---------------------------------------------------------------------------
create or replace function vm_system_guard() returns trigger language plpgsql security definer
set search_path = public as $$
declare reasons text;
begin
    if new.status = 'validated' and old.status <> 'validated' then
        if not exists (select 1 from users where id = vm_actor() and is_human and role in ('qa','system_owner')) then
            raise exception 'only Quality Assurance or the System Owner can release a system';
        end if;
        select string_agg(b.code || ' (' || b.ref || ')', '; ') into reasons
          from (select * from vm_release_blockers(new.id) limit 6) b;
        if reasons is not null then
            raise exception 'system cannot be released: %', reasons;
        end if;
        insert into releases (system_id, fingerprint, vsr_artifact_id, released_by)
        values (new.id, new.current_fingerprint,
                (select a.id from v_current_artifacts a where a.system_id = new.id and a.kind = 'VSR'), vm_actor());
    end if;
    return new;
end $$;

-- ---------------------------------------------------------------------------
-- Signatures: as before, plus the summary report cannot be approved against open blockers,
-- and dated records (changes, periodic reviews, audit trail reviews) never supersede each other.
-- ---------------------------------------------------------------------------
create or replace function vm_signature_guard() returns trigger language plpgsql as $$
declare
    a artifacts%rowtype;
    u users%rowtype;
    reasons text;
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
    if a.kind = 'VSR' and new.meaning = 'approved' then
        select string_agg(b.code || ' (' || b.ref || ')', '; ') into reasons
          from (select * from vm_release_blockers(a.system_id, false) limit 6) b;
        if reasons is not null then
            raise exception 'the summary report cannot be approved while release blockers remain: %', reasons;
        end if;
    end if;
    new.signer_name := u.display_name;
    new.signed_at := clock_timestamp();   -- signatures cannot be back-dated
    return new;
end $$;

create or replace function vm_signature_apply() returns trigger language plpgsql security definer
set search_path = public as $$
declare a artifacts%rowtype;
begin
    if vm_is_approvable(new.artifact_id) then
        select * into a from artifacts where id = new.artifact_id;
        update artifacts set status = 'superseded'
         where system_id = a.system_id and kind = a.kind and kind not in ('CHANGE','PR','ATR')
           and status = 'approved' and id <> a.id;
        update artifacts set status = 'approved' where id = a.id;
    end if;
    return new;
end $$;

-- Periodic reviews need the system owner as well as QA, like the plan and the summary report.
create or replace function vm_is_approvable(p_artifact uuid) returns boolean language sql stable as $$
    select exists (
               select 1 from signatures s join users u on u.id = s.signer_id join artifacts a on a.id = s.artifact_id
               where s.artifact_id = p_artifact and s.meaning = 'approved' and u.role = 'qa'
                 and s.content_hash = a.content_hash)
       and (
               (select kind from artifacts where id = p_artifact) not in ('VP','VSR','PR')
            or exists (
               select 1 from signatures s join users u on u.id = s.signer_id join artifacts a on a.id = s.artifact_id
               where s.artifact_id = p_artifact and s.meaning = 'approved' and u.role = 'system_owner'
                 and s.content_hash = a.content_hash))
$$;

-- ---------------------------------------------------------------------------
-- Execution: automated tests are recorded by the runner, manual tests by a human tester. Never crossed.
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
    if not exists (select 1 from users where id = new.runner_id
                                         and (role = 'runner' or (is_human and role = 'tester'))) then
        raise exception 'executions must be attributed to a runner identity or a human tester';
    end if;
    return new;
end $$;

create or replace function vm_result_guard() returns trigger language plpgsql as $$
declare
    how text;
    who users%rowtype;
begin
    select automation into how from test_cases where id = new.test_case_id;
    select * into who from users where id = new.recorded_by;
    if how = 'pytest' and who.role is distinct from 'runner' then
        raise exception 'results of automated tests can only be recorded by the runner, not %', new.recorded_by;
    end if;
    if how = 'manual' and not (who.is_human and who.role = 'tester') then
        raise exception 'results of manual tests must be recorded by a human tester, not %', new.recorded_by;
    end if;
    if how = 'manual' and current_user <> 'vm_tester' then
        raise exception 'manual results are recorded through the tester path only';
    end if;
    if how = 'pytest' and current_user = 'vm_tester' then
        raise exception 'a tester cannot record the result of an automated test';
    end if;
    if not exists (select 1 from evidence e where e.id = new.evidence_id and e.execution_id = new.execution_id
                                              and e.test_case_id = new.test_case_id) then
        raise exception 'a result must cite evidence captured in the same execution for the same test';
    end if;
    new.recorded_at := clock_timestamp();
    return new;
end $$;

-- ---------------------------------------------------------------------------
-- Deviations: closed by a passing retest, or accepted by QA with a written justification.
-- ---------------------------------------------------------------------------
create or replace function vm_deviation_guard() returns trigger language plpgsql as $$
begin
    if new.status in ('resolved','accepted') and old.status = 'open' then
        if new.resolved_by is null or not (select is_human from users where id = new.resolved_by) then
            raise exception 'a deviation must be closed by a human';
        end if;
        if new.status = 'resolved' and (new.resolution is null or new.retest_execution_id is null or not exists (
                select 1 from step_results where execution_id = new.retest_execution_id
                                             and test_case_id = new.test_case_id and outcome = 'pass')) then
            raise exception 'a deviation needs a resolution and a passing retest before it can be closed';
        end if;
        if new.status = 'accepted' then
            if (select role from users where id = new.resolved_by) is distinct from 'qa' then
                raise exception 'only Quality Assurance can accept a deviation without a passing retest';
            end if;
            if new.resolution is null or length(trim(new.resolution)) < 30 then
                raise exception 'accepting a deviation needs a written justification of at least 30 characters';
            end if;
        end if;
        new.resolved_at := clock_timestamp();
    elsif old.status <> 'open' and new.status is distinct from old.status then
        raise exception 'a closed deviation cannot be reopened or re-closed; raise a new one';
    end if;
    return new;
end $$;

-- ---------------------------------------------------------------------------
-- Review findings: QA records how each one was dealt with; nothing else about a finding can change.
-- ---------------------------------------------------------------------------
create or replace function vm_finding_guard() returns trigger language plpgsql as $$
begin
    if (new.severity, new.document, new.location, new.finding, new.recommended_action, new.found_by)
       is distinct from (old.severity, old.document, old.location, old.finding, old.recommended_action, old.found_by) then
        raise exception 'the content of a review finding cannot be edited';
    end if;
    if new.disposition is not null then
        if (select role from users where id = new.disposed_by and is_human) is distinct from 'qa' then
            raise exception 'only Quality Assurance can disposition a review finding';
        end if;
        if new.disposition_note is null or length(trim(new.disposition_note)) < 15 then
            raise exception 'a disposition needs a note of at least 15 characters';
        end if;
        new.disposed_at := clock_timestamp();
    end if;
    return new;
end $$;

drop trigger if exists finding_guard on findings;
create trigger finding_guard before update on findings
    for each row execute function vm_finding_guard();
