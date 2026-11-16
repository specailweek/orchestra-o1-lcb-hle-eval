from pathlib import Path
import os
from share_settings import ROOT as SHARE_ROOT, MAIN_PATH, SUB_PATH, MAIN_URL, SUB_URL, LOCAL_KEY, TOOL_IMAGE
import asyncio,json,os,time,re,traceback,fcntl,ast,sys,shlex,contextvars
from pydantic import Field
import httpx
import lcb_team_base as m
from transformers import AutoTokenizer
tokenizer=AutoTokenizer.from_pretrained(MAIN_PATH,local_files_only=True)
sub_tokenizer=AutoTokenizer.from_pretrained(SUB_PATH,local_files_only=True)
request_token_cost=contextvars.ContextVar('lcb_request_token_cost',default=1)
class TokenBudgetGate:
 def __init__(self,name,requests,tokens):
  self.name=name;self.max_requests=requests;self.max_tokens=tokens;self.running=0;self.tokens=0;self.condition=asyncio.Condition();self.waiters=[];self.reserved=contextvars.ContextVar(name+'_reserved',default=0)
 async def __aenter__(self):
  cost=request_token_cost.get();assert 0<cost<=self.max_tokens,(self.name,cost,self.max_tokens);started=time.time()
  async with self.condition:
   ticket=object();self.waiters.append(ticket)
   try:await self.condition.wait_for(lambda:self.waiters[0] is ticket and self.running<self.max_requests and self.tokens+cost<=self.max_tokens)
   except BaseException:self.waiters.remove(ticket);self.condition.notify_all();raise
   self.waiters.pop(0)
   self.running+=1;self.tokens+=cost;self.reserved.set(cost)
   m.event('model_admission',model_role=self.name,waiting_seconds=time.time()-started,reserved_tokens=cost,active_requests=self.running,total_reserved_tokens=self.tokens)
   self.condition.notify_all()
  return self
 async def __aexit__(self,*exc):
  async with self.condition:
   self.running-=1;self.tokens-=self.reserved.get();self.reserved.set(0);self.condition.notify_all()
def bounded(text,limit,tok=tokenizer):
 ids=tok.encode(text)
 if len(ids)<=limit:return text
 m.event('context_compaction',original_tokens=len(ids),retained_tokens=limit,method='Deterministic head/tail extraction; full raw logs retained')
 return tok.decode(ids[:6000])+'\n[Earlier trace compacted; full log retained]\n'+tok.decode(ids[-(limit-6100):])
def normalize_request(model,params):
 tok=tokenizer if model=='Orchestra-o1-8B-all-ourdata' else sub_tokenizer
 context=40960 if model=='Orchestra-o1-8B-all-ourdata' else 65536
 messages=params['messages'];budget=context-params['max_tokens']-1024
 # All requests including memory compression and trace summaries reserve output space.
 total=sum(len(tok.encode(str(msg.get('content',''))))+32 for msg in messages)
 if total>budget:
  remaining=budget
  for msg in reversed(messages):
   text=str(msg.get('content',''));limit=max(256,remaining-64)
   if len(tok.encode(text))>limit:msg['content']=bounded(text,limit,tok) if limit>6200 else tok.decode(tok.encode(text)[-limit:])
   remaining-=len(tok.encode(str(msg.get('content',''))))+32
  if remaining<0:raise ValueError('Request has too many independent messages for context budget')
 cost=sum(len(tok.encode(str(msg.get('content',''))))+32 for msg in messages)+params['max_tokens']+1024
 request_token_cost.set(cost)
