-- DD-72: drift marker for "edited since last export" (DD-71 Mark-as-sent).
-- A nullable timestamp the clean-copy export route stamps on every export; the
-- Mark-as-sent flow compares each non-deleted node's updated_at against it to
-- detect drift (a non-blocking "you've edited since your last export" warning).
-- This is the ONE column DD-71 said it wouldn't need: the snapshot/pointer
-- mechanism is unchanged, only the drift marker requires schema.
ALTER TABLE contracts ADD COLUMN IF NOT EXISTS last_export_at TIMESTAMPTZ;
