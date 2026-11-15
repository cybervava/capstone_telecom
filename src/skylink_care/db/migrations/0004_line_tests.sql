-- 0004_line_tests: remote line tests the platform has run.
-- A line test is a read-only probe. Its result decides what may be done for one customer, the
-- node it names repairs a defective address mapping, and several lines failing on one node
-- within a short time are evidence of a node fault the NOC has not declared yet.
CREATE TABLE line_tests (
    test_id     text PRIMARY KEY,                 -- issued by Network Monitoring
    contact_id  uuid REFERENCES contacts ON DELETE CASCADE,
    service_id  text NOT NULL,
    node_id     text NOT NULL,                    -- the node that serves the line, per the test
    result      text NOT NULL CHECK (result IN ('PASS', 'FAIL', 'INCONCLUSIVE')),
    tested_at   timestamptz NOT NULL
);
CREATE INDEX line_tests_by_node ON line_tests (node_id, tested_at);
CREATE INDEX line_tests_by_service ON line_tests (service_id, tested_at DESC);
