# AX Fabric Odoo knowledge-layer lab

This repository contains the local foundation for the first AX Fabric Odoo knowledge layer. It is a development and validation environment, not the final production deployment.

## Current components

- PostgreSQL 17 with pgvector 0.8.6 is the canonical structured knowledge store and vector-search engine.
- MinIO is the local S3-compatible evidence store.
- PostgreSQL relationship tables hold the first graph projection. LadybugDB is deferred until its versioning, authentication, backup, concurrency and operational model are validated.

Both runtime images are pinned by digest. Ports bind to loopback so the services are not exposed on every host interface.

## Storage responsibilities

- PostgreSQL stores sources, provenance, Odoo releases/modules/dependencies, claims, tenant environment snapshots, ingestion state, artifact references and audit events.
- pgvector stores embeddings only after the source content is approved for retrieval.
- MinIO stores immutable source files, raw extracts, normalized exports, evaluation evidence and published pack artifacts.

The knowledge layer must not become a copy of an entire customer Odoo database. Customer configuration and limited evidence stay in a tenant-private namespace.

## Local startup

1. Copy `.env.example` to `.env` and replace every example secret.
2. Start Docker Desktop.
3. Run:

```powershell
docker compose --env-file .env -f compose\foundation.yml up -d
docker compose --env-file .env -f compose\foundation.yml ps
```

The one-shot `minio-init` container creates five buckets and enables bucket versioning. It should exit successfully after initialization.

Local endpoints:

- PostgreSQL: private Docker endpoint `ax-postgres:5432`; no host port is published
- MinIO API: `http://127.0.0.1:9000`
- MinIO console: `http://127.0.0.1:9001`

## Data initialization and migrations

`deployment/docker/init-database.sql` initializes a new PostgreSQL volume. Docker entrypoint initialization does not rerun this file for an existing volume. Apply it explicitly as a migration when upgrading an existing local database.

Before applying a migration, create a database backup. After applying it, validate the schema, extensions and seed records. Do not use `docker compose down -v` because it deletes the retained PostgreSQL and MinIO volumes.

Incremental migrations are stored in `deployment/migrations` and must be applied in migration-number order with
`deployment/scripts/apply-migrations.ps1`. The runner verifies SHA-256 checksums and stops if an applied migration
was changed. Migration `002_knowledge_core_governance_evidence.sql` adds tenant placement, rights,
retention, scopes, immutable record revisions, publication events, source artifact/version lineage, normalized
documents, passages and exact evidence spans.

Migration `003_scope_lineage_integrity.sql` validates scope parents and cross-scope grants, enforces same-scope
lineage links and applies scope-aware row-level-security policies to the canonical tables.

Run `tests/sql/002_knowledge_core_governance_evidence_test.sql` after migration 002. It uses a rolled-back
transaction to verify core constraints and immutable revisions while confirming that the existing Odoo ingestion
records remain intact.

The Odoo manifest worker writes both the compatibility tables and the canonical lineage path. Set
`ODOO_SOURCE_REVISION` to the exact checked-out commit, then run:

```powershell
docker compose --env-file .env -f compose\foundation.yml -f compose\ingestion.yml run --build --rm ingestion-worker
```

Unchanged manifests reuse their canonical artifact, source-version and processing-output records. Run
`tests/sql/003_scope_lineage_integrity_test.sql` to verify grant enforcement, RLS policy presence and lineage counts.

## Official Odoo source coverage

The current verified public corpus pins the official Community source and documentation for three supported major
versions. Exact revisions make every extraction reproducible and prevent a moving branch from silently changing an
answer.

| Version | Community source revision | Modules | Dependencies | Documentation revision | Documents | Blocks | Passages |
| --- | --- | ---: | ---: | --- | ---: | ---: | ---: |
| 17.0 | `c98bc11e08d38cbc9f0e4bcfc552343ff9030583` | 611 | 1,201 | `fbd45fcb982b00e45073a502b74073123acc6369` | 955 | 49,150 | 2,783 |
| 18.0 | `db1315502cfa31d62305948524160b211c5e50c0` | 655 | 1,342 | `e03c73f147f3a265bc3e6bd7e18ee116ff2c814b` | 1,063 | 56,395 | 3,159 |
| 19.0 | `a830a6a1dafb7f24e484460ef3a15c2e0583090b` | 662 | 1,416 | `0937ae12d0206b1229730a13ca99b8a78bfc2b6b` | 1,159 | 62,887 | 3,531 |

