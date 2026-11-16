"""Dataset adapter for original Orchestra-o1 MainAgent/DelegateTaskTool/ReAct/Runner."""
from pathlib import Path
import os
from share_settings import ROOT as SHARE_ROOT, MAIN_PATH, SUB_PATH, MAIN_URL, SUB_URL, LOCAL_KEY, TOOL_IMAGE
import asyncio,contextvars,datetime,fcntl,hashlib,json,math,os,re,time,traceback,subprocess
from typing import Any
import httpx
from pydantic import Field
from base.agent.base_action import BaseAction
from base.engine.async_llm import AsyncLLM,LLMsConfig,create_llm_instance
import base.engine.async_llm as engine
from base.agent.memory import Memory
from base.engine.utils import parse_llm_action_response as fallback_parse
from benchmark.common.env import BasicInfo,Environment
from benchmark.common.runner import Runner
import orchestra_o1.main_agent as main_module
import orchestra_o1.subagents.react_agent as react_module
import orchestra_o1.tools.delegate as delegate_module
from orchestra_o1.main_agent import MainAgent
from orchestra_o1.subagents import ReActAgent
from orchestra_o1.tools.delegate import DelegateTaskTool,_make_serializable
from orchestra_o1.tools.complete import CompleteTool
from orchestra_o1.prompts.omnigaia import OmniGAIAMainAgentPrompt

R=SHARE_ROOT;D=R/'multimodal-eval';A=D/'assets'
MAIN='Qwen3.8-27B';SUB='Qwen3-Omni-30B-A3B-Instruct';KEY=LOCAL_KEY
CV=contextvars.ContextVar('case',default=None);ROOT=None
main_gate=asyncio.Semaphore(8);sub_gate=asyncio.Semaphore(8);media_gate=asyncio.Semaphore(2)
client=None

def event(kind,**kw):
 case=CV.get()
 if case and ROOT:
  out=ROOT/case['id'];out.mkdir(exist_ok=True)
  with (out/'events.jsonl').open('a') as f:f.write(json.dumps({'time':time.time(),'kind':kind,**kw},ensure_ascii=False,default=str)+'\n')

def parse(text):
 try:
  d=json.loads(text)
  if isinstance(d,dict) and isinstance(d.get('action'),str) and isinstance(d.get('params',{}),dict):return d
 except (TypeError,ValueError):pass
 return fallback_parse(text)
main_module.parse_llm_action_response=parse;react_module.parse_llm_action_response=parse

# Keep the original model configuration/usage accounting, add bounded local transport.
old_init=AsyncLLM.__init__
def init(self,*a,**kw):
 old_init(self,*a,**kw);self.aclient=self.aclient.with_options(timeout=900,max_retries=0)
 create=self.aclient.chat.completions.create
 async def wrapped(*args,**params):
  prompt=params['messages'][-1].get('content','');role='memory' if 'memory compression module' in str(prompt)[:300] else ('summary' if 'trajectory summarizer' in str(prompt)[:300] else 'agent')
  params['max_tokens']=8192 if role=='agent' and self.config.model==MAIN else (4096 if role=='agent' else 1024)
  if role=='agent':params['response_format']={'type':'json_object'}
  if self.config.model==MAIN:params.update(reasoning_effort='medium',extra_body={'chat_template_kwargs':{'enable_thinking':True}})
  if 'normalize_request' in globals():normalize_request(self.config.model,params)
  gate=main_gate if self.config.model==MAIN else sub_gate
  started=time.time();event('model_request',model=self.config.model,role=role,prompt=prompt,max_tokens=params['max_tokens'])
  try:
   async with gate:response=await create(*args,**params)
   event('model_response',model=self.config.model,role=role,seconds=time.time()-started,usage=response.usage.model_dump(),finish_reason=response.choices[0].finish_reason,response=response.choices[0].message.content)
   return response
  except Exception as exc:event('model_error',model=self.config.model,role=role,error=str(exc)[:1000]);raise
 self.aclient.chat.completions.create=wrapped
AsyncLLM.__init__=init
engine.DEFAULT_MAX_RETRIES=1

