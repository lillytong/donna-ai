-- DD-67: the issue comments feature is removed entirely. The issue description
-- (title + position text) is editable inline instead, so a separate comment
-- thread is redundant. Drop the comment tables; comment_embeddings FKs into
-- issue_comments so it goes first.

DROP TABLE IF EXISTS comment_embeddings;
DROP TABLE IF EXISTS issue_comments;
