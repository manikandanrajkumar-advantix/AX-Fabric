from __future__ import annotations
import hashlib,io,json,os
from datetime import datetime,timezone
from bs4 import BeautifulSoup
from pypdf import PdfReader
import boto3,psycopg
BUCKET='odoo-normalized-data'; PARSER='odoo-web-normalizer'; VERSION='0.1.0'
def req(n):
 v=os.environ.get(n)
 if not v: raise RuntimeError(f'missing {n}')
 return v
def sha(b): return hashlib.sha256(b).hexdigest()
def parse_html(data,url):
 soup=BeautifulSoup(data,'html.parser')
 for tag in soup(['script','style','nav','footer','noscript','svg']): tag.decompose()
 title=(soup.title.get_text(' ',strip=True) if soup.title else url); blocks=[]; headings=[]
 for tag in soup.find_all(['h1','h2','h3','h4','p','li','pre','table']):
  text=' '.join(tag.get_text(' ',strip=True).split())
  if not text or len(text)<2: continue
  if tag.name.startswith('h'):
   level=int(tag.name[1]); headings=headings[:level-1]+[text]
  blocks.append({'kind':tag.name,'heading_path':headings.copy(),'content':text,'locator':{'uri':url,'element_ordinal':len(blocks)}})
 return title,blocks
def parse_pdf(data,url):
 reader=PdfReader(io.BytesIO(data)); blocks=[]; title=reader.metadata.title if reader.metadata and reader.metadata.title else url
 for page_no,page in enumerate(reader.pages,1):
  text='\n'.join(x.strip() for x in (page.extract_text() or '').splitlines() if x.strip())
  if text: blocks.append({'kind':'pdf_page','heading_path':[],'content':text,'locator':{'uri':url,'page':page_no}})
 return title,blocks
def main():
 s3=boto3.client('s3',endpoint_url=req('S3_ENDPOINT'),aws_access_key_id=req('S3_ACCESS_KEY'),aws_secret_access_key=req('S3_SECRET_KEY'),region_name='us-east-1'); done=0
 with psycopg.connect(req('DATABASE_URL')) as conn:
  with conn.cursor() as cur:
   cur.execute("""SELECT w.capture_id,w.scope_id,w.final_uri,w.media_type,a.bucket,a.object_key,a.rights_policy_id,a.retention_policy_id
   FROM source_registry.web_captures w JOIN source_registry.artifacts a USING(artifact_id)
   LEFT JOIN source_registry.web_documents d USING(capture_id) WHERE d.capture_id IS NULL ORDER BY w.retrieved_at"""); rows=cur.fetchall()
  for capture,scope,url,media,bucket,key,rights,retention in rows:
   raw=s3.get_object(Bucket=bucket,Key=key)['Body'].read(); title,blocks=parse_pdf(raw,url) if media=='application/pdf' else parse_html(raw,url)
   payload=json.dumps({'title':title,'source_uri':url,'parser':PARSER,'parser_version':VERSION,'blocks':blocks},ensure_ascii=False,separators=(',',':')).encode(); digest=sha(payload); outkey=f'web/{capture}/{digest}.json'
   put=s3.put_object(Bucket=BUCKET,Key=outkey,Body=payload,ContentType='application/json',Metadata={'sha256':digest})
   with conn.cursor() as cur:
    cur.execute("""INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at)
    VALUES(%s,'minio',%s,%s,%s,%s,%s,'application/json','local','development-storage-policy','public','odoo-public-web',%s,%s,'available',%s) RETURNING artifact_id""",(scope,BUCKET,outkey,put.get('VersionId') or f'unversioned:{digest}',digest,len(payload),rights,retention,datetime.now(timezone.utc))); artifact=cur.fetchone()[0]
    cur.execute("INSERT INTO source_registry.web_documents(capture_id,scope_id,normalized_artifact_id,title,parser_name,parser_version,content_sha256,state) VALUES(%s,%s,%s,%s,%s,%s,%s,'validated') RETURNING document_id",(capture,scope,artifact,title,PARSER,VERSION,digest)); doc=cur.fetchone()[0]
    cur.executemany("INSERT INTO source_registry.web_document_blocks(document_id,scope_id,ordinal,block_kind,heading_path,content,locator,text_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",[(doc,scope,i,b['kind'],b['heading_path'],b['content'],json.dumps(b['locator']),sha(b['content'].encode())) for i,b in enumerate(blocks)])
   conn.commit(); done+=1
 print(json.dumps({'normalized':done,'status':'succeeded'}))
if __name__=='__main__': main()
