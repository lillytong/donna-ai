-- DD-65: issue status collapses from the 5-value taxonomy
-- (open/agreed/deferred/kicked/dismissed) to a binary open/closed.
-- Existing non-open rows migrate to 'closed'; the CHECK constraint is rebuilt.
-- Drop the constraint first so the UPDATE is not rejected by the old CHECK.

ALTER TABLE issues DROP CONSTRAINT IF EXISTS issues_status_check;

UPDATE issues SET status = 'closed' WHERE status <> 'open';

ALTER TABLE issues
    ADD CONSTRAINT issues_status_check CHECK (status IN ('open', 'closed'));
