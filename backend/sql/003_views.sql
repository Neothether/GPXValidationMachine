-- Live traceability, gap reports and audit-trail anomaly detection.

create or replace view v_current_artifacts as
select distinct on (system_id, kind) *
  from artifacts
 order by system_id, kind, version desc;

create or replace view v_latest_results as
select distinct on (sr.test_case_id) sr.*
  from step_results sr
 order by sr.test_case_id, sr.recorded_at desc;

-- requirement <-> risk <-> test <-> result <-> approval, one row per requirement/test pair.
create or replace view v_trace as
select r.system_id,
       r.id            as requirement_id,
       r.req_id,
       r.level,
       r.statement,
       r.source_ref,
       p.req_id        as parent_req_id,
       ra.status       as spec_status,
       k.rating        as risk_rating,
       k.test_depth,
       tc.id           as test_case_id,
       tc.test_id,
       tc.title        as test_title,
       pa.kind         as protocol_kind,
       pa.version      as protocol_version,
       pa.status       as protocol_status,
       lr.outcome,
       lr.recorded_at  as executed_at,
       lr.execution_id,
       ev.sha256       as evidence_sha256,
       d.status        as deviation_status
  from requirements r
  join v_current_artifacts ra on ra.id = r.artifact_id
  left join requirements p on p.id = r.parent_id
  left join risks k on k.requirement_id = r.id
  left join test_requirements tr on tr.requirement_id = r.id
  left join test_cases tc on tc.id = tr.test_case_id
                         and tc.artifact_id in (select id from v_current_artifacts)
  left join artifacts pa on pa.id = tc.artifact_id
  left join v_latest_results lr on lr.test_case_id = tc.id
  left join evidence ev on ev.id = lr.evidence_id
  left join lateral (select status from deviations where test_case_id = tc.id
                      order by opened_at desc limit 1) d on true;

create or replace view v_gaps as
select r.system_id, 'untested_requirement' as gap, r.req_id as ref, r.statement as detail
  from requirements r join v_current_artifacts a on a.id = r.artifact_id
 where r.level in ('URS','FS')
   and not exists (select 1 from test_requirements tr join test_cases tc on tc.id = tr.test_case_id
                    where tr.requirement_id = r.id and tc.artifact_id in (select id from v_current_artifacts))
union all
select r.system_id, 'unassessed_risk', r.req_id, r.statement
  from requirements r join v_current_artifacts a on a.id = r.artifact_id
 where r.level = 'FS' and not exists (select 1 from risks k where k.requirement_id = r.id)
union all
select tc.system_id, 'orphan_test', tc.test_id, tc.title
  from test_cases tc join v_current_artifacts a on a.id = tc.artifact_id
 where not exists (select 1 from test_requirements tr where tr.test_case_id = tc.id)
union all
select tc.system_id, 'not_executed', tc.test_id, tc.title
  from test_cases tc join v_current_artifacts a on a.id = tc.artifact_id
 where not exists (select 1 from step_results sr where sr.test_case_id = tc.id)
union all
select tc.system_id, 'failing_test', tc.test_id, tc.title
  from test_cases tc join v_current_artifacts a on a.id = tc.artifact_id
  join v_latest_results lr on lr.test_case_id = tc.id
 where lr.outcome <> 'pass'
union all
select a.system_id, 'unapproved_document', a.kind || ' v' || a.version, a.title
  from v_current_artifacts a
 where a.status = 'draft';

-- Anomalies in the target system's own audit trail (Part 11 §11.10(e), Annex 11 §9).
create or replace view v_audit_anomalies as
select system_id, 'back_dated_entry' as anomaly, actor, recorded_at as at,
       format('%s on %s claims event time %s but was recorded %s', action, record_id, event_time, recorded_at) as detail
  from target_audit_events
 where event_time < recorded_at - interval '1 hour'
union all
select system_id, 'self_approval', actor, recorded_at,
       format('%s approved record %s which they also authored', actor, record_id)
  from target_audit_events
 where action = 'approve' and detail->>'author' = actor
union all
select system_id, 'mass_delete', actor, bucket,
       format('%s deletions within 5 minutes', n)
  from (select system_id, actor, time_bucket('5 minutes', recorded_at) as bucket, count(*) as n
          from target_audit_events
         where action = 'delete'
         group by 1, 2, 3) b
 where n >= 10
union all
select system_id, 'off_hours_change', actor, recorded_at,
       format('%s on %s at %s UTC', action, record_id, to_char(recorded_at at time zone 'UTC', 'HH24:MI'))
  from target_audit_events
 where action in ('update','delete')
   and extract(hour from recorded_at at time zone 'UTC') between 1 and 4;

grant select on v_current_artifacts, v_latest_results, v_trace, v_gaps, v_audit_anomalies to vm_agent, vm_runner, vm_app;
