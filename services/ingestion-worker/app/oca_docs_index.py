from __future__ import annotations
import hashlib,json,os
import numpy as np,onnxruntime as ort,psycopg
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer
MODEL=os.environ.get('EMBEDDING_MODEL','intfloat/multilingual-e5-small'); REV=os.environ.get('EMBEDDING_MODEL_REVISION','919cfbe11fbf4f1b9bb321007c46c14feaf84227'); LIMIT=3500
def digest(s): return hashlib.sha256(s.encode()).hexdigest()
def main():
 model_dir=snapshot_download(MODEL,revision=REV,allow_patterns=['onnx/model.onnx','*.json','*.model','tokenizer*']); tok=AutoTokenizer.from_pretrained(model_dir,local_files_only=True,trust_remote_code=False); session=ort.InferenceSession(os.path.join(model_dir,'onnx','model.onnx'),providers=['CPUExecutionProvider'])
 with psycopg.connect(os.environ['DATABASE_URL']) as conn:
  with conn.cursor() as cur: cur.execute("SELECT document_id,(SELECT scope_id FROM oca.repositories r WHERE r.repository_id=d.repository_id) FROM oca.documents d WHERE state='quarantined' ORDER BY document_id"); docs=cur.fetchall()
  passages=0
  for doc,scope in docs:
   with conn.cursor() as cur:
    cur.execute('SELECT block_id,content FROM oca.document_blocks WHERE document_id=%s ORDER BY ordinal',(doc,)); blocks=cur.fetchall(); groups=[]; current=[]; size=0
    for block in blocks:
     if current and size+len(block[1])+2>LIMIT: groups.append(current); current=[]; size=0
     current.append(block); size+=len(block[1])+2
    if current: groups.append(current)
    cur.execute('DELETE FROM oca.passages WHERE document_id=%s AND ordinal >= %s',(doc,len(groups)))
    for ordinal,group in enumerate(groups):
     content='\n\n'.join(x[1] for x in group); cur.execute("INSERT INTO oca.passages(document_id,scope_id,ordinal,content,text_sha256,token_count,chunker_version) VALUES(%s,%s,%s,%s,%s,%s,'oca-blocks-3500-v1') ON CONFLICT(document_id,ordinal) DO UPDATE SET content=EXCLUDED.content,text_sha256=EXCLUDED.text_sha256,token_count=EXCLUDED.token_count RETURNING passage_id",(doc,scope,ordinal,content,digest(content),len(content.split()))); pid=cur.fetchone()[0]
     cur.execute('DELETE FROM oca.passage_blocks WHERE passage_id=%s',(pid,)); cur.executemany('INSERT INTO oca.passage_blocks(passage_id,block_id,ordinal) VALUES(%s,%s,%s)',[(pid,b[0],i) for i,b in enumerate(group)]); passages+=1
   conn.commit()
  with conn.cursor() as cur: cur.execute("SELECT p.passage_id,p.scope_id,p.content FROM oca.passages p LEFT JOIN knowledge.oca_passage_embeddings_e5_small e ON e.passage_id=p.passage_id AND e.model_name=%s AND e.model_revision=%s WHERE e.passage_id IS NULL OR e.input_hash<>encode(digest(convert_to('passage: '||p.content,'UTF8'),'sha256'),'hex') ORDER BY length(p.content),p.passage_id",(MODEL,REV)); rows=cur.fetchall()
  embedded=0
  for start in range(0,len(rows),32):
   batch=rows[start:start+32]; texts=['passage: '+r[2] for r in batch]; tokens=tok(texts,padding=True,truncation=True,max_length=512,return_tensors='np'); names={i.name for i in session.get_inputs()}; inputs={k:v.astype(np.int64) for k,v in tokens.items() if k in names}
   if 'token_type_ids' in names and 'token_type_ids' not in inputs: inputs['token_type_ids']=np.zeros_like(tokens['input_ids'],dtype=np.int64)
   hidden=session.run(None,inputs)[0]; mask=tokens['attention_mask'][...,None]; vectors=(hidden*mask).sum(1)/np.clip(mask.sum(1),1,None); vectors/=np.clip(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12,None)
   with conn.cursor() as cur: cur.executemany("INSERT INTO knowledge.oca_passage_embeddings_e5_small(passage_id,scope_id,model_name,model_revision,dimensions,input_hash,embedding) VALUES(%s,%s,%s,%s,384,%s,%s::vector) ON CONFLICT(passage_id,model_name,model_revision) DO UPDATE SET input_hash=EXCLUDED.input_hash,embedding=EXCLUDED.embedding,created_at=clock_timestamp()",[(r[0],r[1],MODEL,REV,digest('passage: '+r[2]),json.dumps(v.tolist())) for r,v in zip(batch,vectors)])
   conn.commit(); embedded+=len(batch)
 print(json.dumps({'passages_processed':passages,'embeddings_written':embedded,'status':'succeeded'}))
if __name__=='__main__': main()
