from __future__ import annotations
import json,os
from datetime import timedelta
import psycopg
RULES=[
 ('online.non_standard_apps.unsupported','deployment','odoo_online','supports_non_standard_apps',False,'Odoo Online is not compatible with non-standard apps',{},['19.0'],['community','enterprise'],['online'],7),
 ('api.external.custom_only','subscription_plan','custom','external_api_available',True,'Access to data via the external API is only available on Custom Odoo pricing plans',{},['19.0'],['enterprise'],['online','odoo_sh','on_premise'],7),
 ('api.external.free_standard_unavailable','subscription_plan','one_app_free_standard','external_api_available',False,'Access to the external API is not available on One App Free or Standard plans',{},['19.0'],['enterprise'],['online'],7),
 ('standard.online.no_custom_modules','subscription_plan','standard','custom_modules_supported',False,'The standard plan is hosted on Odoo Online, our cloud infrastructure to host databases without custom modules',{},['current'],['enterprise'],['online'],7),
 ('custom.plan.use_cases','subscription_plan','custom','supports_advanced_customization',True,'The custom plan is for companies that want to manage multiple companies on a single database and that need to customize Odoo through Odoo Studio, custom developments or through the API',{},['current'],['enterprise'],['online','odoo_sh','on_premise'],7),
 ('einvoice.format.customer_country','capability','electronic_invoicing','default_format_depends_on_customer_country',True,'By default, the format available in the send window depends on the customer’s country',{},['19.0'],['community','enterprise'],['online','odoo_sh','on_premise'],30),
 ('localization.company_specific','capability','fiscal_localization','country_specific',True,'Fiscal localizations are country-specific modules',{},['19.0'],['community','enterprise'],['online','odoo_sh','on_premise'],30),
 ('payroll.localization.country_specific','capability','payroll_localization','country_specific',True,'Payroll localizations refer to the specific process of adapting payroll systems',{},['19.0'],['enterprise'],['online','odoo_sh','on_premise'],30)
]
def main():
 created=0; linked=0
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur:
   for key,stype,sid,predicate,value,needle,plan,versions,editions,deployments,days in RULES:
    cur.execute("""SELECT b.block_id,w.capture_id,w.retrieved_at FROM source_registry.web_document_blocks b
    JOIN source_registry.web_documents d USING(document_id) JOIN source_registry.web_captures w USING(capture_id)
    WHERE b.content ILIKE %s ORDER BY length(b.content),b.ordinal LIMIT 1""",('%'+needle+'%',)); evidence=cur.fetchone()
    if not evidence: continue
    block,capture,retrieved=evidence
    cur.execute("""INSERT INTO knowledge.claims(claim_key,subject_type,subject_id,predicate,object_value,evidence_status,confidence_score,version_scope,edition_scope,deployment_scope,plan_scope,localization_scope,valid_from,valid_to,last_verified_at,publication_status)
    VALUES(%s,%s,%s,%s,%s,'documented',90,%s,%s,%s,%s,'{}',%s,%s,%s,'draft') ON CONFLICT(claim_key) WHERE claim_key IS NOT NULL DO UPDATE
    SET object_value=EXCLUDED.object_value,last_verified_at=EXCLUDED.last_verified_at,valid_to=EXCLUDED.valid_to,evidence_status='documented',publication_status='draft' RETURNING claim_id""",
    (key,stype,sid,predicate,json.dumps(value),json.dumps(versions),json.dumps(editions),json.dumps(deployments),json.dumps(plan),retrieved,retrieved+timedelta(days=days),retrieved)); claim=cur.fetchone()[0]; created+=1
    cur.execute("INSERT INTO knowledge.web_claim_evidence(claim_id,block_id,evidence_role,source_capture_id) VALUES(%s,%s,'supports',%s) ON CONFLICT DO NOTHING",(claim,block,capture)); linked+=cur.rowcount
  conn.commit()
 print(json.dumps({'rules':len(RULES),'claims_upserted':created,'evidence_links_added':linked,'publication_status':'draft'}))
if __name__=='__main__': main()
