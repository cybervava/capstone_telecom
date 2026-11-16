-- 0009_ops_items: what needs a person and belongs to no contact.
--
-- A write that failed with no conversation waiting for it (a notification, a ticket update made
-- by the feed consumer) and a feed that cannot go on by itself had nowhere to go but the log.
-- They are queued here for operations staff. There is one open item for each kind of trouble
-- and each subject: the same trouble seen again is counted on the item, not queued again.
CREATE TABLE ops_items (
    item_id      uuid PRIMARY KEY,
    kind         text NOT NULL CHECK (kind <> ''),
    subject      text NOT NULL CHECK (subject <> ''),
    summary      text NOT NULL,
    details      jsonb NOT NULL DEFAULT '{}'::jsonb,
    priority     smallint NOT NULL DEFAULT 3,
    status       text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'DONE')),
    occurrences  integer NOT NULL DEFAULT 1 CHECK (occurrences > 0),
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    last_seen_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    closed_at    timestamptz,
    closed_by    text,
    closed_note  text
);
CREATE UNIQUE INDEX ops_items_one_open ON ops_items (kind, subject) WHERE status = 'OPEN';
CREATE INDEX ops_items_open ON ops_items (priority, created_at) WHERE status = 'OPEN';