m.normalize_request=normalize_request
SERVICE=(SHARE_ROOT/'lcb-service');D=Path(os.environ.get('LCB_RUN_DIR',str(SERVICE/'full-v6-r2')));D.mkdir(parents=True,exist_ok=True);R=SHARE_ROOT;m.MAIN='Orchestra-o1-8B-all-ourdata';ROOT=D/'results';ROOT.mkdir(exist_ok=True);m.ROOT=ROOT
class Execute(m.BaseAction):
 name:str='ExecuteCodeAction';description:str='Execute Python code or Bash in an isolated container. Supply test input via stdin. Files persist under /workspace; Python variables do not persist. No internet or hidden tests.';parameters:dict={'type':'object','properties':{'code':{'type':'string'},'language':{'type':'string','enum':['python','bash']},'stdin':{'type':'string'}},'required':['code'],'additionalProperties':False}
 async def __call__(self,code='',language='python',stdin=''):
  if language not in ('python','bash') or not all(isinstance(x,str) for x in [code,stdin]):return {'success':False,'error':'code/stdin must be strings; language must be python/bash'}
  case=m.CV.get();folder=ROOT/case['id']/'workspace';folder.mkdir(exist_ok=True);name='pjm-lcb-tool-'+str(os.getpid())+'-'+str(time.time_ns());m.event('tool_started',tool=self.name,language=language,code=code,stdin=stdin)
  cmd=['docker','run','--rm','-i','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','96','--memory','2g','--cpus','2','--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,size=256m','-e','HOME=/tmp','-v',str(folder)+':/workspace:rw','-w','/workspace',TOOL_IMAGE,*(['python','-u','-c',code] if language=='python' else ['bash','-c',code])]
  p=await asyncio.create_subprocess_exec(*cmd,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
  try:out,err=await asyncio.wait_for(p.communicate(stdin.encode()),30);result={'success':p.returncode==0,'exit_code':p.returncode,'stdout':out.decode(errors='replace')[-12000:],'stderr':err.decode(errors='replace')[-4000:]}
  except asyncio.TimeoutError:result={'success':False,'error':'Execution time budget30seconds exhausted'}
  finally:
   cleanup=await asyncio.create_subprocess_exec('docker','rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL);await cleanup.wait()
   if p.returncode is None:p.kill()
   await p.wait()
  m.event('tool_completed',tool=self.name,result=result);return result
def workspace_path(path):
 if not isinstance(path,str) or not path.startswith('/workspace/'):raise ValueError('path must start with /workspace/')
 root=(ROOT/m.CV.get()['id']/'workspace').resolve();target=(root/path.removeprefix('/workspace/')).resolve()
 if not target.is_relative_to(root):raise ValueError('path must remain inside this question workspace')
 return target
class WriteFile(m.BaseAction):
 name:str='WriteFileAction';description:str='Write exact UTF-8 file content into /workspace. Use this for source files instead of nested Python string escaping.';parameters:dict={'type':'object','properties':{'path':{'type':'string'},'content':{'type':'string'}},'required':['path','content'],'additionalProperties':False}
 async def __call__(self,path,content):
  if not isinstance(content,str):raise ValueError('content must be a string')
  target=workspace_path(path);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content);result={'success':True,'path':path,'bytes':len(content.encode())};m.event('file_written',path=path,content=content);return result
class RunFile(m.BaseAction):
 name:str='RunFileAction';description:str='Run an existing Python file with public sample stdin inside the isolated question container. Returns actual stdout/stderr/exit status.';parameters:dict={'type':'object','properties':{'path':{'type':'string'},'stdin':{'type':'string'}},'required':['path'],'additionalProperties':False}
 async def __call__(self,path,stdin=''):
  target=workspace_path(path)
  if not target.is_file():return {'success':False,'error':'File does not exist: '+path}
  return await Execute()(code='python '+shlex.quote(path),language='bash',stdin=stdin)
class Env(m.MediaEnv):
 def __init__(self,case):self.case=case;self.tools={t.name:t for t in [Execute(),WriteFile(),RunFile()]};self.instruction=case['question']
 def clone(self):return Env(self.case)
 def get_basic_info(self):return m.BasicInfo(env_id=self.case['id'],instruction=self.instruction,action_space='\n'.join(t.name+': '+t.description+' Schema:'+json.dumps(t.parameters) for t in self.tools.values())+'\nfinish: params result,status,summary',max_steps=16,meta_data={'category':'LiveCodeBench-codegeneration'})
 async def reset(self,seed=None):return {'instruction':self.instruction,'workspace':'/workspace','message':'Write/run real Python and verify public examples before finishing.'}
 async def step(self,action):
  try:return await super().step(action)
  except (TypeError,ValueError) as exc:
   result={'success':False,'error':'Tool arguments invalid: '+str(exc),'schema':Execute().parameters};m.event('tool_result',action=action,result=result);return result,0.,False,{'last_action_result':result}
async def step(self,observation,history,**kw):
 if history:
  z=history[-1];await self.memory.add_memory(obs=z.info.get('last_action_result',z.observation) if z.info else z.observation,action=z.action,raw_response=z.raw_response)
 schemas={t.name:t.parameters for t in [Execute(),WriteFile(),RunFile()] if not self.allowed_tools or t.name in self.allowed_tools}
 warning=self._get_budget_warning(self.max_steps-len(history));repeat=''
 if len(history)>=2 and history[-1].action==history[-2].action:repeat='Your last action was repeated. Read the actual result. Change the action to advance the task, run the saved code, or finish with observed results.'
 prompt=f'''You are a coding SubAgent in the original Orchestra-o1 team. Implement correct Python3, execute public examples, and correct observed failures. Save complete source to /workspace/solution.py. Prefer WriteFileAction to write exact source, then RunFileAction with stdin to run it. Checking whether a file exists is not a test. After a successful sample run finish promptly. Files persist; Python variables do not. No internet or hidden tests. Return one JSON object with action and params. Allowed tool schemas: {json.dumps(schemas)}. Also finish(params result:string,status:done/partial,summary:string). Task:{self.task_instruction}\nContext:{self.context}\nOriginal problem:{self.original_question}\nMemory:{self._get_memory()}\nLatest observation:{observation}\nStep {len(history)+1}/{self.max_steps}\n{warning}\n{repeat}'''
 prompt=bounded(prompt,60000,sub_tokenizer);response=await self.llm(prompt);return self.parse_action(response),response,prompt
m.PairedWorker.step=step
class Complete(m.BaseAction):
 name:str='complete';description:str='Submit final Python source or /workspace/solution.py after public verification where feasible. Direct valid submissions are accepted. No hidden-test feedback.';parameters:dict={'type':'object','properties':{'answer':{'type':'string'}},'required':['answer']}
 async def __call__(self,answer='',**extra):
  case=m.CV.get();folder=ROOT/case['id']
  if answer.strip().startswith('/workspace/'):
   file=(folder/'workspace'/answer.strip().removeprefix('/workspace/')).resolve()
   if not file.is_relative_to((folder/'workspace').resolve()) or not file.is_file():return {'done':False,'error':'Submitted file does not exist'}
   code=file.read_text()
  else:
   blocks=re.findall(r'```(?:python|py)?\s*\n(.*?)```',answer,re.S);code=blocks[-1] if blocks else answer
  try:ast.parse(code)
  except SyntaxError as e:return {'done':False,'error':'Invalid Python syntax:'+str(e)}
  (folder/'candidate.py').write_text(code);return {'done':True,'answer':code}
class CodingMain(m.FeedbackMain):
 def _format_subtask_history(self):return bounded(super()._format_subtask_history(),24000)
class Prompt:
 @staticmethod
 def build_prompt(instruction,meta,prior_context,attempt_index,max_attempts,sub_models,subtask_history='',model_to_alias=None,tools=None):
  return bounded(f'''You are MainAgent of the original Orchestra-o1 coding team. Only model_1 (Qwen3-Omni) can execute tools. Delegate algorithm design, implementation and public-example verification, then inspect real results and replan as needed. Do not search or request hidden tests. A plan is not code. Actions ONLY delegate_task(params tasks:[{{task_instruction,context,model:'model_1',tools:['WriteFileAction','RunFileAction','ExecuteCodeAction']}}]) or complete(params answer:Python source or '/workspace/solution.py'). Output one JSON object action,reasoning,params. Final file must match required stdin/stdout or starter Solution signature. One final submission per problem; internal public testing allowed.\nPROBLEM:{instruction}\nROUND:{attempt_index}/{max_attempts}\nACTUAL WORKER HISTORY:{subtask_history}\nERROR FEEDBACK:{prior_context[-8000:]}''',32000)
async def run_case(row):
 ident=row['platform']+'-'+str(row['question_id']);folder=ROOT/ident;folder.mkdir(exist_ok=True);(folder/'workspace').mkdir(exist_ok=True)
 examples=json.loads(row['public_test_cases']);(folder/'workspace'/'public_examples.json').write_text(json.dumps(examples));preview=json.dumps(examples[:3],ensure_ascii=False)
 if len(preview)>16000:preview=preview[:16000]+'\n[Preview truncated; read full public examples from /workspace/public_examples.json]'
 question=row['question_content']+'\nStarter code:\n'+(row.get('starter_code') or '')+'\nPublic examples preview:\n'+preview+'\nAll public examples are available at /workspace/public_examples.json. Hidden tests are unavailable.';case={'id':ident,'question':question};token=m.CV.set(case);rounds=[];answer=None;start=time.time()
 try:
  env=Env(case);runner=m.Runner();runner.step_timeout=600;delegate=m.VerifiedDelegate(env=env,runner=runner,models=[m.SUB],benchmark_type='omnigaia',alias_to_model={'model_1':m.SUB});main=CodingMain(llm=m.create_llm_instance(m.LLMsConfig.default().get(m.MAIN)),sub_models=[m.SUB],tools=[delegate,Complete()],subagent_tools=list(env.tools.values()),prompt_builder=Prompt,max_attempts=10,benchmark_type='omnigaia',mask_model_names=True);main.reset(env.get_basic_info())
  async def solve():
   nonlocal answer
   for i in range(10):
    action,raw=await main.step(None,[]);rounds.append({'round':i+1,'action':m._make_serializable(action),'raw_response':raw});(folder/'trajectory.json').write_text(json.dumps(rounds,ensure_ascii=False,default=str,indent=2))
    if action.get('action')=='complete' and (action.get('result') or {}).get('done'):answer=action['result']['answer'];return
  await asyncio.wait_for(solve(),1800);result={'status':'submitted' if answer else 'no_submission'}
 except Exception as e:result={'status':'runtime_error','error':type(e).__name__+': '+str(e)[:800]}
 finally:m.CV.reset(token)
 counts={}
 if (folder/'events.jsonl').exists():
  for line in (folder/'events.jsonl').read_text().splitlines():
   e=json.loads(line);counts[e['kind']]=counts.get(e['kind'],0)+1
 result.update(seconds=time.time()-start,rounds=len(rounds),candidate_exists=(folder/'candidate.py').exists(),event_counts=counts,delegated=counts.get('delegation_result',0)>0);return result
async def judge(row,folder):
 # Hidden tests are mounted only after candidate.py has been frozen; never visible to worker tools.
 import hashlib
 before=hashlib.sha256((folder/'candidate.py').read_bytes()).hexdigest();evaldir=folder/'private-eval';evaldir.mkdir(exist_ok=True);out=evaldir/'out';out.mkdir(exist_ok=True);(evaldir/'problem.json').write_text(json.dumps(row));os.chmod(evaldir/'problem.json',0o600)
 name='pjm-lcb-judge-'+str(os.getpid())+'-'+str(time.time_ns())
 cmd=['docker','run','--rm','--init','--name',name,'--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','96','--memory','4g','--cpus','2','--user',f'{os.getuid()}:{os.getgid()}','--tmpfs','/tmp:rw,size=256m','-e','OPENBLAS_NUM_THREADS=1','-v',str(evaldir/'problem.json')+':/eval/problem.json:ro','-v',str(folder/'candidate.py')+':/eval/candidate.py:ro','-v',str(D/'lcb_judge.py')+':/judge.py:ro','-v',str(D/'testing_util.py')+':/checker/testing_util.py:ro','-v',str(out)+':/out:rw',TOOL_IMAGE,'python','/judge.py']
 p=await asyncio.create_subprocess_exec(*cmd,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
 try:stdout,stderr=await asyncio.wait_for(p.communicate(),7200)
 except asyncio.TimeoutError:return {'evaluation_status':'infrastructure_error','error':'Judge watchdog exceeded 7200 seconds','passed':None}
 finally:
  cleanup=await asyncio.create_subprocess_exec('docker','rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL);await cleanup.wait()
  if p.returncode is None:p.kill()
  await p.wait()
 (evaldir/'stdout.log').write_bytes(stdout);(evaldir/'stderr.log').write_bytes(stderr)
 assert before==hashlib.sha256((folder/'candidate.py').read_bytes()).hexdigest()
 if p.returncode or not (out/'verdict.json').exists():return {'evaluation_status':'infrastructure_error','error':stderr.decode(errors='replace')[-1000:],'passed':None}
 return {'evaluation_status':'official_checker_completed',**json.loads((out/'verdict.json').read_text())}
async def preflight():
 report={}
 token=m.CV.set({'id':'validation-tools','question':'Adapter verification only'})
 (ROOT/'validation-tools'/'workspace').mkdir(parents=True,exist_ok=True)
 try:
  tool=Execute()
  checks=[('python_stdin',{'code':'print(int(input())+1)','stdin':'2\n'},'3'),('bash_stdin',{'code':'read x; echo $((x+1))','language':'bash','stdin':'2\n'},'3'),('persist_write',{'code':"open('/workspace/probe.txt','w').write('persisted'); print('saved')"},'saved'),('persist_read',{'code':"print(open('/workspace/probe.txt').read())"},'persisted'),('isolation',{'code':"import os; print(os.path.exists('/eval/problem.json'))"},'False')]
  for name,params,expected in checks:
   res=await tool(**params);assert res.get('success') and res['stdout'].strip()==expected,(name,res);report[name]=True
 finally:m.CV.reset(token)
 stdin={'public_test_cases':json.dumps([{'input':'2 3\n','output':'5\n','testtype':'stdin'}]),'private_test_cases':'[]','metadata':'{}'}
 functional={'public_test_cases':json.dumps([{'input':'2\n3','output':'5','testtype':'functional'}]),'private_test_cases':'[]','metadata':json.dumps({'func_name':'add'})}
 import pickle,zlib,base64
 compressed=dict(stdin,private_test_cases=base64.b64encode(zlib.compress(pickle.dumps(json.dumps([{'input':'8 9\n','output':'17\n','testtype':'stdin'}])))).decode())
 for name,row,source,expected in [('stdin_correct',stdin,'a,b=map(int,input().split());print(a+b)',True),('stdin_wrong',stdin,'print(0)',False),('functional_correct',functional,'class Solution:\n def add(self,a,b): return a+b',True),('functional_wrong',functional,'class Solution:\n def add(self,a,b): return 0',False),('syntax_error',stdin,'def broken(',False),('runtime_error',stdin,'raise RuntimeError("intentional")',False),('time_limit',stdin,'while True: pass',False),('compressed_private',compressed,'a,b=map(int,input().split());print(a+b)',True)]:
  folder=ROOT/('validation-'+name);folder.mkdir(exist_ok=True);(folder/'candidate.py').write_text(source);verdict=await judge(row,folder);assert verdict.get('passed') is expected,(name,verdict);report[name]=True
 # Exercise the real original DelegateTaskTool/ReAct/Runner and its live Omni service.
 case={'id':'validation-delegate','question':'Read two integers from standard input and print their sum. Public sample input: 2 3\n; output: 5\n. Save solution.py.'};folder=ROOT/case['id'];(folder/'workspace').mkdir(parents=True,exist_ok=True);token=m.CV.set(case)
 try:
  env=Env(case);runner=m.Runner();runner.step_timeout=600
  delegate=m.VerifiedDelegate(env=env,runner=runner,models=[m.SUB],benchmark_type='omnigaia',alias_to_model={'model_1':m.SUB})
  result=await asyncio.wait_for(delegate(tasks=[{'task_instruction':'Create /workspace/solution.py containing a complete Python stdin program to sum two integers. Use WriteFileAction to write exact Python source to that file. Then use RunFileAction with path /workspace/solution.py and stdin 2 3\n. Confirm stdout is 5 and finish.','context':case['question'],'model':'model_1','tools':['WriteFileAction','RunFileAction','ExecuteCodeAction']}]),900)
  (folder/'delegate_result.json').write_text(json.dumps(result,default=str))
  events=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines()]
  assert any(e['kind']=='tool_completed' and e['result'].get('success') and e['result'].get('stdout','').strip()=='5' for e in events),'Live delegated tool execution did not return the known sample answer'
  done=await Complete()(answer='/workspace/solution.py');assert done.get('done'),done
  verdict=await judge(compressed,folder);assert verdict.get('passed') is True,verdict;report['original_team_live_delegation_and_submission']=True
 finally:m.CV.reset(token)
 # MainAgent loop must also submit valid source through the same completion action.
 row=dict(stdin,platform='validation',question_id='main',question_content='Read two integers a and b from stdin and print a+b. Return a complete Python program.',starter_code='')
 res=await run_case(row);assert res['candidate_exists'],res
 verdict=await judge(compressed,ROOT/'validation-main');assert verdict.get('passed') is True,(res,verdict);report['live_main_loop']=True
 return report

async def main():
 import hashlib
 lock=(D/'evaluation.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 fingerprint=hashlib.sha256(b''.join((D/name).read_bytes() for name in ['run_lcb_o1.py','lcb_team_base.py','lcb_judge.py','testing_util.py'])).hexdigest()
 statefile=D/'status.json'
 if statefile.exists():
  state=json.loads(statefile.read_text());assert state['adapter_sha256']==fingerprint,'Do not mix adapter versions within one full run'
 else:state={'dataset':'LiveCodeBench release_v6 code_generation_lite','total':1055,'selection':'all 1055 pinned-release problems; scheduling by available data, no subset selection','models':{'main':m.MAIN,'sub':m.SUB},'contexts':{'main':40960,'sub':65536},'prompt_budgets':{'main':32000,'sub':60000},'n':1,'concurrency':2,'framework':'Original Orchestra-o1 MainAgent DelegateTaskTool ReAct Runner; codegeneration adapter revision2','limits':{'main_rounds':10,'worker_steps':16,'case_seconds':1800,'per_test_seconds':6},'adapter_sha256':fingerprint,'started':time.time(),'results':{}}
 state.update(pid=os.getpid(),phase='waiting_services',updated=time.time())
 def save():
  state['updated']=time.time();p=D/'status.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2,default=str));tmp.replace(p)
 save()
 for _ in range(360):
  s=json.loads((SERVICE/'services/services.json').read_text())
  if s['phase']=='failed':raise RuntimeError('Model startup failed; see services/services.json')
  if s['phase']=='ready' and s['services']['sub']['context']==65536:break
  await asyncio.sleep(5)
 else:raise TimeoutError('Model setup timeout')
 config=json.loads((D/'scheduler-config.json').read_text()) if (D/'scheduler-config.json').exists() else {'problem_concurrency':2,'main_requests':2,'sub_requests':2}
 main_requests=int(config.get('main_requests',2));sub_requests=int(config.get('sub_requests',2));assert 1<=main_requests<=4 and 1<=sub_requests<=2
 m.LLMsConfig._default_config=m.LLMsConfig({m.MAIN:{'api_key':m.KEY,'base_url':MAIN_URL,'temperature':.2,'top_p':.95},m.SUB:{'api_key':m.KEY,'base_url':SUB_URL,'temperature':0.,'top_p':1.}});m.main_gate=TokenBudgetGate('main',main_requests,int(config.get('main_token_reservation',135000)));m.sub_gate=TokenBudgetGate('sub',sub_requests,int(config.get('sub_token_reservation',70000)))
 if not state.get('preflight_passed'):
  state['phase']='validating_pipeline';save();state['preflight']=await preflight();state['preflight_passed']=True;save()
 config=json.loads((D/'scheduler-config.json').read_text()) if (D/'scheduler-config.json').exists() else {'problem_concurrency':2}
 slots=int(config['problem_concurrency']);assert 1<=slots<=6
 state.update(phase='running_data_streaming',active_tasks=[],concurrency=slots,model_request_concurrency={'main':main_requests,'sub':sub_requests},token_reservations={'main':m.main_gate.max_tokens,'sub':m.sub_gate.max_tokens});save();gate=asyncio.Semaphore(slots)
 async def execute(row,ident):
  async with gate:
   if ident in state['results']:return
   state['active_tasks'].append(ident);save();folder=ROOT/ident;folder.mkdir(exist_ok=True);rp=folder/'generation_result.json'
   if rp.exists():res=json.loads(rp.read_text())
   elif (folder/'candidate.py').exists():res={'status':'submitted_before_restart','candidate_exists':True,'resumed_frozen_candidate':True}
   elif (folder/'trajectory.json').exists():res={'status':'interrupted_before_submission','candidate_exists':False,'evaluation_status':'infrastructure_error','passed':None}
   else:
    res=await run_case(row);rp.write_text(json.dumps(res))
   if res['candidate_exists']:
    gold=json.loads((D/'data/private'/(ident+'.json')).read_text());assert gold['question_id']==row['question_id']
    for attempt in range(3):
     verdict=await judge(gold,folder)
     if verdict['evaluation_status']!='infrastructure_error':break
     await asyncio.sleep(5)
    res.update(verdict)
   elif 'evaluation_status' not in res:
    infra=res['status']=='runtime_error' and any(x in res.get('error','') for x in ['API','Connection','HTTP','CUDA','OutOfMemory','RateLimit'])
    res.update(evaluation_status='infrastructure_error' if infra else 'no_submission',passed=None if infra else False)
   res.update(title=row['question_title'],difficulty=row['difficulty'],platform=row['platform'],contest_date=row['contest_date'])
   state['results'][ident]=res;state['active_tasks'].remove(ident);state['completed']=len(state['results']);state['passed']=sum(v.get('passed') is True for v in state['results'].values());state['infrastructure_errors']=sum(v.get('passed') is None for v in state['results'].values());save();print('TASK_FINISHED',ident,res['status'],res.get('passed'),state['completed'],'/1055',flush=True)
 pending={};validated=False;public={}
 while True:
  if (D/'data/READY').exists() and not validated:
   manifest=json.loads((D/'data/manifest.json').read_text());pp=D/'data/public.json';assert hashlib.sha256(pp.read_bytes()).hexdigest()==manifest['public_sha256']
   rows=json.loads(pp.read_text());assert len(rows)==1055
   ids=[r['platform']+'-'+str(r['question_id']) for r in rows];assert len(set(ids))==1055
   assert all('private_test_cases' not in r for r in rows)
   offset=0
   for source in manifest['files']:
    digest=hashlib.sha256();size=0
    for ident in ids[offset:offset+source['count']]:
     path=D/'data/private'/(ident+'.json');assert path.is_file()
     with path.open('rb') as file:
      while chunk:=file.read(1024*1024):digest.update(chunk);size+=len(chunk)
    assert digest.hexdigest()==source['sha256'] and size==source['bytes'],source['name'];offset+=source['count']
   assert offset==1055 and set(state['results']).issubset(ids)
   public=dict(zip(ids,rows));validated=True;state.update(phase='running',manifest=manifest,full_dataset_integrity_verified=True);save()
  files=sorted((D/'data/private').glob('*.json')) if not validated else [D/'data/private'/(ident+'.json') for ident in public]
  state['downloaded_questions']=len(files)
  for path in files:
   if len(pending)>=slots:break
   ident=path.stem
   if ident in pending or ident in state['results']:continue
   if validated:row=public[ident]
   else:
    gold=json.loads(path.read_text());assert ident==gold['platform']+'-'+str(gold['question_id']);row={k:v for k,v in gold.items() if k!='private_test_cases'};del gold
   pending[ident]=asyncio.create_task(execute(row,ident))
  save()
  if not pending:
   if validated and len(state['results'])==1055:break
   state['phase']='waiting_remaining_data';save();await asyncio.sleep(10);continue
  done,_=await asyncio.wait(pending.values(),timeout=10,return_when=asyncio.FIRST_COMPLETED)
  for task in done:
   await task
   for ident,value in list(pending.items()):
    if value is task:del pending[ident]
 state.update(phase='finished',active_tasks=[],finished=time.time());state['pass_at_1']=state['passed']/1055 if not state['infrastructure_errors'] else None
 state['valid_case_pass_rate']=state['passed']/(1055-state['infrastructure_errors']) if state['infrastructure_errors']<1055 else None
 state['by_difficulty']={level:{'total':sum(v['difficulty']==level for v in state['results'].values()),'passed':sum(v['difficulty']==level and v.get('passed') is True for v in state['results'].values())} for level in ['easy','medium','hard']};save()
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception:
  p=D/'status.json';s=json.loads(p.read_text()) if p.exists() else {};s.update(phase='failed',error=traceback.format_exc(),updated=time.time());p.write_text(json.dumps(s,indent=2));raise
