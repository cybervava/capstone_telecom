-- When an action became runnable: at proposal when it needs no approval, at the approval
-- otherwise. The executor's give-up clock runs from here and not from the proposal, because an
-- approval may take days and the write deserves its full retry window after it.
ALTER TABLE actions ADD COLUMN ready_at timestamptz;
UPDATE actions SET ready_at = created_at WHERE status <> 'PENDING_APPROVAL';

-- Credits that are proposed but not yet settled, per service: they count towards the
-- billing-cycle cap when the next credit on the same service is worked out.
CREATE INDEX actions_open_credits ON actions ((request_body ->> 'service_id'))
    WHERE action_type = 'CREDIT_APPLY' AND status IN ('PENDING_APPROVAL', 'READY', 'IN_FLIGHT');
