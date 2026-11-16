-- 0010_network_checked_at: when the network state on a contact was decided.
--
-- The contact row carries what the last diagnosis found, and the customer's page shows it. A
-- state without its time cannot be told from one that is hours old: "none reported" must not
-- stand on the page long after anyone looked.
ALTER TABLE contacts ADD COLUMN network_checked_at timestamptz;
