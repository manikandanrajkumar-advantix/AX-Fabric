from __future__ import annotations
import hashlib,json,os,re,subprocess,tempfile,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import boto3,psycopg

RAW_BUCKET='odoo-source-snapshots'; NORMAL_BUCKET='odoo-normalized-data'; WORKERS=8; MAX_FILE=1_000_000
PARSER='oca-text-doc-parser'; VERSION='1.0.1'
ROOT_READMES={'README.md','README.rst'}; LICENSES={'LICENSE','LICENSE.md','LICENSE.txt','COPYING','COPYING.txt'}

def sha(data): return hashlib.sha256(data).hexdigest()
def classify(path,module_roots):
 if path in LICENSES: return 'repository_license',None
 if path in ROOT_READMES: return 'repository_readme',None
 parts=path.split('/'); root=parts[0] if len(parts)>1 else None
 if root not in module_roots: return None,None
 rel='/'.join(parts[1:]); lower=rel.lower()
 if lower.startswith('readme/') and lower.endswith(('.rst','.md','.txt')): return 'module_documentation',module_roots[root]
 if lower.startswith(('doc/','docs/')) and lower.endswith(('.rst','.md','.txt')): return 'module_documentation',module_roots[root]
 if lower in ('readme.rst','readme.md') and not module_roots[root][1]: return 'module_readme',module_roots[root]
 return None,None
def parse_text(text,path):
 lines=text.replace('\r\n','\n').replace('\r','\n').split('\n'); blocks=[]; headings=[]; paragraph=[]
 def flush(line_no):
  nonlocal paragraph
  content=' '.join(x.strip() for x in paragraph if x.strip())
  if content: blocks.append({'heading_path':headings.copy(),'content':content,'locator':{'path':path,'line':line_no-len(paragraph)+1}})
  paragraph=[]
 for i,line in enumerate(lines,1):
  md=re.match(r'^(#{1,6})\s+(.+?)\s*#*$',line.strip())
  rst=i<len(lines) and lines[i].strip() and set(lines[i].strip()) <= set('=-~^"`:+*#') and len(lines[i].strip())>=len(line.strip())
  underline=line.strip() and set(line.strip()) <= set('=-~^"`:+*#') and i>1 and lines[i-2].strip() and len(line.strip())>=len(lines[i-2].strip())
  if underline: continue
  if md:
   flush(i); level=len(md.group(1)); headings[:]=headings[:level-1]+[md.group(2).strip()]
  elif rst and line.strip():
   flush(i); level=1 if lines[i].strip()[0]=='=' else 2; headings[:]=headings[:level-1]+[line.strip()]
  elif not line.strip(): flush(i)
  else: paragraph.append(line)
 flush(len(lines)+1)
 return blocks
