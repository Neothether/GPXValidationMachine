-- Sign-in sessions and password changes. Idempotent: safe to re-run.

-- Only a hash of the session token is stored, so a database reader cannot reuse a session.
create table if not exists sessions (
    token_hash text primary key,
    user_id    text not null references users(id),
    created_at timestamptz not null default clock_timestamp(),
    expires_at timestamptz not null,
    revoked_at timestamptz
);
create index if not exists sessions_user on sessions (user_id);

revoke all on sessions from vm_agent, vm_runner;
grant select, insert on sessions to vm_app;
grant update (revoked_at) on sessions to vm_app;

-- A person changes their own password by proving the current one. Other sessions of theirs end.
create or replace function vm_change_password(p_user text, p_current text, p_new text, p_keep_session text)
returns boolean language plpgsql security definer set search_path = public as $$
begin
    if length(p_new) < 10 then
        raise exception 'the new password must be at least 10 characters';
    end if;
    if p_new = p_current then
        raise exception 'the new password must differ from the current one';
    end if;
    update users set password_hash = crypt(p_new, gen_salt('bf'))
     where id = p_user and is_human and password_hash = crypt(p_current, password_hash);
    if not found then
        return false;
    end if;
    update sessions set revoked_at = clock_timestamp()
     where user_id = p_user and revoked_at is null and token_hash <> p_keep_session;
    perform vm_audit('password_changed', 'user', p_user, '{}');
    return true;
end $$;
revoke all on function vm_change_password(text, text, text, text) from public;
grant execute on function vm_change_password(text, text, text, text) to vm_app;
