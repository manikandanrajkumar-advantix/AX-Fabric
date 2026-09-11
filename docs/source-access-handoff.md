# Odoo knowledge source completion and access handoff

Verified on: 2026-09-09

## Completeness definition

"Complete" means every file in a named, pinned source corpus was captured and processed successfully. It does not
mean that changing commercial terms, laws, the Odoo Apps Store, or the public internet are permanently complete.
Those sources require scheduled recapture and effective-dated records.

## Public source corpus completed by AX Fabric

| Corpus | Versions | Completeness evidence |
| --- | --- | --- |
| Official Odoo Community source manifests | 17.0, 18.0, 19.0 | 1,930 manifests processed; zero failures in final runs |
| Official Odoo documentation | 17.0, 18.0, 19.0 | 3,177/3,177 RST files processed; zero skipped or failed |
| Odoo Online and hosting documentation | 17.0, 18.0, 19.0 | Included in each pinned documentation checkout |
| Odoo.sh documentation and terms | 17.0, 18.0, 19.0 | Included in each pinned documentation checkout |
| Enterprise public terms and migration documentation | 17.0, 18.0, 19.0 | Included as public documentation; Enterprise source is excluded |
| Fiscal localization documentation | 17.0, 18.0, 19.0 | 37, 52 and 60 matching documentation files respectively |
| Payroll documentation | 17.0, 18.0, 19.0 | 8, 21 and 27 matching documentation files respectively |
| E-invoicing/EDI documentation | 17.0, 18.0, 19.0 | 7, 33 and 38 matching documentation files respectively |

The public corpus contains 3,177 documents, 168,432 structural blocks, 9,473 passages and 9,473 current-model
embeddings. Original and normalized artifacts are held in MinIO; structured lineage and vectors are held in
PostgreSQL/pgvector.

## Materials the AX Fabric owner must obtain

### A. Odoo Enterprise repository

Provide a repository checkout or organization-controlled read-only credential for `https://github.com/odoo/enterprise`
for branches 17.0, 18.0 and 19.0. For each branch record the commit hash, subscription/contract reference, authorized
purpose, access owner, access expiry and redistribution restriction. Do not send a personal access token in chat or
commit it to the repository; configure it in the approved secrets manager.

This unlocks the Enterprise-only manifest inventory and the evidence-complete Community-versus-Enterprise matrix.

### B. Current commercial terms

Obtain the applicable official quotation or plan schedule from Odoo, including region, currency, billing period,
plan names, API availability, Studio, hosting, support, upgrade service, multi-company conditions and effective date.
Provide the original PDF or saved official page plus the Odoo account manager's written clarification for ambiguous
terms. Public pages alone cannot prove the terms of a specific customer contract.

### C. Country compliance evidence

Choose the countries AX Fabric will support first. For each country obtain current primary material from the tax,
payroll and e-invoicing authority: official rule/guidance, effective date, schema or API specification, validation
tool, filing calendar and archival requirements. Assign a qualified local accountant/payroll specialist to approve
the interpretation. Odoo documentation is product evidence, not final legal validation.

### D. Commercial third-party applications

For each approved vendor provide the purchased source package or private repository access, invoice/license,
supported Odoo versions, support agreement, vendor security contact and permission for internal automated analysis.
Public Apps Store metadata supports discovery only and is insufficient for a code-quality or security assessment.

### E. Customer Odoo discovery

For each customer obtain written authorization, database URL, version, edition, deployment model, tenant identifier,
allowed models/fields, retention period and a dedicated read-only service account. Put credentials in the secrets
manager. Export only the approved configuration inventory; do not supply a full production database unless a separate
approved migration or incident workflow requires it.

Minimum allowlisted discovery targets are installed module metadata, company/localization metadata, languages,
currencies and explicitly approved non-secret configuration parameters. Roles require separate authorization for
users, groups, model access controls, record rules and company membership.

### F. Customer support evidence

Provide an authorized export from the ticketing/logging system with customer scope, retention policy and permitted
reuse. Redact credentials, tokens, cookies and unnecessary personal or business data before ingestion. Each reusable
resolution needs a verified Odoo version, trigger, error signature, root cause, remediation and validation result.

### G. Review and publication authorities

Nominate people or groups for functional, technical, security, localization/compliance and publication approval.
Define which knowledge types each authority may approve and their required review interval. Industry fit-gap reports
need both an Odoo functional reviewer and an industry subject-matter reviewer. Implementation recipes need execution
evidence from an isolated test environment before publication.

## Public sources that remain dynamic

OCA repositories, the Odoo Apps Store, pricing pages, regulations and Odoo SaaS behavior are moving catalogs. AX
Fabric should capture them with a scheduled job, retain every immutable snapshot, generate change reports and require
review before changed claims are republished. A date-bounded catalog can be complete; a one-time permanent catalog
cannot be guaranteed complete.

## Intake acceptance checklist

Every supplied source must include:

- owner and provider;
- access authorization and permitted purpose;
- Odoo version, edition and deployment applicability;
- country and legal effective date when applicable;
- original file, repository commit or API extraction timestamp;
- checksum and retrieval date;
- classification and retention policy;
- named reviewer and review status;
- redistribution restriction;
- expiry or next verification date.

Sources missing these fields remain quarantined and cannot be used for final agent recommendations.
