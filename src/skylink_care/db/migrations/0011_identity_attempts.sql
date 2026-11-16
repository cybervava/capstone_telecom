-- 0011_identity_attempts: tries at the identity check, counted for each claimed customer ID.
--
-- A customer who is not signed in is verified by a customer ID and the last four digits of the
-- registered mobile. A conversation allows three tries, and nothing counted the tries for one
-- customer ID across conversations: four digits are ten thousand guesses.
--
-- A try is taken here before the answer is put to the CRM, under a lock on the ID, so that
-- conversations answering at the same moment cannot each be the last one allowed. It is given
-- back (deleted) when the answer was right or could not be checked, and stays when it was
-- wrong, for an ID that exists or one that does not. While a customer ID has as many rows
-- as the limits allow (a few within a short window, a few more within a day), no further try
-- for it reaches the CRM.
--
-- A row is of use for a day. Whoever takes a try also removes a handful of older rows,
-- anyone's.
CREATE TABLE identity_attempts (
    attempt_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id   text NOT NULL CHECK (customer_id <> ''),
    contact_id    uuid NOT NULL REFERENCES contacts (contact_id) ON DELETE CASCADE,
    -- What the CRM said of the claimed ID: there is such a customer (and the digits were
    -- wrong), or there is none. NULL until it has answered. Staff are told of a locked ID only
    -- when it is somebody's.
    account_known boolean,
    attempted_at  timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX identity_attempts_by_customer ON identity_attempts (customer_id, attempted_at);
CREATE INDEX identity_attempts_by_age ON identity_attempts (attempted_at);
