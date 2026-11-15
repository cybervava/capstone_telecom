-- One visit booking in progress per service, whoever asks for it. The executor's check that no
-- visit is booked for the service and its booking are then never run twice at once for one
-- service: the second proposal is refused here, by the database.
CREATE UNIQUE INDEX actions_one_open_visit ON actions ((request_body ->> 'service_id'))
    WHERE action_type = 'VISIT_BOOK' AND status IN ('PENDING_APPROVAL', 'READY', 'IN_FLIGHT');
