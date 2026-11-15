-- 0002_reliability: retry back-off for failed workflow runs, who opened a contact, and handoffs
-- that can be raised again after an earlier one for the same reason was completed.

-- A contact whose run failed is not retried before this time (exponential back-off), so a
-- dependency outage of a few seconds cannot exhaust its attempts.
ALTER TABLE inbox ADD COLUMN retry_after timestamptz;
DROP INDEX inbox_pending;
CREATE INDEX inbox_pending ON inbox (seq) WHERE applied_at IS NULL AND NOT dead;

-- The API caller that opened the contact. A channel integration may only see its own contacts.
ALTER TABLE contacts ADD COLUMN created_by text;

-- One *open* handoff per contact and reason. A completed handoff no longer blocks a new one.
ALTER TABLE handoffs DROP CONSTRAINT handoffs_one_per_reason;
CREATE UNIQUE INDEX handoffs_one_open_per_reason ON handoffs (contact_id, reason)
    WHERE status <> 'DONE';
