from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import psycopg

MAX_MANIFEST_BYTES = 1_000_000
BUCKET = "odoo-source-snapshots"
EXTRACTOR_NAME = "odoo-manifest-ingestor"
EXTRACTOR_VERSION = "0.2.0"


def parse_manifest(data: bytes) -> dict:
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds size limit")
    value = ast.literal_eval(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("manifest root must be a dictionary")
    dependencies = value.get("depends", [])
    if not isinstance(dependencies, list) or not all(isinstance(v, str) for v in dependencies):
        raise ValueError("depends must be a list of strings")
    return value


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def main() -> int:
    source_root = Path(required("ODOO_SOURCE_ROOT")).resolve()
    source_revision = required("ODOO_SOURCE_REVISION")
    odoo_version = os.environ.get("ODOO_VERSION", "19.0")
    manifests = sorted(source_root.rglob("__manifest__.py"))
    if not manifests:
        raise RuntimeError(f"no manifests found under {source_root}")

    s3 = boto3.client(
        "s3",
        endpoint_url=required("S3_ENDPOINT"),
        aws_access_key_id=required("S3_ACCESS_KEY"),
        aws_secret_access_key=required("S3_SECRET_KEY"),
        region_name="us-east-1",
    )
    dsn = required("DATABASE_URL")
    now = datetime.now(timezone.utc)

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO governance.data_placements
                   (region, database_alias, object_namespace, security_tier, status)
                   VALUES ('local', 'ax-postgres', 'odoo-public', 'development', 'active')
                   ON CONFLICT (database_alias, object_namespace) DO UPDATE SET status='active'
                   RETURNING placement_id"""
            )
            placement_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO governance.rights_policies
                   (policy_version, allowed_purposes, provider_processing_allowed, redistribution_allowed)
                   VALUES ('odoo-community-source-v1', %s, TRUE, FALSE)
                   ON CONFLICT (policy_version) DO UPDATE
                   SET allowed_purposes=EXCLUDED.allowed_purposes
                   RETURNING rights_policy_id""",
                (json.dumps(["product-knowledge", "retrieval", "verification"]),),
            )
            rights_policy_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO governance.retention_policies
                   (policy_version, class_code, retain_rule, backup_expiry_rule, owner_ref)
                   VALUES ('odoo-public-source-v1', 'public-product-source', %s, %s, 'AX Fabric knowledge owner')
                   ON CONFLICT (policy_version, class_code) DO UPDATE SET owner_ref=EXCLUDED.owner_ref
                   RETURNING retention_policy_id""",
                (json.dumps({"mode": "retain-while-supported"}), json.dumps({"mode": "follow-backup-policy"})),
            )
            retention_policy_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO knowledge.scopes
                   (scope_key, tenant_id, scope_kind, classification, policy_ref, placement_id, status)
                   VALUES ('product:odoo:community', NULL, 'product', 'public',
                           'odoo-community-source-v1', %s, 'active')
                   ON CONFLICT (scope_key) WHERE scope_key IS NOT NULL
                   DO UPDATE SET status='active' RETURNING scope_id""",
                (placement_id,),
            )
            scope_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO source_registry.sources
                   (source_type, authority_tier, canonical_uri, publisher, access_class)
                   VALUES ('official_source', 1, %s, 'Odoo S.A.', 'public')
                   ON CONFLICT (canonical_uri, access_class)
                   DO UPDATE SET enabled = TRUE RETURNING source_id""",
                ("https://github.com/odoo/odoo",),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO odoo.releases (version, release_channel, source_revision, support_status, last_verified_at)
                   VALUES (%s, 'major', %s, 'supported', %s)
                   ON CONFLICT (version, release_channel, COALESCE(source_revision, ''))
                   DO UPDATE SET last_verified_at = EXCLUDED.last_verified_at
                   RETURNING release_id""",
                (odoo_version, source_revision, now),
            )
            release_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO ingestion.runs
                   (source_id, extractor_name, extractor_version, status, started_at)
                   VALUES (%s, %s, %s, 'running', %s)
                   RETURNING run_id""",
                (source_id, EXTRACTOR_NAME, EXTRACTOR_VERSION, now),
            )
            run_id = cur.fetchone()[0]
            canonical_run_key = f"community:{odoo_version}:{source_revision}:manifest-capture"
            config_hash = hashlib.sha256(json.dumps({
                "extractor": EXTRACTOR_NAME,
                "extractor_version": EXTRACTOR_VERSION,
                "odoo_version": odoo_version,
                "source_revision": source_revision,
            }, sort_keys=True).encode()).hexdigest()
            cur.execute(
                """INSERT INTO ingestion.processing_runs
                   (scope_id, run_kind, idempotency_key, processor, processor_version,
                    config_hash, state, started_at)
                   VALUES (%s,'capture',%s,%s,%s,%s,'running',%s)
                   ON CONFLICT (scope_id, run_kind, idempotency_key) DO UPDATE SET
                     processor=EXCLUDED.processor, processor_version=EXCLUDED.processor_version,
                     config_hash=EXCLUDED.config_hash, state='running', started_at=EXCLUDED.started_at,
                     finished_at=NULL, error_code=NULL
                   RETURNING run_id""",
                (scope_id, canonical_run_key, EXTRACTOR_NAME, EXTRACTOR_VERSION, config_hash, now),
            )
            canonical_run_id = cur.fetchone()[0]
        conn.commit()

        processed = failed = 0
        for path in manifests:
            relative = path.relative_to(source_root).as_posix()
            try:
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                manifest = parse_manifest(data)
                module_name = path.parent.name
                object_key = f"odoo/community/{odoo_version}/{source_revision}/manifests/{relative}"
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT artifact_id FROM source_registry.artifacts
                           WHERE scope_id=%s AND bucket=%s AND object_key=%s AND sha256=%s
                             AND state='available' ORDER BY created_at DESC LIMIT 1""",
                        (scope_id, BUCKET, object_key, digest),
                    )
                    artifact_row = cur.fetchone()
                provider_version = None
                if artifact_row is None:
                    put_result = s3.put_object(
                        Bucket=BUCKET, Key=object_key, Body=data,
                        ContentType="text/x-python",
                        Metadata={"sha256": digest, "source-revision": source_revision},
                    )
                    provider_version = put_result.get("VersionId") or f"unversioned:{digest}"
                with conn.transaction():
                    with conn.cursor() as cur:
                        if artifact_row is None:
                            cur.execute(
                                """INSERT INTO source_registry.artifacts
                                   (scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,
                                    media_type,region,encryption_key_ref,classification,acl_policy_ref,
                                    rights_policy_id,retention_policy_id,state,verified_at)
                                   VALUES (%s,'minio',%s,%s,%s,%s,%s,'text/x-python','local',
                                           'development-storage-policy','public','odoo-public',%s,%s,'available',%s)
                                   RETURNING artifact_id""",
                                (scope_id, BUCKET, object_key, provider_version, digest, len(data),
                                 rights_policy_id, retention_policy_id, now),
                            )
                            artifact_id = cur.fetchone()[0]
                        else:
                            artifact_id = artifact_row[0]
                        cur.execute(
                            """INSERT INTO ingestion.run_outputs (scope_id,run_id,artifact_id,role_code)
                               VALUES (%s,%s,%s,'raw-manifest') ON CONFLICT DO NOTHING""",
                            (scope_id, canonical_run_id, artifact_id),
                        )
                        cur.execute(
                            """INSERT INTO source_registry.source_versions
                               (scope_id,source_id,raw_artifact_id,capture_run_id,upstream_version,
                                captured_at,capture_manifest,completeness)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,'complete')
                               ON CONFLICT (scope_id,source_id,raw_artifact_id,capture_run_id)
                               DO UPDATE SET capture_manifest=EXCLUDED.capture_manifest
                               RETURNING source_version_id""",
                            (scope_id, source_id, artifact_id, canonical_run_id, source_revision, now,
                             json.dumps({
                                 "source_kind": "git-repository",
                                 "repository": "https://github.com/odoo/odoo",
                                 "revision": source_revision,
                                 "relative_path": relative,
                                 "product_version": odoo_version,
                             })),
                        )
                        source_version_id = cur.fetchone()[0]
                        cur.execute(
                            """INSERT INTO source_registry.snapshots
                               (source_id, run_id, object_bucket, object_key, content_sha256,
                                media_type, source_revision, product_version, retrieved_at, metadata)
                               VALUES (%s,%s,%s,%s,%s,'text/x-python',%s,%s,%s,%s)
                               ON CONFLICT (object_bucket, object_key) DO UPDATE
                               SET run_id=EXCLUDED.run_id, content_sha256=EXCLUDED.content_sha256,
                                   retrieved_at=EXCLUDED.retrieved_at, metadata=EXCLUDED.metadata
                               RETURNING snapshot_id""",
                            (source_id, run_id, BUCKET, object_key, digest, source_revision,
                             odoo_version, now, json.dumps({
                                 "relative_path": relative,
                                 "artifact_id": str(artifact_id),
                                 "source_version_id": str(source_version_id),
                             })),
                        )
                        snapshot_id = cur.fetchone()[0]
                        cur.execute(
                            """INSERT INTO odoo.modules (technical_name, origin, publisher)
                               VALUES (%s,'official_community','Odoo S.A.')
                               ON CONFLICT (technical_name, origin, publisher) DO UPDATE
                               SET technical_name=EXCLUDED.technical_name RETURNING module_id""",
                            (module_name,),
                        )
                        module_id = cur.fetchone()[0]
                        cur.execute(
                            """INSERT INTO odoo.module_releases
                               (module_id, release_id, manifest_version, display_name, category, summary,
                                license, source_revision, manifest_sha256, installable, application, metadata)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (module_id, release_id, source_revision) DO UPDATE SET
                                 manifest_version=EXCLUDED.manifest_version, display_name=EXCLUDED.display_name,
                                 category=EXCLUDED.category, summary=EXCLUDED.summary, license=EXCLUDED.license,
                                 manifest_sha256=EXCLUDED.manifest_sha256, installable=EXCLUDED.installable,
                                 application=EXCLUDED.application, metadata=EXCLUDED.metadata
                               RETURNING module_release_id""",
                            (module_id, release_id, str(manifest.get("version", "")),
                             str(manifest.get("name", module_name)), manifest.get("category"),
                             manifest.get("summary"), manifest.get("license", "LGPL-3"),
                             source_revision, digest, manifest.get("installable", True),
                             manifest.get("application", False), json.dumps({"snapshot_id": str(snapshot_id)})),
                        )
                        module_release_id = cur.fetchone()[0]
                        cur.execute("DELETE FROM odoo.module_dependencies WHERE module_release_id=%s AND dependency_type='required'", (module_release_id,))
                        for dependency in manifest.get("depends", []):
                            cur.execute(
                                """INSERT INTO odoo.module_dependencies
                                   (module_release_id, dependency_name, dependency_type, source_snapshot_id)
                                   VALUES (%s,%s,'required',%s)
                                   ON CONFLICT DO NOTHING""",
                                (module_release_id, dependency, snapshot_id),
                            )
                        cur.execute(
                            """INSERT INTO ingestion.items (run_id, source_key, status, content_sha256, attempts, processed_at)
                               VALUES (%s,%s,'processed',%s,1,%s)""",
                            (run_id, relative, digest, now),
                        )
                processed += 1
            except Exception as exc:
                conn.rollback()
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO ingestion.items
                               (run_id, source_key, status, attempts, error_code, error_detail, processed_at)
                               VALUES (%s,%s,'failed',1,%s,%s,%s)""",
                            (run_id, relative, type(exc).__name__, str(exc)[:2000], now),
                        )
                        cur.execute(
                            """INSERT INTO ingestion.ingest_issues
                               (run_id,code,severity,locator,message,state)
                               VALUES (%s,%s,'blocking',%s,%s,'open')""",
                            (canonical_run_id, type(exc).__name__, json.dumps({"relative_path": relative}),
                             str(exc)[:2000]),
                        )
                failed += 1

        status = "succeeded" if failed == 0 else "partial"
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE ingestion.runs SET status=%s, finished_at=%s, statistics=%s WHERE run_id=%s""",
                (status, datetime.now(timezone.utc), json.dumps({"processed": processed, "failed": failed}), run_id),
            )
            cur.execute(
                """UPDATE ingestion.processing_runs
                   SET state=%s, finished_at=%s, metrics=%s, error_code=%s WHERE run_id=%s""",
                (status, datetime.now(timezone.utc), json.dumps({"processed": processed, "failed": failed}),
                 None if failed == 0 else "manifest_failures", canonical_run_id),
            )
        conn.commit()
    print(json.dumps({"run_id": str(run_id), "processed": processed, "failed": failed, "status": status}))
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
