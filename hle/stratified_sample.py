"""Select one fifth of HLE, with predeclared subject/modality/answer-type strata."""
from pathlib import Path
import json,math,random,hashlib,collections,argparse,os
SEED=42
def allocate(counts,target):
 total=sum(counts.values());assert 0<target<=total
 if len(counts)>target:raise ValueError('Too many nonempty strata to cover every stratum at this sample size; review official category field before sampling')
 ideal={k:counts[k]*target/total for k in counts};quota={k:min(counts[k],max(1,math.floor(ideal[k]))) for k in counts}
 while sum(quota.values())>target:
  k=max((k for k in counts if quota[k]>1),key=lambda k:(quota[k]-ideal[k],counts[k],k));quota[k]-=1
 while sum(quota.values())<target:
  k=max((k for k in counts if quota[k]<counts[k]),key=lambda k:(ideal[k]-quota[k],counts[k],k));quota[k]+=1
 assert sum(quota.values())==target and all(1<=quota[k]<=counts[k] for k in counts)
 return quota
def image_present(value):
 if isinstance(value,dict):return bool(value.get('bytes') or value.get('path'))
 return bool(value)
def select(rows):
 if not rows:raise ValueError('Dataset is empty')
 required={'id','question','answer','answer_type','image'};assert required.issubset(rows[0]),sorted(rows[0])
 subject='category' if 'category' in rows[0] else ('subject' if 'subject' in rows[0] else None)
 if subject is None:raise ValueError('No official broad subject/category field. Inspect schema before choosing strata; do not invent difficulty labels.')
 groups=collections.defaultdict(list)
 for row in rows:
  assert row.get(subject) and row.get('answer_type'),'Missing subject/answer_type must be resolved explicitly'
  groups[(str(row[subject]),'image' if image_present(row.get('image')) else 'text',str(row['answer_type']))].append(row)
 target=round(len(rows)/5);counts={k:len(v) for k,v in groups.items()};quota=allocate(counts,target);chosen=[];report=[]
 for key in sorted(groups):
  pool=sorted(groups[key],key=lambda r:str(r['id']));seed=int.from_bytes(hashlib.sha256((str(SEED)+'|'+json.dumps(key)).encode()).digest()[:8],'big');chosen+=random.Random(seed).sample(pool,quota[key]);report.append({'subject':key[0],'modality':key[1],'answer_type':key[2],'population':counts[key],'sample':quota[key],'fraction':quota[key]/counts[key],'analysis_weight':counts[key]/quota[key]})
 chosen.sort(key=lambda r:str(r['id']));assert len(chosen)==target and len({r['id'] for r in chosen})==target
 return chosen,{'population':len(rows),'sample_size':target,'requested_fraction':.2,'seed':SEED,'subject_field':subject,'strata':report,'policy':'Proportional largest-remainder allocation with at least one per nonempty joint stratum; small strata may exceed20%; fixed before inference; no answers used for selection','sample_ids':[str(r['id']) for r in chosen],'search_budget':{'max_per_question':10,'max_total':target*10},'score_reporting':'Report raw sample accuracy and subject/modality/type breakdowns; use population weights for a full-population estimate; this is a stratified subset, not full HLE score'}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('parquet',type=Path);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
 import pyarrow.parquet as pq
 rows=pq.read_table(args.parquet).to_pylist();selected,report=select(rows);out=args.output;out.mkdir(parents=True,exist_ok=True);os.chmod(out,0o700)
 # Gold answers are never placed in the agent-facing manifest.
 public=[{k:r[k] for k in ['id','question','image','answer_type']+[report['subject_field']] if k in r} for r in selected];gold={str(r['id']):{'answer':r['answer']} for r in selected}
 for name,value in [('sample.public.json',public),('sample.gold.json',gold),('sampling-report.json',report)]:
  p=out/name;p.write_text(json.dumps(value,ensure_ascii=False,indent=2));os.chmod(p,0o600)
 report['source_sha256']=hashlib.file_digest(args.parquet.open('rb'),'sha256').hexdigest();(out/'sampling-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'population':len(rows),'sample':len(selected),'nonempty_strata':len(report['strata']),'seed':SEED,'search_budget':report['search_budget']}))
if __name__=='__main__':main()