class PairedWorker(ReActAgent):
 async def step(self,observation,history,**kwargs):
  # Original ReAct loop, with actual step count and completed action/result pairing.
  if history:
   rec=history[-1];await self.memory.add_memory(obs=rec.observation if not rec.info else rec.info.get('last_action_result',rec.observation),action=rec.action,raw_response=rec.raw_response)
  n=len(history);prompt=self._build_prompt(observation,n+1,self.max_steps,self.max_steps-n-1,self._get_budget_warning(self.max_steps-n-1))
  prompt+='\nThis is a fixed multiple-choice perception task. Call the appropriate provided media tool to inspect the actual media, then finish with an option letter and brief supporting evidence. Do not search for benchmark answers. Use only declared tool names/parameters.'
  response=await self.llm(prompt);return self.parse_action(response),response,prompt
 # The base reset sets max_steps on current runtime; keep explicit for reproducibility.
 def reset(self,env_info):
  super().reset(env_info);self.max_steps=env_info.max_steps

delegate_module.ReActAgent=PairedWorker

class VerifiedDelegate(DelegateTaskTool):
 async def __call__(self,tasks=None,**kw):
  if tasks is not None:
   if not isinstance(tasks,list) or any(not isinstance(t,dict) for t in tasks):return {'error':'tasks must be an array of objects','subtask_results':[]}
   errors=[f'task {i}: model must explicitly be model_1' for i,t in enumerate(tasks) if t.get('model') not in ('model_1',SUB)]
   if errors:return {'error':'; '.join(errors),'subtask_results':[]}
  result=await super().__call__(tasks=tasks,**kw);event('delegation_result',result=_make_serializable(result));return result
 async def _summarize_trace(self,trace,task_instruction):
  # Bind to active worker model, rather than a repository hard-coded model string.
  if not trace:return 'No steps executed'
  llm=create_llm_instance(LLMsConfig.default().get(self.models[0]));return await llm('You are a trajectory summarizer. Summarize actual code changes, file paths, executed tests, observed results, and remaining errors in at most 6 bullets.\nTask: '+task_instruction+'\nTrace: '+self._trace_formatter.format_trace(trace))

class FeedbackMain(MainAgent):
 def _update_context(self,action,params,result):
  super()._update_context(action,params,result)
  errors=[result.get('error')]+[x.get('error') for x in result.get('subtask_results',[])]
  errors=[x for x in errors if x]
  if errors:self.context='Actual tool/delegation errors: '+json.dumps(errors)+'\n'+self.context

class ChoicePrompt:
 @staticmethod
 def build_prompt(*args,**kw):
  return OmniGAIAMainAgentPrompt.build_prompt(*args,**kw)+'\nEvaluation protocol: answer with exactly one option letter using complete(answer="A"). First delegate perception using the permitted media tool; include model="model_1" explicitly in EVERY task. Use the exact provided local media path. Keep instructions concise. Independent visual/audio checks may run in parallel, but prefer one comprehensive media inspection before extra calls. For the FIRST video inspection, omit max_frames, start_time and end_time so the full clip is processed with the fixed sampling protocol. Ask workers to finish promptly after obtaining sufficient evidence. You have 6 planning rounds. Summaries and tool errors are evidence; do not invent observed content.'

class CheckedComplete(CompleteTool):
 async def __call__(self,answer='',**extra):
  if extra or not isinstance(answer,str):return {'error':'complete accepts only answer (string)','done':False}
  return await super().__call__(answer=answer)

