-- Who closed an item of the staff queue, and what they noted. `closed_at` has been there since
-- the first migration; an item could not be closed until now.
ALTER TABLE handoffs ADD COLUMN closed_by text;
ALTER TABLE handoffs ADD COLUMN closed_note text;
