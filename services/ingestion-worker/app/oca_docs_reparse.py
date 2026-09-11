from __future__ import annotations
import hashlib,json,os
from datetime import datetime,timezone
import boto3,psycopg
from .oca_docs import NORMAL_BUCKET,PARSER,VERSION,parse_text
def sha(data): return hashlib.sha256(data).hexdigest()
def main():
 s3=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1'); updated=0
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur:
   cur.execute("""SELECT d.document_id,d.repository_id,d.branch_name,d.commit_sha,d.relative_path,d.document_type,
    r.full_name,r.scope_id,a.bucket,a.object_key,a.rights_policy_id,a.retention_policy_id
    FROM oca.documents d JOIN oca.repositories r USING(repository_id) JOIN source_registry.artifacts a ON a.artifact_id=d.raw_artifact_id
    WHERE d.parser_name<>%s OR d.parser_version<>%s ORDER BY d.document_id""",(PARSER,VERSION)); rows=cur.fetchall()
  for doc,rid,branch,commit,path,kind,name,scope,bucket,key,rights,retention in rows:
   raw=s3.get_object(Bucket=bucket,Key=key)['Body'].read(); text=raw.decode('utf-8-sig'); blocks=parse_text(text,path)
   normalized=json.dumps({'repository':name,'branch':branch,'commit':commit,'path':path,'type':kind,'parser':PARSER,'parser_version':VERSION,'blocks':blocks},ensure_ascii=False,separators=(',',':')).encode(); digest=sha(normalized); outkey=f'oca/docs/{rid}/{branch}/{commit}/{path}.{digest}.json'; put=s3.put_object(Bucket=NORMAL_BUCKET,Key=outkey,Body=normalized,ContentType='application/json',Metadata={'sha256':digest})
   with conn.cursor() as cur:
    cur.execute("""INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at)
    VALUES(%s,'minio',%s,%s,%s,%s,%s,'application/json','local','development-storage-policy','public','oca-review-required',%s,%s,'available',%s)
    ON CONFLICT(provider,bucket,object_key,provider_version) DO UPDATE SET verified_at=EXCLUDED.verified_at RETURNING artifact_id""",(scope,NORMAL_BUCKET,outkey,put.get('VersionId') or 'unversioned:'+digest,digest,len(normalized),rights,retention,datetime.now(timezone.utc))); artifact=cur.fetchone()[0]
    cur.execute("UPDATE oca.documents SET normalized_artifact_id=%s,parser_name=%s,parser_version=%s,state='quarantined' WHERE document_id=%s",(artifact,PARSER,VERSION,doc)); cur.execute('DELETE FROM oca.document_blocks WHERE document_id=%s',(doc,))
    cur.executemany("INSERT INTO oca.document_blocks(document_id,scope_id,ordinal,heading_path,content,locator,text_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)",[(doc,scope,i,b['heading_path'],b['content'],json.dumps({**b['locator'],'repository':name,'branch':branch,'commit':commit}),sha(b['content'].encode())) for i,b in enumerate(blocks)])
   conn.commit(); updated+=1
 print(json.dumps({'documents_reparsed':updated,'parser_version':VERSION,'status':'succeeded'}))
if __name__=='__main__': main()
