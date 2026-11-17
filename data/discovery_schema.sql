-- Table: docs
CREATE TABLE docs (
    id TEXT PRIMARY KEY, table_id TEXT NOT NULL, title TEXT NOT NULL,
    source_label TEXT NOT NULL, source_url TEXT NOT NULL,
    text TEXT NOT NULL, keywords TEXT NOT NULL, doc_type TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