class MediaAction(BaseAction):
 case:dict=Field(exclude=True)
 kind:str=Field(exclude=True)
 async def __call__(self,query='',image_path=None,video_path=None,audio_path=None,analyze_audio=None,max_frames=None,start_time=None,end_time=None,**extra):
  if extra:return {'success':False,'error':'Unsupported parameters: '+','.join(extra)}
  case=self.case;p=Path(case['media_path']).resolve();given=image_path or video_path or audio_path
  if given and Path(given).name!=p.name and Path(given).resolve()!=p:return {'success':False,'error':'Only the media provided for this question may be accessed'}
  if not query:return {'success':False,'error':'query is required'}
  data=[];body={};details={'path':str(p),'media_type':self.kind}
  if self.kind=='image':
   data=[{'type':'image_url','image_url':{'url':p.as_uri()}}];body['mm_processor_kwargs']={'min_pixels':3136,'max_pixels':1003520}
  else:
   use_audio=bool(case.get('use_audio'));duration=case['actual_duration_seconds']
   if analyze_audio is not None and bool(analyze_audio)!=use_audio:return {'success':False,'error':f'This dataset protocol requires analyze_audio={use_audio}; retry with that setting'}
   start=max(0,float(start_time or 0));end=min(duration,float(end_time if end_time is not None else duration))
   if end<=start:return {'success':False,'error':'Empty temporal interval'}
   if start>0 or end<duration-.05:
    seg=A/'segments';seg.mkdir(exist_ok=True);target=seg/(hashlib.sha256((str(p)+str(start)+str(end)).encode()).hexdigest()+'.mp4')
    if not target.exists():
     args=[str(D/'bin/ffmpeg'),'-y','-ss',str(start),'-i',str(p),'-t',str(end-start),'-c:v','libx264','-preset','ultrafast','-crf','20','-c:a','aac',str(target)]
     proc=await asyncio.create_subprocess_exec(*args,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.PIPE);_,err=await asyncio.wait_for(proc.communicate(),120)
     if proc.returncode:return {'success':False,'error':'Segment decode failed: '+err.decode(errors='replace')[-500:]}
    p=target
   frames=max(2,math.ceil((end-start)*2));requested=max_frames
   if requested is not None:frames=min(frames,max(2,int(requested)))
   details.update(start=start,end=end,use_audio=use_audio,num_frames=frames,target_fps=2,max_pixels_per_frame=50176,requested_max_frames=requested)
   data=[{'type':'video_url','video_url':{'url':p.as_uri()}}]
   body={'mm_processor_kwargs':{'use_audio_in_video':use_audio,'min_pixels':3136,'max_pixels':50176},'media_io_kwargs':{'video':{'num_frames':frames,'fps':2}}}
  data.append({'type':'text','text':case['question']+'\n'+'\n'.join(case.get('options',[]))+'\nCurrent analysis request: '+query+'\nGround every observation in the provided media. State uncertainty if a detail is not visible or audible.'})
  payload={'model':SUB,'messages':[{'role':'user','content':data}],'temperature':0,'max_tokens':2048,**body}
  started=time.time();event('media_request',**details,query=query)
  try:
   async with media_gate,sub_gate:
    response=await client.post('http://127.0.0.1:8802/v1/chat/completions',headers={'Authorization':'Bearer '+KEY},json=payload)
    response.raise_for_status();d=response.json()
   text=d['choices'][0]['message']['content'];event('media_response',seconds=time.time()-started,usage=d.get('usage'),response=text,**details)
   return {'success':bool(text),'output':text,'metrics':{'usage':d.get('usage'),'media':details},'error':None if text else 'Empty perception response'}
  except Exception as exc:
   event('media_error',error=str(exc)[:1200],**details);return {'success':False,'error':type(exc).__name__+': '+str(exc)[:1200],'output':None}

class MediaEnv(Environment):
 def __init__(self,case,tools):
  self.case=case;self.tools={x.name:x for x in tools};self.instruction=case['question']+'\n'+'\n'.join(case.get('options',[]))+f"\nMedia file: {case['media_path']}\nInput protocol: "+('image only' if case['media_type']=='image' else f"video with analyze_audio={case.get('use_audio',False)}; no supplied subtitles")
 def clone(self):return MediaEnv(self.case,list(self.tools.values()))
 def get_basic_info(self):
  action_space='Available actions\n'+'\n\n'.join('### '+t.name+'\n'+t.description+'\nParameters: '+json.dumps(t.parameters) for t in self.tools.values())+'\n\n### finish\nParameters: result (option and evidence), status (done/partial), summary.'
  return BasicInfo(env_id=self.case['id'],instruction=self.instruction,action_space=action_space,max_steps=12,meta_data={'modal_inputs':[{'type':self.case['media_type'],'path':self.case['media_path']}],'category':self.case['category']})
 async def reset(self,seed=None):return {'instruction':self.instruction,'message':'Inspect provided media with declared tool. Do not access answers or other data files.'}
 async def step(self,action):
  name=action.get('action');params=action.get('params') or {}
  if name=='finish':
   finish={k:params.get(k,'') for k in ['result','status','summary']};return {'finish_result':finish},0.,True,{'finished':True,'finish_result':finish}
  tool=self.tools.get(name)
  if tool is None:result={'success':False,'error':'Unknown action; use '+', '.join(self.tools)+' or finish'}
  else:result=await tool(**params)
  event('tool_result',action=action,result=result)
  return result,0.,False,{'last_action_result':result}

