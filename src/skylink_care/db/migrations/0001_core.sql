-- 0001_core: intake ledger, per-contact inbox, node episodes, action ledger, approvals,
-- handoffs, audit log and the reference tables the mapping pipeline loads.

-- ---------------------------------------------------------------------------------------------
-- Intake ledger. One row per contact. The complaint reference is issued here, in the same
-- transaction as the first inbox row, before any model or dependency is called.
-- ---------------------------------------------------------------------------------------------
CREATE SEQUENCE complaint_ref_seq AS bigint;

CREATE TABLE contacts (
    contact_id                uuid PRIMARY KEY,
    channel                   text NOT NULL CHECK (channel IN ('chat', 'call_transcript')),
    client_request_id         text NOT NULL,
    request_hash              text NOT NULL,
    complaint_ref             text NOT NULL UNIQUE,
    ref_seq                   bigint NOT NULL UNIQUE,
    status                    text NOT NULL DEFAULT 'RECEIVED' CHECK (status IN (
                                  'RECEIVED', 'IN_PROGRESS', 'AWAITING_CUSTOMER', 'AWAITING_APPROVAL',
                                  'AWAITING_NETWORK', 'HANDOFF', 'RESOLVED', 'CLOSED')),
    session_id                text,
    authenticated_customer_id text,
    caller_phone              text,
    customer_id               text,
    service_id                text,
    service_type              text,
    node_id                   text,
    intent                    text,
    network_state             text,
    outage_id                 text,
    episode_id                uuid,
    correlation_id            text NOT NULL,
    received_at               timestamptz NOT NULL,
    reference_issued_at       timestamptz NOT NULL DEFAULT clock_timestamp(),
    reference_latency_ms      integer NOT NULL CHECK (reference_latency_ms >= 0),
    closed_at                 timestamptz,
    updated_at                timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT contacts_request_unique UNIQUE (channel, client_request_id)
);
CREATE INDEX contacts_customer_recent ON contacts (customer_id, received_at DESC)
    WHERE customer_id IS NOT NULL;
CREATE INDEX contacts_open_on_node ON contacts (node_id, service_type)
    WHERE closed_at IS NULL AND node_id IS NOT NULL;

CREATE TABLE contact_messages (
    message_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id  uuid NOT NULL REFERENCES contacts ON DELETE CASCADE,
    direction   text NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    author      text NOT NULL,
    body        text NOT NULL,
    meta        jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Makes a re-run of the same workflow step, or a client retry, a no-op.
    dedupe_key  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT contact_messages_dedupe UNIQUE (contact_id, dedupe_key)
);

-- ---------------------------------------------------------------------------------------------
-- Inbox. Everything that should wake a contact's workflow. The workflow keeps its own cursor in
-- checkpointed state, so redelivery is harmless; applied_at is only the scheduling hint.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE inbox (
    seq         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id  uuid NOT NULL REFERENCES contacts ON DELETE CASCADE,
    event_type  text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key  text NOT NULL,
    attempts    integer NOT NULL DEFAULT 0,
    last_error  text,
    dead        boolean NOT NULL DEFAULT false,
    applied_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT inbox_dedupe UNIQUE (contact_id, dedupe_key)
);
CREATE INDEX inbox_pending ON inbox (seq) WHERE applied_at IS NULL AND NOT dead;

-- ---------------------------------------------------------------------------------------------
-- Node episodes: the care platform's own identity for "something is wrong on this node".
-- At most one open episode per node and service, so a suspected fault and the outage the NOC
-- later declares share one incident ticket.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE node_episodes (
    episode_id    uuid PRIMARY KEY,
    node_id       text NOT NULL,
    service_type  text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('NETWORK_OUTAGE', 'SUSPECTED_NODE_FAULT')),
    outage_id     text,
    opened_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    closed_at     timestamptz
);
CREATE UNIQUE INDEX node_episodes_one_open ON node_episodes (node_id, service_type)
    WHERE closed_at IS NULL;
