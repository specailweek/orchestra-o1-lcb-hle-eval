"""Use the pinned official HLE judge prompt and schema, with durable bounded requests."""
from pathlib import Path
import os
from share_settings import ROOT as SHARE_ROOT, MAIN_PATH, SUB_PATH, MAIN_URL, SUB_URL, LOCAL_KEY, TOOL_IMAGE
import asyncio,json,ast,time,fcntl,traceback
from typing import Literal
from pydantic import BaseModel
from openai import AsyncOpenAI
D=(SHARE_ROOT/'hle');SOURCE=D/'official/hle_eval/run_judge_results.py'
tree=ast.parse(SOURCE.read_text());namespace={'BaseModel':BaseModel,'Literal':Literal}
for node in tree.body:
 if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='JUDGE_PROMPT' for t in node.targets):PROMPT=ast.literal_eval(node.value)
 if isinstance(node,ast.ClassDef) and node.name=='ExtractedAnswer':exec(compile(ast.Module(body=[node],type_ignores=[]),'official-HLE-schema','exec'),namespace)
Schema=namespace['ExtractedAnswer']
def atomic(path,data):
 tmp=path.with_suffix('.judge.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str));tmp.replace(path)
async def main():
 lock=(D/'judge.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);client=AsyncOpenAI(api_key=os.environ.get('O1_JUDGE_API_KEY', 'EMPTY'),base_url=os.environ['O1_JUDGE_URL'],timeout=240,max_retries=0)
 state=json.loads((D/'judge-status.json').read_text()) if (D/'judge-status.json').exists() else {'model':os.environ['O1_JUDGE_MODEL'],'requests':0,'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0},'unknown_usage_requests':0,'max_requests':550,'max_recorded_tokens':3000000,'selftests':{},'errors':{}}
 state.update(pid=__import__('os').getpid(),phase='starting',updated=time.time());judgments=json.loads((D/'judgments.json').read_text()) if (D/'judgments.json').exists() else {};gate=asyncio.Semaphore(2)
 def save():state['updated']=time.time();atomic(D/'judge-status.json',state)
 async def judge(question,gold,response):
  if state['requests']>=state['max_requests'] or state['usage']['total_tokens']>=state['max_recorded_tokens']:raise RuntimeError('Local judge request/token budget exhausted')
  state['requests']+=1;save()
  try:
   async with gate:
    answer=await client.beta.chat.completions.parse(model=state['model'],max_completion_tokens=4096,messages=[{'role':'user','content':PROMPT.format(question=question,correct_answer=gold,response=response)}],response_format=Schema)
   if answer.usage:
    for key in state['usage']:state['usage'][key]+=getattr(answer.usage,key,0) or 0
   if not answer.choices[0].message.parsed:raise ValueError('No parsed judge response')
   return answer.choices[0].message.parsed.model_dump()
  except Exception:
   state['unknown_usage_requests']+=1;raise
  finally:save()
 try:
  for label,response,expected in [('correct','Explanation: Two plus two is four.\nAnswer: 4\nConfidence:100%','yes'),('wrong','Answer: 3\nConfidence:100%','no')]:
   if not state['selftests'].get(label):
    result=await judge('What is2+2?','4',response);assert result['correct']==expected,result;state['selftests'][label]=True;save()
  public=json.loads((D/'data/sample.public.json').read_text());questions={str(r['id']):r for r in public};gold=json.loads((D/'data/sample.gold.json').read_text());state.update(phase='waiting_predictions',total=len(public));save()
  while True:
   predictions=json.loads((D/'predictions.json').read_text()) if (D/'predictions.json').exists() else {};todo=[ident for ident in predictions if ident not in judgments and state['errors'].get(ident,0)<2]
   async def run(ident):
    try:
     result=await judge(questions[ident]['question'],gold[ident]['answer'],predictions[ident]['response']);judgments[ident]=result;atomic(D/'judgments.json',judgments);print('HLE_JUDGED',ident,result['correct'],flush=True)
    except Exception as exc:
     state['errors'][ident]=state['errors'].get(ident,0)+1;state['last_error']=type(exc).__name__+': '+str(exc)[:300]
     if getattr(exc,'status_code',None) in [401,402,403,429] or 'budget exhausted' in str(exc):state['phase']='blocked_api_or_budget';save();raise
     save()
   # Keep only2 in flight; do not enqueue hundreds of paid requests at once.
   for offset in range(0,len(todo),2):await asyncio.gather(*(run(ident) for ident in todo[offset:offset+2]))
   evaluation=json.loads((D/'status.json').read_text()) if (D/'status.json').exists() else {};correct=sum(v['correct']=='yes' for v in judgments.values());state.update(phase='scoring',judged=len(judgments),correct=correct,completed_predictions=evaluation.get('completed',0),pending_answered=len(predictions)-len(judgments));save()
   scores={'sample_size':len(public),'judged':len(judgments),'correct':correct,'accuracy_on_judged':correct/len(judgments) if judgments else None,'full_sample_accuracy':None,'setting':'O1 with tools, stratified20% HLE; not full-dataset/no-tools leaderboard score','model':state['model'],'usage':state['usage'],'by_subject':{},'by_modality':{},'by_type':{}}
   for field,target in [('category','by_subject'),('answer_type','by_type')]:
    for row in public:
     ident=str(row['id']);level=str(row.get(field,row.get('subject','unknown')));item=scores[target].setdefault(level,{'sample':0,'judged':0,'correct':0});item['sample']+=1
     if ident in judgments:item['judged']+=1;item['correct']+=judgments[ident]['correct']=='yes'
   for row in public:
    ident=str(row['id']);level='image' if row.get('image') else 'text';item=scores['by_modality'].setdefault(level,{'sample':0,'judged':0,'correct':0});item['sample']+=1
    if ident in judgments:item['judged']+=1;item['correct']+=judgments[ident]['correct']=='yes'
   if evaluation.get('phase')=='predictions_finished':
    unresolved=[ident for ident in predictions if ident not in judgments];infra=[k for k,v in evaluation['results'].items() if v['status']=='infrastructure_error']
    scores.update(unresolved_judge=len(unresolved),inference_infrastructure_errors=len(infra))
    if not unresolved and not infra:scores['full_sample_accuracy']=correct/len(public)
    state['phase']='finished' if not unresolved and not infra else 'finished_with_missing_scores';atomic(D/'scores.json',scores);save();return
   atomic(D/'scores.json',scores);await asyncio.sleep(15)
 except Exception as exc:
  state.update(phase='blocked_api_or_budget' if getattr(exc,'status_code',None) in [401,402,403,429] or 'budget exhausted' in str(exc) else 'failed',error=type(exc).__name__+': '+str(exc)[:400]);save();raise
 finally:await client.close()
if __name__=='__main__':
 if os.environ.get('O1_ENABLE_JUDGE')!='1':raise SystemExit('Judge disabled. Configure a local judge and explicitly set O1_ENABLE_JUDGE=1.')
 asyncio.run(main())