def tools_for(case):
 if case['media_type']=='image':name='ImageAnalysisAction';kind='image';props={'query':{'type':'string'},'image_path':{'type':'string'}}
 else:name='VideoAnalysisAction';kind='video';props={'query':{'type':'string'},'video_path':{'type':'string'},'analyze_audio':{'type':'boolean'},'max_frames':{'type':'integer'},'start_time':{'type':'number'},'end_time':{'type':'number'}}
 return [MediaAction(name=name,kind=kind,case=case,description='Inspect ONLY the actual local media supplied for this question using the active Omni model. Video uses 2 FPS by default and full duration unless a temporal interval or max_frames is explicitly requested. Audio mode must match the question protocol.',parameters={'type':'object','properties':props,'required':['query',kind+'_path'],'additionalProperties':False})]

def letter(answer,case):
 text=str(answer).strip().strip('`').strip()
 m=re.fullmatch(r'\(?([A-Z])\)?[.)]?',text,re.I)
 if m:return m.group(1).upper()
 m=re.fullmatch(r'(?:answer|option|choice|答案)\s*[:：]?\s*\(?([A-Z])\)?[.)]?',text,re.I)
 return m.group(1).upper() if m else None

async def run_case(case):
 token=CV.set(case);out=ROOT/case['id'];out.mkdir(exist_ok=True);(out/'input.json').write_text(json.dumps(case,ensure_ascii=False,indent=2));rounds=[];answer=None
 try:
  tools=tools_for(case);env=MediaEnv(case,tools);runner=Runner();runner.step_timeout=900
  delegate=VerifiedDelegate(env=env,runner=runner,models=[SUB],benchmark_type='omnigaia',alias_to_model={'model_1':SUB})
  main=FeedbackMain(llm=create_llm_instance(LLMsConfig.default().get(MAIN)),sub_models=[SUB],tools=[delegate,CheckedComplete()],subagent_tools=tools,prompt_builder=ChoicePrompt,max_attempts=6,benchmark_type='omnigaia',mask_model_names=True)
  main.reset(env.get_basic_info())
  async def solve():
   nonlocal answer
   for i in range(6):
    action,response=await main.step(None,[]);
    if action.get('error'):main.context='Protocol error from last action: '+str(action['error'])+'\n'+main.context
    rounds.append({'round':i+1,'action':_make_serializable(action),'raw_response':response});(out/'trajectory.json').write_text(json.dumps(rounds,ensure_ascii=False,indent=2))
    if action.get('action')=='complete' and (action.get('result') or {}).get('done'):
     answer=action['result'].get('answer');break
  await asyncio.wait_for(solve(),1800)
  prediction=letter(answer,case);result={'status':'completed' if prediction else 'answer_format_error','answer':answer,'prediction':prediction,'rounds':len(rounds)}
 except asyncio.TimeoutError:result={'status':'time_budget_exhausted','answer':answer,'prediction':letter(answer,case),'rounds':len(rounds)}
 except Exception:result={'status':'runtime_error','error':traceback.format_exc(),'answer':answer,'prediction':None,'rounds':len(rounds)}
 finally:CV.reset(token)
 events=[json.loads(x) for x in (out/'events.jsonl').read_text().splitlines()] if (out/'events.jsonl').exists() else []
 result.update(media_calls=sum(x['kind']=='media_response' for x in events),tool_errors=sum(x['kind']=='media_error' for x in events),model_errors=sum(x['kind']=='model_error' for x in events))
 return result

