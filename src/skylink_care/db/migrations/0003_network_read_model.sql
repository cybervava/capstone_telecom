-- 0003_network_read_model: what the care platform has learnt from the NOC event feed.
-- This read model can prove that an outage exists. It can never prove that one does not:
-- silence on a feed is not evidence, and the feed may be behind or down.

-- Position and health of the feed consumer (one row).
CREATE TABLE feed_state (
    feed             text PRIMARY KEY,
    applied_seq      bigint NOT NULL DEFAULT 0,   -- last event applied to the tables below
    source_last_seq  bigint NOT NULL DEFAULT 0,   -- highest sequence the feed said it holds
    heartbeat_at     timestamptz,                 -- when the feed produced its last answer
    polled_at        timestamptz,                 -- when we last received an answer
    last_error       text,
    last_error_at    timestamptz,
    -- Set when an event was lost or unusable (a hole in the sequence, a malformed outage event).
    -- The tables below may then be missing a resolution, so they stop counting as evidence
    -- until an operator has reconciled them and clears the mark.
    gap_at           timestamptz,
    gap_detail       text
);
INSERT INTO feed_state (feed) VALUES ('noc');

CREATE TABLE outages (
    outage_id       text PRIMARY KEY,
    version         integer NOT NULL,
    cause           text NOT NULL,
    planned         boolean NOT NULL,
    service_types   text[] NOT NULL DEFAULT '{}',
    started_at      timestamptz NOT NULL,
    eta             timestamptz,
    eta_source      text,
    resolved_at     timestamptz,
    last_event_seq  bigint NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE outage_nodes (
    outage_id  text NOT NULL REFERENCES outages ON DELETE CASCADE,
    node_id    text NOT NULL,
    PRIMARY KEY (outage_id, node_id)
);
CREATE INDEX outage_nodes_by_node ON outage_nodes (node_id);

CREATE TABLE node_alarms (
    alarm_id        text NOT NULL,
    node_id         text NOT NULL,
    alarm_type      text NOT NULL,
    severity        text NOT NULL,                -- 'unknown' when the source did not say
    raised_at       timestamptz NOT NULL,
    cleared_at      timestamptz,
    last_event_seq  bigint NOT NULL,
    PRIMARY KEY (alarm_id, node_id)
);
CREATE INDEX node_alarms_active ON node_alarms (node_id) WHERE cleared_at IS NULL;

-- A notification that must not be sent (no consent at send time) is a finished action, not a
-- failed one: nobody needs to be alerted and nothing will be retried.
ALTER TABLE actions DROP CONSTRAINT actions_status_check;
ALTER TABLE actions ADD CONSTRAINT actions_status_check CHECK (status IN (
    'PENDING_APPROVAL', 'READY', 'IN_FLIGHT', 'SUCCEEDED', 'SKIPPED',
    'FAILED', 'REJECTED', 'EXPIRED', 'BLOCKED'));

-- Timers: "wake this contact at a later time" (re-check the network, end a hold-down, close a
-- quiet contact). A due timer is turned into an inbox event exactly once.
CREATE TABLE timers (
    timer_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id  uuid NOT NULL REFERENCES contacts ON DELETE CASCADE,
    kind        text NOT NULL,
    fire_at     timestamptz NOT NULL,
    dedupe_key  text NOT NULL,
    fired_at    timestamptz,
    cancelled   boolean NOT NULL DEFAULT false,
    CONSTRAINT timers_dedupe UNIQUE (contact_id, dedupe_key)
);
CREATE INDEX timers_due ON timers (fire_at) WHERE fired_at IS NULL AND NOT cancelled;