CREATE INDEX node_episodes_by_outage ON node_episodes (outage_id) WHERE outage_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- Action ledger. Agents propose; only the executor performs. request_body is exactly what will
-- be sent and is never rewritten: a changed request is a new action with a new key.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE actions (
    action_id       uuid PRIMARY KEY,
    action_key      text NOT NULL UNIQUE,
    action_type     text NOT NULL,
    lane            smallint NOT NULL DEFAULT 5,
    contact_id      uuid REFERENCES contacts,
    episode_id      uuid REFERENCES node_episodes,
    requested_by    text NOT NULL,
    request_body    jsonb NOT NULL,
    request_hash    text NOT NULL,
    inputs          jsonb NOT NULL DEFAULT '{}'::jsonb,
    depends_on      uuid REFERENCES actions,
    approval_level  text NOT NULL DEFAULT 'none'
                    CHECK (approval_level IN ('none', 'agent', 'supervisor')),
    status          text NOT NULL CHECK (status IN (
                        'PENDING_APPROVAL', 'READY', 'IN_FLIGHT', 'SUCCEEDED',
                        'FAILED', 'REJECTED', 'EXPIRED', 'BLOCKED')),
    policy_version  text,
    external_ref    text,
    response_body   jsonb,
    attempts        integer NOT NULL DEFAULT 0,
    available_at    timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_error      text,
    correlation_id  text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX actions_runnable ON actions (lane, available_at, created_at)
    WHERE status IN ('READY', 'IN_FLIGHT');
CREATE INDEX actions_by_contact ON actions (contact_id) WHERE contact_id IS NOT NULL;

CREATE TABLE approvals (
    approval_id    uuid PRIMARY KEY,
    action_id      uuid NOT NULL UNIQUE REFERENCES actions,
    required_role  text NOT NULL CHECK (required_role IN ('agent', 'supervisor')),
    -- Hash of the frozen request the approver is shown; the decision must quote it back.
    request_hash   text NOT NULL,
    summary        jsonb NOT NULL,
    status         text NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED')),
    requested_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at     timestamptz NOT NULL,
    decided_by     text,
    decided_at     timestamptz,
    decision_note  text
);
CREATE INDEX approvals_pending ON approvals (required_role, requested_at) WHERE status = 'PENDING';

CREATE TABLE handoffs (
    handoff_id  uuid PRIMARY KEY,
    contact_id  uuid NOT NULL REFERENCES contacts,
    reason      text NOT NULL,
    summary     text NOT NULL,
    priority    smallint NOT NULL DEFAULT 5,
    status      text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLAIMED', 'DONE')),
    claimed_by  text,
    created_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    closed_at   timestamptz,
    CONSTRAINT handoffs_one_per_reason UNIQUE (contact_id, reason)
);
CREATE INDEX handoffs_open ON handoffs (priority, created_at) WHERE status = 'OPEN';

-- ---------------------------------------------------------------------------------------------
-- Audit log: append-only. Who or what did each step, with redacted inputs and outputs and the
-- model, prompt and policy versions in force.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              timestamptz NOT NULL DEFAULT clock_timestamp(),
    correlation_id  text NOT NULL,
    contact_id      uuid,
    actor           text NOT NULL,
    step            text NOT NULL,
    outcome         text NOT NULL,
    inputs          jsonb NOT NULL DEFAULT '{}'::jsonb,
    outputs         jsonb NOT NULL DEFAULT '{}'::jsonb,
    model           text,
    prompt_version  text,
    policy_version  text,
    duration_ms     integer
);
CREATE INDEX audit_log_by_contact ON audit_log (contact_id, audit_id) WHERE contact_id IS NOT NULL;
CREATE INDEX audit_log_by_correlation ON audit_log (correlation_id, audit_id);

CREATE FUNCTION audit_log_is_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;
CREATE TRIGGER audit_log_no_rewrite BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();
CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_is_append_only();

-- ---------------------------------------------------------------------------------------------
-- Reference data loaded by the mapping pipeline. Identifiers only: no names, phones or emails.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE nodes (
    node_id         text PRIMARY KEY,
    node_type       text NOT NULL CHECK (node_type IN ('AGG', 'OLT', 'TOWER')),
    service_type    text,
    parent_node_id  text,
    city            text NOT NULL,
    area            text NOT NULL,
    pincode         text NOT NULL,
    status          text NOT NULL CHECK (status IN ('ACTIVE', 'DECOMMISSIONED'))
);
CREATE INDEX nodes_by_parent ON nodes (parent_node_id) WHERE parent_node_id IS NOT NULL;

CREATE TABLE address_node_map (
    address_id    text NOT NULL,
    service_type  text NOT NULL,
    node_id       text NOT NULL,
    source        text NOT NULL,
    valid_from    date NOT NULL,
    valid_to      date,
    map_version   text NOT NULL
);
CREATE INDEX address_node_map_current ON address_node_map (address_id, service_type)
    WHERE valid_to IS NULL;
CREATE INDEX address_node_map_by_node ON address_node_map (node_id) WHERE valid_to IS NULL;

CREATE TABLE service_index (
    service_id    text PRIMARY KEY,
    customer_id   text NOT NULL,
    service_type  text NOT NULL,
    address_id    text NOT NULL,
    status        text NOT NULL
);
CREATE INDEX service_index_by_address ON service_index (address_id, service_type);
CREATE INDEX service_index_by_customer ON service_index (customer_id);
