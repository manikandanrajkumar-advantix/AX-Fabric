from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import psycopg

RAW_BUCKET = "odoo-source-snapshots"
NORMALIZED_BUCKET = "odoo-normalized-data"
PROCESSOR = "odoo-rst-document-ingestor"
PROCESSOR_VERSION = "0.1.0"
MAX_FILE_BYTES = 5_000_000
PASSAGE_CHAR_LIMIT = 3500
HEADING_CHARS = set("=-~^\"`:+*#<>_")


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_rst(data: bytes) -> list[dict]:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("documentation file exceeds size limit")
    text = data.decode("utf-8-sig")
    lines = text.splitlines()
    blocks: list[dict] = []
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        if i + 1 < len(lines):
            marker = lines[i + 1].strip()
            if marker and len(set(marker)) == 1 and marker[0] in HEADING_CHARS and len(marker) >= len(lines[i].strip()):
                blocks.append({
                    "kind": "heading", "text": lines[i], "line_start": i + 1, "line_end": i + 2,
                    "structure": {"adornment": marker[0]},
                })
                i += 2
                continue
        start = i
        while i < len(lines) and lines[i].strip():
            if i > start and i + 1 < len(lines):
                marker = lines[i + 1].strip()
                if marker and len(set(marker)) == 1 and marker[0] in HEADING_CHARS and len(marker) >= len(lines[i].strip()):
                    break
            i += 1
        content = "\n".join(lines[start:i])
        stripped = content.lstrip()
        kind = "paragraph"
        if stripped.startswith(".. code-block::") or stripped.startswith(".. literalinclude::"):
            kind = "code"
        elif stripped.startswith(".. list-table::") or stripped.startswith(".. csv-table::"):
            kind = "table"
        blocks.append({
            "kind": kind, "text": content, "line_start": start + 1, "line_end": i,
            "structure": {},
        })
    return blocks


def build_passages(blocks: list[dict]) -> list[dict]:
    passages: list[dict] = []
    current: list[tuple[int, dict]] = []
    current_size = 0
    for index, block in enumerate(blocks):
        addition = len(block["text"]) + (2 if current else 0)
        if current and current_size + addition > PASSAGE_CHAR_LIMIT:
            passages.append(make_passage(current))
            current = []
            current_size = 0
        current.append((index, block))
        current_size += len(block["text"]) + (2 if len(current) > 1 else 0)
    if current:
        passages.append(make_passage(current))
    return passages


def make_passage(items: list[tuple[int, dict]]) -> dict:
    text_parts: list[str] = []
    spans: list[dict] = []
    offset = 0
    for block_index, block in items:
        if text_parts:
            text_parts.append("\n\n")
            offset += 2
        content = block["text"]
        text_parts.append(content)
        spans.append({"block_index": block_index, "start": offset, "end": offset + len(content)})
        offset += len(content)
    return {"text": "".join(text_parts), "spans": spans}


def upsert_artifact(cur, s3, *, scope_id, bucket, key, data, media_type, rights_id, retention_id, now):
    sha = digest(data)
    cur.execute(
        """SELECT artifact_id FROM source_registry.artifacts
           WHERE scope_id=%s AND bucket=%s AND object_key=%s AND sha256=%s AND state='available'
           ORDER BY created_at DESC LIMIT 1""",
        (scope_id, bucket, key, sha),
    )
    row = cur.fetchone()
    if row:
        return row[0], sha
    result = s3.put_object(
        Bucket=bucket, Key=key, Body=data, ContentType=media_type,
        Metadata={"sha256": sha},
    )
    version = result.get("VersionId") or f"unversioned:{sha}"
    cur.execute(
        """INSERT INTO source_registry.artifacts
           (scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,
            encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at)
           VALUES (%s,'minio',%s,%s,%s,%s,%s,%s,'local','development-storage-policy','public',
                   'odoo-documentation-public',%s,%s,'available',%s) RETURNING artifact_id""",
        (scope_id, bucket, key, version, sha, len(data), media_type, rights_id, retention_id, now),
    )
    return cur.fetchone()[0], sha