Module counts are distinct module releases parsed from manifests. Dependency counts are dependency edges, so one
module normally contributes several edges. The documentation sparse checkouts include authoritative RST text and
exclude large media assets. The documentation repository license is CC BY-SA 4.0; source records retain attribution,
processing details and redistribution conditions.

Run the documentation ingestion with:

```powershell
docker compose --env-file .env -f compose\foundation.yml -f compose\documentation-ingestion.yml run --build --rm documentation-ingestion-worker
```

The worker stores original RST objects in `odoo-source-snapshots` and normalized JSON in
`odoo-normalized-data`. PostgreSQL stores source versions, structural blocks, line-based locators, retrieval
passages and block-to-passage mappings. A repeated run against the same commit reuses all immutable objects and
documents. All three documentation runs completed without skipped or failed files. The Odoo 19 corpus additionally
passed the citation-integrity and retrieval evaluations described below.

Run `tests/sql/004_documentation_corpus_test.sql` to verify corpus completeness, citation integrity and lexical
search availability.

Public Community source and official documentation do not provide every fact AX Fabric needs. Enterprise source and
Enterprise-only manifests require authorized licensed access. Current pricing and subscription terms require a
separately captured time-stamped official web source. Odoo Online and Odoo.sh operational restrictions, localization
claims, migration recipes and API contracts require targeted extraction and version-pair tests. OCA and third-party
applications require an allowlist plus license, security, maintenance and publisher review. Customer configuration,
installed modules, permissions and support evidence belong in tenant-private scopes and require customer-authorized
discovery. None of these categories should be inferred from Community manifests alone.

## Lexical retrieval baseline

Migrations `004_scoped_lexical_retrieval.sql` and `005_english_lexical_ranking.sql` provide scoped, ranked English
full-text retrieval. `knowledge.search_passages` returns bounded candidates with the exact source path, line range,
pinned Git revision and GitHub citation URL. Search results are evidence candidates and do not become accepted
knowledge claims automatically.

Run the versioned evaluation with:

```powershell
docker compose --env-file .env -f compose\foundation.yml -f compose\retrieval-evaluation.yml run --build --rm retrieval-evaluator
```

The `odoo-19-lexical-v1` baseline contains 20 questions across deployment, licensing, Studio, security, inventory,
sales, finance, projects, support, HR and APIs. Its verified result is Recall@10 100%, mean reciprocal rank 0.758,
citation integrity 100%, p50 query latency 148 ms and p95 349 ms on this local corpus. Detailed query traces are
written to `data/evaluations/odoo-lexical-v1-results.json` and the summary to the corresponding Markdown file.
These measurements qualify this corpus and test set only; broader paraphrase, negative-constraint and version-pair
tests are required before production certification.

Run `tests/sql/005_scoped_lexical_retrieval_test.sql` to verify result limits, empty-query behavior and citation
integrity at the database contract boundary.

## pgvector embeddings

An embedding is a fixed-length numeric representation of text meaning. Similar questions and passages should have
nearby vectors even when they use different words. pgvector stores these vectors inside PostgreSQL and supports
cosine nearest-neighbor search while the relational database continues to enforce scope, version and provenance.

Migration `007_e5_passage_embeddings.sql` provides a typed 384-dimension table and cosine HNSW index. The local
CPU worker uses `intfloat/multilingual-e5-small` at exact revision
`919cfbe11fbf4f1b9bb321007c46c14feaf84227` through ONNX Runtime. It stores the model revision and prefixed-input
hash with every vector. Model files remain in a persistent Docker cache and customer text is not sent to an
external inference API.

The verified corpus has 9,473 unique passage vectors: 2,783 for Odoo 17, 3,159 for Odoo 18 and 3,531 for
Odoo 19. Every supported passage has one current-model vector; all vectors have 384 dimensions and unit-length
normalization. Repeated embedding runs insert zero duplicate rows. Set `ODOO_VERSION` to the target version and
generate missing embeddings with:

```powershell
docker compose --env-file .env -f compose\foundation.yml -f compose\embedding.yml run --build --rm embedding-worker
```

These vectors are indexed but are not yet enabled for agent answers. Vector and hybrid retrieval must pass the
versioned evaluation gate before they become an approved retrieval path.

## Production boundary

Production should use managed PostgreSQL and S3-compatible object storage where possible, private networking, TLS, a secrets manager, least-privilege application identities, automated encrypted backups, restore tests, monitoring and documented recovery objectives. This Compose file is the local development analogue of that architecture.
# AX-Fabric
