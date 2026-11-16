from pathlib import Path
import os
from share_settings import ROOT as SHARE_ROOT, MAIN_PATH, SUB_PATH, MAIN_URL, SUB_URL, LOCAL_KEY, TOOL_IMAGE
import asyncio,json,os,time,re,traceback,fcntl,ast,sys,shlex,contextvars
from pydantic import Field
import httpx
import hle_team_base as m
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
 context=65536
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
D=(SHARE_ROOT/'hle');R=SHARE_ROOT;m.MAIN='Orchestra-o1-8B-all-ourdata';ROOT=D/'results';ROOT.mkdir(exist_ok=True);m.ROOT=ROOT
class Execute(m.BaseAction):
 name:str='ExecuteCodeAction';description:str='Execute Python code or Bash in an isolated container. Supply test input via stdin. Files persist under /workspace; Python variables do not persist. No internet or hidden tests.';parameters:dict={'type':'object','properties':{'code':{'type':'string'},'language':{'type':'string','enum':['python','bash']},'stdin':{'type':'string'}},'required':['code'],'additionalProperties':False}
 async def __call__(self,code='',language='python',stdin=''):
  if language not in ('python','bash') or not all(isinstance(x,str) for x in [code,stdin]):return {'success':False,'error':'code/stdin must be strings; language must be python/bash'}
  case=m.CV.get();folder=ROOT/case['id']/'workspace';folder.mkdir(exist_ok=True);name='pjm-hle-tool-'+str(os.getpid())+'-'+str(time.time_ns());m.event('tool_started',tool=self.name,language=language,code=code,stdin=stdin)
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