def main() -> int:
    source_root = Path(required("ODOO_DOCUMENTATION_ROOT")).resolve()
    revision = required("ODOO_DOCUMENTATION_REVISION")
    odoo_version = os.environ.get("ODOO_VERSION", "19.0")
    files = sorted(source_root.rglob("*.rst"))
    if not files:
        raise RuntimeError(f"no RST files found under {source_root}")
    s3 = boto3.client(
        "s3", endpoint_url=required("S3_ENDPOINT"), aws_access_key_id=required("S3_ACCESS_KEY"),
        aws_secret_access_key=required("S3_SECRET_KEY"), region_name="us-east-1",
    )
    now = datetime.now(timezone.utc)
    schema_body = json.dumps({
        "name": "axfabric.normalized-rst", "version": "1",
        "block_fields": ["kind", "text", "line_start", "line_end", "structure"],
    }, sort_keys=True, separators=(",", ":")).encode()
    config_hash = digest(json.dumps({
        "processor": PROCESSOR, "version": PROCESSOR_VERSION,
        "passage_char_limit": PASSAGE_CHAR_LIMIT, "revision": revision,
    }, sort_keys=True).encode())

    with psycopg.connect(required("DATABASE_URL")) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO governance.data_placements
                (region,database_alias,object_namespace,security_tier,status)
                VALUES('local','ax-postgres','odoo-documentation','development','active')
                ON CONFLICT(database_alias,object_namespace) DO UPDATE SET status='active'
                RETURNING placement_id""")
            placement_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO governance.rights_policies
                (policy_version,allowed_purposes,provider_processing_allowed,redistribution_allowed)
                VALUES('odoo-documentation-cc-by-sa-4.0-v1',%s,TRUE,TRUE)
                ON CONFLICT(policy_version) DO UPDATE SET allowed_purposes=EXCLUDED.allowed_purposes
                RETURNING rights_policy_id""", (json.dumps(["product-knowledge","retrieval","citation"]),))
            rights_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO governance.retention_policies
                (policy_version,class_code,retain_rule,backup_expiry_rule,owner_ref)
                VALUES('odoo-documentation-v1','public-product-documentation',%s,%s,'AX Fabric knowledge owner')
                ON CONFLICT(policy_version,class_code) DO UPDATE SET owner_ref=EXCLUDED.owner_ref
                RETURNING retention_policy_id""",
                (json.dumps({"mode":"retain-pinned-revisions"}), json.dumps({"mode":"follow-backup-policy"})))
            retention_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO knowledge.scopes
                (scope_key,scope_kind,classification,policy_ref,placement_id,status)
                VALUES(%s,'product','public','odoo-documentation-cc-by-sa-4.0-v1',%s,'active')
                ON CONFLICT(scope_key) WHERE scope_key IS NOT NULL DO UPDATE SET status='active'
                RETURNING scope_id""", (f"product:odoo:documentation:{odoo_version}", placement_id))
            scope_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO source_registry.sources
                (source_type,authority_tier,canonical_uri,publisher,access_class)
                VALUES('official_documentation',1,'https://github.com/odoo/documentation','Odoo S.A.','public')
                ON CONFLICT(canonical_uri,access_class) DO UPDATE SET enabled=TRUE RETURNING source_id""")
            source_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO knowledge.schema_contracts
                (name,version,schema_hash,schema_uri,validator_version,state)
                VALUES('axfabric.normalized-rst','1',%s,'internal://schemas/normalized-rst/v1',%s,'active')
                ON CONFLICT(name,version) DO UPDATE SET validator_version=EXCLUDED.validator_version RETURNING schema_id""",
                (digest(schema_body), PROCESSOR_VERSION))
            schema_id = cur.fetchone()[0]
            run_ids = {}
            for kind in ("capture", "parse"):
                cur.execute("""INSERT INTO ingestion.processing_runs
                    (scope_id,run_kind,idempotency_key,processor,processor_version,config_hash,state,started_at)
                    VALUES(%s,%s,%s,%s,%s,%s,'running',%s)
                    ON CONFLICT(scope_id,run_kind,idempotency_key) DO UPDATE SET state='running',started_at=EXCLUDED.started_at,
                      finished_at=NULL,error_code=NULL,config_hash=EXCLUDED.config_hash RETURNING run_id""",
                    (scope_id, kind, f"documentation:{odoo_version}:{revision}:{PROCESSOR_VERSION}:{kind}",
                     PROCESSOR, PROCESSOR_VERSION, config_hash, now))
                run_ids[kind] = cur.fetchone()[0]
        conn.commit()

        processed = skipped = failed = blocks_total = passages_total = 0
        for path in files:
            relative = path.relative_to(source_root).as_posix()
            try:
                raw = path.read_bytes()
                blocks = parse_rst(raw)
                passages = build_passages(blocks)
                normalized = json.dumps({
                    "schema": "axfabric.normalized-rst@1", "source_revision": revision,
                    "source_path": relative, "blocks": blocks,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                raw_key = f"odoo/documentation/{odoo_version}/{revision}/rst/{relative}"
                normalized_key = f"odoo/documentation/{odoo_version}/{revision}/normalized/{relative}.json"
                with conn.transaction():
                    with conn.cursor() as cur:
                        raw_artifact, raw_sha = upsert_artifact(
                            cur, s3, scope_id=scope_id, bucket=RAW_BUCKET, key=raw_key, data=raw,
                            media_type="text/x-rst", rights_id=rights_id, retention_id=retention_id, now=now)
                        cur.execute("""INSERT INTO ingestion.run_outputs(scope_id,run_id,artifact_id,role_code)
                            VALUES(%s,%s,%s,'raw-document') ON CONFLICT DO NOTHING""",
                            (scope_id, run_ids["capture"], raw_artifact))
                        cur.execute("""INSERT INTO source_registry.source_versions
                            (scope_id,source_id,raw_artifact_id,capture_run_id,upstream_version,captured_at,capture_manifest,completeness)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,'complete')
                            ON CONFLICT(scope_id,source_id,raw_artifact_id,capture_run_id)
                            DO UPDATE SET capture_manifest=EXCLUDED.capture_manifest RETURNING source_version_id""",
                            (scope_id, source_id, raw_artifact, run_ids["capture"], revision, now,
                             json.dumps({"repository":"https://github.com/odoo/documentation","revision":revision,
                                         "relative_path":relative,"branch":odoo_version})))
                        source_version = cur.fetchone()[0]
                        normalized_artifact, normalized_sha = upsert_artifact(
                            cur, s3, scope_id=scope_id, bucket=NORMALIZED_BUCKET, key=normalized_key,
                            data=normalized, media_type="application/json", rights_id=rights_id,
                            retention_id=retention_id, now=now)
                        cur.execute("""INSERT INTO ingestion.run_outputs(scope_id,run_id,artifact_id,role_code)
                            VALUES(%s,%s,%s,'normalized-document') ON CONFLICT DO NOTHING""",
                            (scope_id, run_ids["parse"], normalized_artifact))
                        cur.execute("""SELECT d.document_id FROM source_registry.normalized_documents d
                            JOIN source_registry.artifacts a ON a.artifact_id=d.normalized_artifact_id
                            WHERE d.source_version_id=%s AND d.extraction_run_id=%s AND a.sha256=%s""",
                            (source_version, run_ids["parse"], normalized_sha))
                        existing = cur.fetchone()
                        if existing:
                            skipped += 1
                            continue
                        cur.execute("""INSERT INTO source_registry.normalized_documents
                            (scope_id,source_version_id,extraction_run_id,normalized_artifact_id,format_schema_id,
                             language_code,generation,state)
                            VALUES(%s,%s,%s,%s,%s,'en',1,'validated') RETURNING document_id""",
                            (scope_id, source_version, run_ids["parse"], normalized_artifact, schema_id))
                        document_id = cur.fetchone()[0]
                        block_ids = []
                        for ordinal, block in enumerate(blocks):
                            locator = {"kind":"git-lines","path":relative,"revision":revision,
                                       "line_start":block["line_start"],"line_end":block["line_end"]}
                            text_hash = digest(block["text"].encode())
                            cur.execute("""INSERT INTO source_registry.document_blocks
                                (scope_id,document_id,sequence_no,block_kind,path,text_content,locator,structure,text_hash)
                                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING block_id""",
                                (scope_id, document_id, ordinal, block["kind"],
                                 f"/{relative}#L{block['line_start']}-L{block['line_end']}", block["text"],
                                 json.dumps(locator), json.dumps(block["structure"]), text_hash))
                            block_id = cur.fetchone()[0]
                            block_ids.append(block_id)
                            cur.execute("""INSERT INTO source_registry.evidence_spans
                                (scope_id,source_version_id,document_id,block_id,locator,quoted_text,quote_hash,verification)
                                VALUES(%s,%s,%s,%s,%s,%s,%s,'locator_verified')""",
                                (scope_id, source_version, document_id, block_id, json.dumps(locator), block["text"], text_hash))
                        for ordinal, passage in enumerate(passages):
                            passage_hash = digest(passage["text"].encode())
                            cur.execute("""INSERT INTO source_registry.passages
                                (scope_id,document_id,generation,sequence_no,content,text_hash,language_code,
                                 token_count,chunker_version,acl_policy_ref)
                                VALUES(%s,%s,1,%s,%s,%s,'en',%s,%s,'odoo-documentation-public') RETURNING passage_id""",
                                (scope_id, document_id, ordinal, passage["text"], passage_hash,
                                 len(passage["text"].split()), PROCESSOR_VERSION))
                            passage_id = cur.fetchone()[0]
                            for span_no, span in enumerate(passage["spans"]):
                                block = blocks[span["block_index"]]
                                cur.execute("""INSERT INTO source_registry.passage_blocks
                                    (passage_id,block_id,span_no,block_char_start,block_char_end,
                                     passage_char_start,passage_char_end)
                                    VALUES(%s,%s,%s,0,%s,%s,%s)""",
                                    (passage_id, block_ids[span["block_index"]], span_no, len(block["text"]),
                                     span["start"], span["end"]))
                        processed += 1
                        blocks_total += len(blocks)
                        passages_total += len(passages)
            except Exception as exc:
                conn.rollback()
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute("""INSERT INTO ingestion.ingest_issues
                            (run_id,code,severity,locator,message,state)
                            VALUES(%s,%s,'blocking',%s,%s,'open')""",
                            (run_ids["parse"], type(exc).__name__, json.dumps({"relative_path":relative}), str(exc)[:2000]))
                failed += 1

        state = "succeeded" if failed == 0 else "partial"
        metrics = {"processed":processed,"skipped":skipped,"failed":failed,
                   "blocks":blocks_total,"passages":passages_total,"files":len(files)}
        with conn.cursor() as cur:
            for run_id in run_ids.values():
                cur.execute("""UPDATE ingestion.processing_runs SET state=%s,finished_at=%s,metrics=%s,error_code=%s
                    WHERE run_id=%s""", (state, datetime.now(timezone.utc), json.dumps(metrics),
                                          None if failed == 0 else "document_failures", run_id))
        conn.commit()
    print(json.dumps({"revision":revision,"status":state,**metrics}))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
