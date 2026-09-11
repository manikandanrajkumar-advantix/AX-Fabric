from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import psycopg


ASSESSMENT_VERSION = "oca-preliminary-v1"
SUPPORTED_LICENSES = {"AGPL-3", "LGPL-3", "GPL-3"}


def normalize_license(value: str | None) -> str | None:
    if not value:
        return None
    aliases = {
        "AGPL-3.0": "AGPL-3",
        "AGPL-3.0-only": "AGPL-3",
        "AGPL-3.0-or-later": "AGPL-3",
        "LGPL-3.0": "LGPL-3",
        "LGPL-3.0-only": "LGPL-3",
        "LGPL-3.0-or-later": "LGPL-3",
        "GPL-3.0": "GPL-3",
        "GPL-3.0-only": "GPL-3",
        "GPL-3.0-or-later": "GPL-3",
    }
    return aliases.get(value, value)


def maintenance_status(pushed_at, assessed_at):
    if pushed_at is None:
        return "unknown", 10, "Repository push date is unavailable"
    days = (assessed_at - pushed_at).days
    if days <= 180:
        return "active_180d", 0, None
    if days <= 365:
        return "active_365d", 0, None
    if days <= 730:
        return "aging_730d", 5, f"Repository has not been pushed for {days} days"
    return "stale", 15, f"Repository has not been pushed for {days} days"


def main():
    assessed_at = datetime.now(timezone.utc)
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT scope_id FROM knowledge.scopes WHERE scope_key='product:odoo:oca'")
            scope_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO oca.assessment_runs(scope_id,assessment_version,started_at,status) "
                "VALUES(%s,%s,%s,'running') RETURNING run_id",
                (scope_id, ASSESSMENT_VERSION, assessed_at),
            )
            run_id = cur.fetchone()[0]
            cur.execute(
                """
                SELECT mm.module_release_id, mm.repository_id, mm.branch_name,
                       mr.license, r.license_spdx, r.pushed_at,
                       COALESCE(array_agg(md.dependency_name ORDER BY md.dependency_name)
                           FILTER (WHERE md.dependency_name IS NOT NULL), ARRAY[]::text[]) dependencies,
                       rel.release_id
                FROM oca.module_manifests mm
                JOIN odoo.module_releases mr USING(module_release_id)
                JOIN odoo.releases rel USING(release_id)
                JOIN oca.repositories r USING(repository_id)
                LEFT JOIN odoo.module_dependencies md USING(module_release_id)
                GROUP BY mm.module_release_id, mm.repository_id, mm.branch_name,
                         mr.license, r.license_spdx, r.pushed_at, rel.release_id
                ORDER BY mm.repository_id, mm.branch_name, mm.module_release_id
                """
            )
            rows = cur.fetchall()

        counts = {"low": 0, "medium": 0, "high": 0}
        for module_release_id, repository_id, branch, manifest_raw, repo_raw, pushed_at, dependencies, release_id in rows:
            manifest_license = normalize_license(manifest_raw)
            repository_license = normalize_license(repo_raw)
            reasons = []
            score = 0

            if manifest_license is None:
                license_status = "missing"
                score += 40
                reasons.append("Module manifest does not declare a license")
            elif manifest_license not in SUPPORTED_LICENSES:
                license_status = "unsupported"
                score += 35
                reasons.append(f"Manifest license {manifest_license} requires policy review")
            elif repository_license in (None, "NOASSERTION"):
                license_status = "manifest_only"
                reasons.append("Repository-level license is unavailable; manifest license is present")
            elif repository_license == manifest_license:
                license_status = "consistent"
            else:
                license_status = "module_override"
                score += 5
                reasons.append(
                    f"Manifest license {manifest_license} differs from repository metadata {repository_license}"
                )

            maintenance, maintenance_score, maintenance_reason = maintenance_status(pushed_at, assessed_at)
            score += maintenance_score
            if maintenance_reason:
                reasons.append(maintenance_reason)

            unresolved = []
            with conn.cursor() as cur:
                for dependency in dependencies:
                    cur.execute(
                        """
                        SELECT EXISTS(
                            SELECT 1
                            FROM odoo.modules m
                            JOIN odoo.module_releases candidate USING(module_id)
                            WHERE m.technical_name=%s
                              AND candidate.release_id=%s
                              AND COALESCE(candidate.installable, TRUE)
                        )
                        """,
                        (dependency, release_id),
                    )
                    if not cur.fetchone()[0]:
                        unresolved.append(dependency)
            if unresolved:
                score += min(30, len(unresolved) * 5)
                reasons.append(f"{len(unresolved)} declared dependencies are not present in the current catalog")

            score = min(score, 100)
            risk_level = "high" if score >= 40 else "medium" if score >= 10 else "low"
            counts[risk_level] += 1
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO oca.module_assessments(
                        module_release_id,repository_id,branch_name,run_id,assessed_at,assessment_version,
                        manifest_license,repository_license,license_status,maintenance_status,
                        declared_dependency_count,unresolved_dependency_count,unresolved_dependencies,
                        risk_score,risk_level,review_status,reasons)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'quarantined',%s)
                    ON CONFLICT(module_release_id) DO UPDATE SET
                        repository_id=EXCLUDED.repository_id,branch_name=EXCLUDED.branch_name,
                        run_id=EXCLUDED.run_id,assessed_at=EXCLUDED.assessed_at,
                        assessment_version=EXCLUDED.assessment_version,
                        manifest_license=EXCLUDED.manifest_license,repository_license=EXCLUDED.repository_license,
                        license_status=EXCLUDED.license_status,maintenance_status=EXCLUDED.maintenance_status,
                        declared_dependency_count=EXCLUDED.declared_dependency_count,
                        unresolved_dependency_count=EXCLUDED.unresolved_dependency_count,
                        unresolved_dependencies=EXCLUDED.unresolved_dependencies,
                        risk_score=EXCLUDED.risk_score,risk_level=EXCLUDED.risk_level,
                        review_status='quarantined',reasons=EXCLUDED.reasons
                    """,
                    (module_release_id, repository_id, branch, run_id, assessed_at, ASSESSMENT_VERSION,
                     manifest_license, repository_license, license_status, maintenance,
                     len(dependencies), len(unresolved), json.dumps(unresolved), score, risk_level,
                     json.dumps(reasons)),
                )

        finished_at = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE oca.assessment_runs SET finished_at=%s,status='succeeded',modules_assessed=%s,
                    low_risk=%s,medium_risk=%s,high_risk=%s WHERE run_id=%s
                """,
                (finished_at, len(rows), counts["low"], counts["medium"], counts["high"], run_id),
            )
        conn.commit()
    print(json.dumps({"run_id": str(run_id), "assessment_version": ASSESSMENT_VERSION,
                      "modules_assessed": len(rows), **counts}))


if __name__ == "__main__":
    main()