def scan(item):
 rid,name,url,branch,commit,module_rows=item; last='unknown git failure'
 for attempt in range(2):
  try:
   with tempfile.TemporaryDirectory() as td:
    subprocess.run(['git','clone','-q','--filter=blob:none','--no-checkout','--depth','1','--branch',branch,url,td],timeout=180,check=True,capture_output=True)
    actual=subprocess.run(['git','-C',td,'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    if actual!=commit: raise RuntimeError(f'branch moved: expected {commit}, received {actual}')
    paths=subprocess.run(['git','-C',td,'ls-tree','-r','--name-only',commit],check=True,capture_output=True,text=True).stdout.splitlines()
    roots={path.split('/')[0]:(mr,False) for path,mr in module_rows}
    for p in paths:
     parts=p.split('/')
     if len(parts)>2 and parts[0] in roots and parts[1].lower()=='readme' and p.lower().endswith(('.rst','.md','.txt')): roots[parts[0]]=(roots[parts[0]][0],True)
    docs=[]
    for path in paths:
     kind,module=classify(path,roots)
     if not kind: continue
     data=subprocess.run(['git','-C',td,'show',f'{commit}:{path}'],check=True,capture_output=True,timeout=30).stdout
     if len(data)>MAX_FILE: raise ValueError(f'{path} exceeds {MAX_FILE} bytes')
     text=data.decode('utf-8-sig'); blocks=parse_text(text,path)
     if blocks: docs.append((path,kind,module[0] if module else None,data,blocks))
    return item,docs,None
  except Exception as exc:
   last=str(exc)
   if attempt==0: time.sleep(1)
 return item,[],last
def main():
 now=datetime.now(timezone.utc); s3=boto3.client('s3',endpoint_url=os.environ['S3_ENDPOINT'],aws_access_key_id=os.environ['S3_ACCESS_KEY'],aws_secret_access_key=os.environ['S3_SECRET_KEY'],region_name='us-east-1')
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur:
   cur.execute("SELECT scope_id FROM knowledge.scopes WHERE scope_key='product:odoo:oca'"); scope=cur.fetchone()[0]
   cur.execute("SELECT rights_policy_id FROM governance.rights_policies WHERE policy_version='oca-public-metadata-v1'"); rights=cur.fetchone()[0]
   cur.execute("SELECT retention_policy_id FROM governance.retention_policies WHERE policy_version='oca-public-v1' AND class_code='public-catalog'"); retention=cur.fetchone()[0]
   cur.execute("""SELECT b.repository_id,r.full_name,r.clone_url,b.branch_name,b.commit_sha,
    array_agg(ARRAY[m.relative_path,m.module_release_id::text]) FROM oca.repository_branches b JOIN oca.repositories r USING(repository_id)
    JOIN oca.module_manifests m ON m.repository_id=b.repository_id AND m.branch_name=b.branch_name AND m.commit_sha=b.commit_sha
    WHERE NOT EXISTS(SELECT 1 FROM oca.documents d WHERE d.repository_id=b.repository_id AND d.branch_name=b.branch_name AND d.commit_sha=b.commit_sha)
    GROUP BY b.repository_id,r.full_name,r.clone_url,b.branch_name,b.commit_sha ORDER BY r.full_name,b.branch_name"""); raw=cur.fetchall()
   items=[]
   for rid,name,url,branch,commit,module_rows in raw: items.append((rid,name,url,branch,commit,[(x[0],x[1]) for x in module_rows]))
   cur.execute("INSERT INTO oca.documentation_scan_runs(scope_id,started_at,status,branches_attempted) VALUES(%s,%s,'running',%s) RETURNING run_id",(scope,now,len(items))); run=cur.fetchone()[0]
  conn.commit(); bok=bbad=docs_ok=docs_bad=0
  with ThreadPoolExecutor(max_workers=WORKERS) as pool:
   for future in as_completed([pool.submit(scan,x) for x in items]):
    item,docs,error=future.result(); rid,name,url,branch,commit,_=item
    if error:
     with conn.cursor() as cur: cur.execute("INSERT INTO oca.documentation_scan_issues(run_id,repository_id,branch_name,error_code,message) VALUES(%s,%s,%s,'branch_scan_failed',%s)",(run,rid,branch,error[:2000]))
     conn.commit(); bbad+=1; continue
    bok+=1
    for path,kind,mr,data,blocks in docs:
     try:
      digest=sha(data); raw_key=f'oca/docs/{rid}/{branch}/{commit}/{path}'; raw_put=s3.put_object(Bucket=RAW_BUCKET,Key=raw_key,Body=data,ContentType='text/plain',Metadata={'sha256':digest})
      normalized=json.dumps({'repository':name,'branch':branch,'commit':commit,'path':path,'type':kind,'parser':PARSER,'parser_version':VERSION,'blocks':blocks},ensure_ascii=False,separators=(',',':')).encode(); ndigest=sha(normalized); norm_key=f'oca/docs/{rid}/{branch}/{commit}/{path}.{ndigest}.json'; norm_put=s3.put_object(Bucket=NORMAL_BUCKET,Key=norm_key,Body=normalized,ContentType='application/json',Metadata={'sha256':ndigest})
      with conn.cursor() as cur:
       artifacts=[]
       for bucket,key,put,content,dig,media in [(RAW_BUCKET,raw_key,raw_put,data,digest,'text/plain'),(NORMAL_BUCKET,norm_key,norm_put,normalized,ndigest,'application/json')]:
        cur.execute("""INSERT INTO source_registry.artifacts(scope_id,provider,bucket,object_key,provider_version,sha256,byte_count,media_type,region,encryption_key_ref,classification,acl_policy_ref,rights_policy_id,retention_policy_id,state,verified_at)
        VALUES(%s,'minio',%s,%s,%s,%s,%s,%s,'local','development-storage-policy','public','oca-review-required',%s,%s,'available',%s)
        ON CONFLICT(provider,bucket,object_key,provider_version) DO UPDATE SET verified_at=EXCLUDED.verified_at RETURNING artifact_id""",(scope,bucket,key,put.get('VersionId') or 'unversioned:'+dig,dig,len(content),media,rights,retention,now)); artifacts.append(cur.fetchone()[0])
       cur.execute("""INSERT INTO oca.documents(repository_id,branch_name,commit_sha,module_release_id,document_type,relative_path,raw_artifact_id,normalized_artifact_id,content_sha256,parser_name,parser_version,state,scan_run_id)
       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'quarantined',%s) ON CONFLICT(repository_id,branch_name,relative_path) DO UPDATE SET commit_sha=EXCLUDED.commit_sha,module_release_id=EXCLUDED.module_release_id,raw_artifact_id=EXCLUDED.raw_artifact_id,normalized_artifact_id=EXCLUDED.normalized_artifact_id,content_sha256=EXCLUDED.content_sha256,parser_name=EXCLUDED.parser_name,parser_version=EXCLUDED.parser_version,state='quarantined',scan_run_id=EXCLUDED.scan_run_id RETURNING document_id""",(rid,branch,commit,mr,kind,path,artifacts[0],artifacts[1],digest,PARSER,VERSION,run)); doc=cur.fetchone()[0]
       cur.execute('DELETE FROM oca.document_blocks WHERE document_id=%s',(doc,))
       cur.executemany("INSERT INTO oca.document_blocks(document_id,scope_id,ordinal,heading_path,content,locator,text_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)",[(doc,scope,i,b['heading_path'],b['content'],json.dumps({**b['locator'],'repository':name,'branch':branch,'commit':commit}),sha(b['content'].encode())) for i,b in enumerate(blocks)])
      conn.commit(); docs_ok+=1
     except Exception as exc:
      conn.rollback(); docs_bad+=1
      with conn.cursor() as cur: cur.execute("INSERT INTO oca.documentation_scan_issues(run_id,repository_id,branch_name,relative_path,error_code,message) VALUES(%s,%s,%s,%s,'document_failed',%s)",(run,rid,branch,path,str(exc)[:2000]))
      conn.commit()
  status='succeeded' if bbad==0 and docs_bad==0 else 'partial'
  with conn.cursor() as cur: cur.execute("UPDATE oca.documentation_scan_runs SET finished_at=%s,status=%s,branches_succeeded=%s,branches_failed=%s,documents_processed=%s,documents_failed=%s WHERE run_id=%s",(datetime.now(timezone.utc),status,bok,bbad,docs_ok,docs_bad,run))
  conn.commit(); print(json.dumps({'branches_attempted':len(items),'branches_succeeded':bok,'branches_failed':bbad,'documents':docs_ok,'document_failures':docs_bad,'status':status}))
if __name__=='__main__': main()
