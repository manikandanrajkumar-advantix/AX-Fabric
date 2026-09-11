from __future__ import annotations
import hashlib,json,os,sys
import numpy as np
import onnxruntime as ort
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer
import psycopg

MODEL=os.environ.get('EMBEDDING_MODEL','intfloat/multilingual-e5-small')
REVISION=os.environ.get('EMBEDDING_MODEL_REVISION','919cfbe11fbf4f1b9bb321007c46c14feaf84227')
BATCH=int(os.environ.get('EMBEDDING_BATCH_SIZE','32'))
ODOO_VERSION=os.environ.get('ODOO_VERSION','19.0')

def main():
    model_dir=snapshot_download(MODEL,revision=REVISION,allow_patterns=['onnx/model.onnx','*.json','*.model','tokenizer*'])
    tokenizer=AutoTokenizer.from_pretrained(model_dir,local_files_only=True,trust_remote_code=False)
    session=ort.InferenceSession(os.path.join(model_dir,'onnx','model.onnx'),providers=['CPUExecutionProvider'])
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
      with conn.cursor() as cur:
        scope_key=f'product:odoo:documentation:{ODOO_VERSION}'
        cur.execute("SELECT scope_id FROM knowledge.scopes WHERE scope_key=%s",(scope_key,))
        scope_row=cur.fetchone()
        if scope_row is None:
          raise RuntimeError(f'knowledge scope does not exist: {scope_key}')
        scope=scope_row[0]
        cur.execute("""SELECT p.passage_id,p.content FROM source_registry.passages p
          WHERE p.scope_id=%s AND NOT EXISTS(SELECT 1 FROM knowledge.passage_embeddings_e5_small e
          WHERE e.passage_id=p.passage_id AND e.model_name=%s AND e.model_revision=%s) ORDER BY p.passage_id""",(scope,MODEL,REVISION))
        rows=cur.fetchall()
      completed=0
      for start in range(0,len(rows),BATCH):
        batch=rows[start:start+BATCH]
        texts=['passage: '+r[1] for r in batch]
        tokens=tokenizer(texts,padding=True,truncation=True,max_length=512,return_tensors='np')
        names={item.name for item in session.get_inputs()}
        inputs={k:v.astype(np.int64) for k,v in tokens.items() if k in names}
        if 'token_type_ids' in names and 'token_type_ids' not in inputs:
            inputs['token_type_ids']=np.zeros_like(tokens['input_ids'],dtype=np.int64)
        hidden=session.run(None,inputs)[0]
        mask=tokens['attention_mask'][...,None]
        vectors=(hidden*mask).sum(axis=1)/np.clip(mask.sum(axis=1),1,None)
        vectors=vectors/np.clip(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12,None)
        if vectors.shape[1]!=384: raise RuntimeError('unexpected embedding dimension')
        with conn.transaction():
          with conn.cursor() as cur:
            cur.executemany("""INSERT INTO knowledge.passage_embeddings_e5_small
              (scope_id,passage_id,model_name,model_revision,dimensions,input_hash,embedding)
              VALUES(%s,%s,%s,%s,384,%s,%s::vector) ON CONFLICT DO NOTHING""",[
              (scope,row[0],MODEL,REVISION,hashlib.sha256(('passage: '+row[1]).encode()).hexdigest(),json.dumps(vec.tolist()))
              for row,vec in zip(batch,vectors)])
        completed+=len(batch)
        if completed%320==0: print(json.dumps({'embedded':completed,'total':len(rows)}),flush=True)
    print(json.dumps({'model':MODEL,'revision':REVISION,'embedded':completed,'status':'succeeded'}))
    return 0
if __name__=='__main__': sys.exit(main())
