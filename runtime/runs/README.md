# Runtime runs

Each run is an append-only JSONL event stream plus an immutable normalized workflow snapshot.
Run files are generated locally and should contain IDs and evidence paths only, never secrets or PII.
Exchange messages store metadata, repository-relative artifact paths, and hashes, never payload text.
