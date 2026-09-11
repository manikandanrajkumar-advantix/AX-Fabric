BEGIN;

CREATE TABLE IF NOT EXISTS oca.assessment_runs (
    run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_id UUID NOT NULL REFERENCES knowledge.scopes(scope_id),
    assessment_version TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
    modules_assessed INTEGER NOT NULL DEFAULT 0,
    low_risk INTEGER NOT NULL DEFAULT 0,
    medium_risk INTEGER NOT NULL DEFAULT 0,
    high_risk INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oca.module_assessments (
    module_release_id UUID PRIMARY KEY REFERENCES odoo.module_releases(module_release_id) ON DELETE CASCADE,
    repository_id BIGINT NOT NULL REFERENCES oca.repositories(repository_id),
    branch_name TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES oca.assessment_runs(run_id),
    assessed_at TIMESTAMPTZ NOT NULL,
    assessment_version TEXT NOT NULL,
    manifest_license TEXT,
    repository_license TEXT,
    license_status TEXT NOT NULL CHECK (license_status IN ('consistent','module_override','manifest_only','missing','unsupported')),
    maintenance_status TEXT NOT NULL CHECK (maintenance_status IN ('active_180d','active_365d','aging_730d','stale','unknown')),
    declared_dependency_count INTEGER NOT NULL CHECK (declared_dependency_count >= 0),
    unresolved_dependency_count INTEGER NOT NULL CHECK (unresolved_dependency_count >= 0),
    unresolved_dependencies JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(unresolved_dependencies) = 'array'),
    risk_score INTEGER NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low','medium','high')),
    review_status TEXT NOT NULL DEFAULT 'quarantined' CHECK (review_status IN ('quarantined','approved','rejected')),
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(reasons) = 'array'),
    UNIQUE (repository_id, branch_name, module_release_id)
);

CREATE INDEX IF NOT EXISTS idx_oca_module_assessments_risk
    ON oca.module_assessments(review_status, risk_level, risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_oca_module_assessments_repository
    ON oca.module_assessments(repository_id, branch_name);

INSERT INTO platform.schema_migrations(migration_id,checksum)
VALUES('016_oca_preliminary_assessments','managed-by-repository')
ON CONFLICT DO NOTHING;

COMMIT;