async def main():
 global ROOT,client
 lock=(R/'run/mm-o1-30.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 batch='o1-qwen38-omni-mm30-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S');ROOT=R/'deliverables'/batch;ROOT.mkdir()
 state={'phase':'waiting_for_data','batch':batch,'started':time.time(),'models':{'main':MAIN,'sub':SUB},'concurrency':8,'limits':{'main_rounds':6,'worker_steps':12,'case_seconds':1800,'api_seconds':900,'main_output_tokens':8192,'sub_output_tokens':2048,'media_output_tokens':2048},'datasets':{},'results':{},'output_dir':str(ROOT),'contexts':{'main':262144,'sub':65536},'framework':'Original Orchestra-o1 MainAgent + DelegateTaskTool + ReActAgent + Runner; local dataset/media transport adapter','adaptations':['strict whole JSON parsing before original fallback','actual worker step counts and completed action/result memory pairing','nested delegate parameter validation and error feedback','summary uses configured worker model','fixed manifests and gold-free agent environment','exact option matching; runtime errors reported separately'],'protocol':{'MMStar':'images up to 1003520 pixels','WorldSense':'full <=120s video, audio+video, no supplied subtitles, 2 FPS and 50176 pixels/frame by default','Video-MME':'short split, visual-only video, no subtitles, 2 FPS and 50176 pixels/frame by default','caveat':'Only 10 selected questions per dataset, not full official evaluation; explicit segment/frame requests recorded per tool call.'}}
 status=R/'logs/mm-o1-30-status.json'
 def save():
  state['updated']=time.time()
  for p in [status,ROOT/'status.json']:
   temp=p.with_suffix('.tmp');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2));temp.replace(p)
 save()
 for _ in range(720):
  if all((D/(n+'.json')).exists() and (D/(n+'.private.json')).exists() for n in ['MMStar','WorldSense','Video-MME']):break
  p=R/'logs/mm-data-preparation.json'
  if p.exists() and json.loads(p.read_text())['phase']=='failed':raise RuntimeError('Dataset preparation failed')
  await asyncio.sleep(5)
 else:raise RuntimeError('Dataset preparation timeout')
 LLMsConfig._default_config=LLMsConfig({MAIN:{'api_key':KEY,'base_url':MAIN_URL,'temperature':1.,'top_p':.95},SUB:{'api_key':KEY,'base_url':SUB_URL,'temperature':0.,'top_p':1.}})
 cases=[];gold={}
 for name in ['MMStar','WorldSense','Video-MME']:
  rows=json.loads((D/(name+'.json')).read_text());assert len(rows)==10
  private=json.loads((D/(name+'.private.json')).read_text());gold.update({r['id']:r['answer'].strip().upper() for r in private})
  for row in rows:row['dataset']=name
  cases.extend(rows);state['datasets'][name]={'total':10,'finished':0,'correct':0,'runtime_errors':0}
  (ROOT/(name+'-selection.json')).write_text(json.dumps(rows,ensure_ascii=False,indent=2))
 # Interleave datasets so all three produce early results rather than waiting for one category.
 cases=sorted(cases,key=lambda r:(next(i for i,x in enumerate(json.loads((D/(r['dataset']+'.json')).read_text())) if x['id']==r['id']),r['dataset']))
 client=httpx.AsyncClient(timeout=httpx.Timeout(900,connect=20),limits=httpx.Limits(max_connections=32));gate=asyncio.Semaphore(8)
 state['phase']='running';save()
 async def one(case):
  async with gate:
   started=time.time();state['results'][case['id']]={'status':'running','dataset':case['dataset'],'started':started};save()
   result=await run_case(case);result.update(dataset=case['dataset'],expected_answer=gold[case['id']],correct=result.get('prediction')==gold[case['id']],seconds=round(time.time()-started,2));state['results'][case['id']]=result
   d=state['datasets'][case['dataset']];d['finished']+=1;d['correct']+=int(result['correct']);d['runtime_errors']+=int(result['status'] in ('runtime_error','time_budget_exhausted'))
   (ROOT/case['id']/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));save()
 try:await asyncio.gather(*(one(case) for case in cases));state.update(phase='finished',finished=time.time());save()
 finally:await client.aclose()

if __name__=='__main__':asyncio.run(main())
