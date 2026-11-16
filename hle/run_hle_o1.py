"""HLE stratified subset on the original Orchestra-o1 team, with isolated tools."""
from pathlib import Path
import os
from share_settings import ROOT as SHARE_ROOT, MAIN_PATH, SUB_PATH, MAIN_URL, SUB_URL, LOCAL_KEY, TOOL_IMAGE
import asyncio,json,time,os,re,fcntl,traceback,base64,hashlib,random,socket,ipaddress,urllib.parse,io
from html.parser import HTMLParser
from pydantic import Field
import httpx
from PIL import Image,ImageDraw
import hle_runtime as r
from hle_schemas import WORKER_ACTION_SCHEMA,worker_schema
m=r.m;D=r.D;ROOT=r.ROOT;client=None;image_gate=asyncio.Semaphore(1);search_counts=json.loads((D/'search-ledger.json').read_text()) if (D/'search-ledger.json').exists() else {}
CREDS={k:os.environ[k] for k in ['SERPER_API_KEY','SERPER_BASE_URL'] if k in os.environ}

def atomic(path,data):
 tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str));tmp.replace(path)
old_parse=m.parse
def safe_parse(text):
 value=old_parse(text)
 if isinstance(value,dict) and not isinstance(value.get('params',{}),dict):return {'action':'invalid_action','params':{},'error':'params must be a JSON object, not a string or list'}
 return value
m.main_module.parse_llm_action_response=safe_parse;m.react_module.parse_llm_action_response=safe_parse
class PageText(HTMLParser):
 def __init__(self):super().__init__();self.parts=[];self.hidden=0
 def handle_starttag(self,tag,attrs):
  if tag in ['script','style','nav']:self.hidden+=1
 def handle_endtag(self,tag):
  if tag in ['script','style','nav']:self.hidden=max(0,self.hidden-1)
 def handle_data(self,text):
  if not self.hidden and text.strip():self.parts.append(text.strip())
class Search(m.BaseAction):
 name:str='GoogleSearchAction';description:str='Search external academic and factual sources. Max10 queries per question. Do not search benchmark IDs, HLE answer dumps, or standard answers.';parameters:dict={'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}
 async def __call__(self,query):
  case=m.CV.get();ident=case['id'];count=search_counts.get(ident,0)
  if count>=10:return {'success':False,'error':'Per-question search budget10 exhausted'}
  if ident.startswith('hle-') and sum(v for k,v in search_counts.items() if k.startswith('hle-'))>=5000:return {'success':False,'error':'Global5000-query research budget exhausted'}
  if not isinstance(query,str) or not query.strip() or len(query)>1200:return {'success':False,'error':'query must be nonempty text of at most1200 chars'}
  if any(term in query.lower() for term in ['cais/hle','humanity\'s last exam','hle benchmark','hle answers',case.get('original_id','NO-ID-MATCH')]):return {'success':False,'error':'Search for underlying subject knowledge, not benchmark IDs or benchmark answer dumps'}
  parsed=urllib.parse.urlsplit(CREDS.get('SERPER_BASE_URL','https://google.serper.dev').strip());assert parsed.scheme=='https' and parsed.hostname=='google.serper.dev' and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
  base='https://google.serper.dev'
  key=CREDS.get('SERPER_API_KEY')
  if not key:return {'success':False,'error':'Search credential unavailable'}
  search_counts[ident]=count+1;atomic(D/'search-ledger.json',search_counts);m.event('search_started',query=query,index=count+1)
  try:
   response=await client.post(base+'/search',headers={'X-API-KEY':key},json={'q':query,'num':5},timeout=45);response.raise_for_status();data=response.json();result={'success':True,'results':[{k:x.get(k) for k in ['title','link','snippet']} for x in data.get('organic',[])[:5]],'queries_used':count+1,'credits_this_request':data.get('credits')}
  except Exception as exc:result={'success':False,'error':type(exc).__name__,'queries_used':count+1}
  m.event('search_completed',result=result);return result
async def safe_url(url):
 parsed=urllib.parse.urlsplit(url)
 if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password:raise ValueError('Only public HTTP(S) URLs without credentials')
 addresses=await asyncio.get_running_loop().run_in_executor(None,lambda:socket.getaddrinfo(parsed.hostname,parsed.port or (443 if parsed.scheme=='https' else 80),type=socket.SOCK_STREAM))
 if any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):raise ValueError('Private/local addresses are not accessible')
 return url
class ReadURL(m.BaseAction):
 name:str='ExtractUrlContentAction';description:str='Read a public webpage or PDF discovered during research. Private/local addresses and benchmark dataset files are blocked.';parameters:dict={'type':'object','properties':{'url':{'type':'string'}},'required':['url'],'additionalProperties':False}
 async def __call__(self,url):
  if any(x in url.lower() for x in ['cais/hle','centerforaisafety/hle','sample.gold','test-00000-of-00001.parquet']):return {'success':False,'error':'Benchmark data and answers are not research sources'}
  try:
   for _ in range(4):
    await safe_url(url)
    async with client.stream('GET',url,follow_redirects=False,timeout=45) as response:
     if response.status_code in [301,302,303,307,308]:url=urllib.parse.urljoin(url,response.headers['location']);continue
     response.raise_for_status();buf=bytearray()
     async for chunk in response.aiter_bytes():
      buf.extend(chunk)
      if len(buf)>12*1024*1024:raise ValueError('Document exceeds12MiB read limit')
     content=bytes(buf);ctype=response.headers.get('content-type','')
    if 'pdf' in ctype or content.startswith(b'%PDF'):
     from pypdf import PdfReader
     doc=PdfReader(io.BytesIO(content));text='\n'.join(page.extract_text() or '' for page in doc.pages[:30])
    else:
     parser=PageText();parser.feed(content.decode('utf-8',errors='replace'));text='\n'.join(parser.parts)
    result={'success':True,'url':url,'text':text[:24000],'truncated':len(text)>24000};m.event('url_read',url=url,characters=len(text));return result
   return {'success':False,'error':'Too many redirects'}
  except Exception as exc:return {'success':False,'error':type(exc).__name__+': '+str(exc)[:180]}
class Vision(m.BaseAction):
 name:str='ImageAnalysisAction';description:str='Inspect the actual question image using Omni. Supply a focused query. Image is supplied automatically from this question, never invented.';parameters:dict={'type':'object','properties':{'query':{'type':'string'}},'required':['query'],'additionalProperties':False}
 async def __call__(self,query):
  case=m.CV.get();path=case.get('image_path')
  if not path:return {'success':False,'error':'This question has no image'}
  raw=Path(path).read_bytes();mime='image/png' if raw.startswith(b'\x89PNG') else 'image/jpeg';url='data:'+mime+';base64,'+base64.b64encode(raw).decode();text='Inspect the supplied image faithfully. '+str(query)[:16000]+'\nQuestion: '+case['question'][:24000]
  body={'model':m.SUB,'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':url}},{'type':'text','text':text}]}],'temperature':0,'max_tokens':4096,'mm_processor_kwargs':{'min_pixels':3136,'max_pixels':6021120}}
  r.request_token_cost.set(min(60000,len(r.sub_tokenizer.encode(text))+4096+12000));m.event('image_analysis_started',image_sha256=hashlib.sha256(raw).hexdigest(),query=query)
  try:
   async with image_gate,m.sub_gate:
    response=await client.post('http://127.0.0.1:8802/v1/chat/completions',headers={'Authorization':'Bearer '+m.KEY},json=body,timeout=600);response.raise_for_status();data=response.json()
   result={'success':True,'analysis':data['choices'][0]['message'].get('content') or '','usage':data.get('usage')};m.event('image_analysis_completed',result=result);return result
  except Exception as exc:result={'success':False,'error':type(exc).__name__+': '+str(exc)[:250]};m.event('image_analysis_error',result=result);return result
def tools():return [r.Execute(),r.WriteFile(),r.RunFile(),Search(),ReadURL(),Vision()]
class Env(m.MediaEnv):
 def __init__(self,case):self.case=case;self.instruction=case['question'];self.tools={tool.name:tool for tool in tools()}
 def clone(self):return Env(self.case)
 def get_basic_info(self):return m.BasicInfo(env_id=self.case['id'],instruction=self.instruction,action_space='\n'.join(t.name+': '+t.description+' Schema:'+json.dumps(t.parameters) for t in self.tools.values())+'\nfinish: result,status,summary',max_steps=16,meta_data={'category':'HLE-academic','image_present':bool(self.case.get('image_path'))})
 async def reset(self,seed=None):return {'instruction':self.instruction,'workspace':'/workspace','has_image':bool(self.case.get('image_path')),'message':'Use actual tool evidence, derive an exact answer, and finish with conclusion and uncertainty.'}
 async def step(self,action):
  try:return await super().step(action)
  except (TypeError,ValueError,KeyError) as exc:
   result={'success':False,'error':'Invalid tool arguments: '+str(exc)[:300]};m.event('tool_result',action=action,result=result);return result,0.,False,{'last_action_result':result}
async def worker_step(self,observation,history,**kw):
 if history:
  z=history[-1];await self.memory.add_memory(obs=z.info.get('last_action_result',z.observation) if z.info else z.observation,action=z.action,raw_response=z.raw_response)
 schema={t.name:t.parameters for t in tools() if not self.allowed_tools or t.name in self.allowed_tools};warning=self._get_budget_warning(self.max_steps-len(history));repeat=''
 if len(history)>=2 and history[-1].action==history[-2].action:repeat='Repeated identical action detected. Read its result and advance reasoning, change approach or finish.'
 prompt=f'''You are an expert SubAgent in the original Orchestra-o1 team solving difficult academic questions. Reason carefully, perform useful calculations or research, and inspect the actual image via ImageAnalysisAction when provided. Search for subject knowledge, never benchmark answers. Do not turn a non-coding question into an unnecessary programming task. Files persist, Python variables do not. Return one JSON action,params object. Allowed schemas: {json.dumps(schema)}. finish params: result (exact proposed answer and supporting reasoning),status(done/partial),summary. Task:{self.task_instruction}\nContext:{self.context}\nOriginal question:{self.original_question}\nMemory:{self._get_memory()}\nLatest observation:{observation}\nStep:{len(history)+1}/{self.max_steps}\n{warning}\n{repeat}'''
 prompt=r.bounded(prompt,55000,r.sub_tokenizer);schema_token=WORKER_ACTION_SCHEMA.set(worker_schema(schema))
 try:response=await self.llm(prompt)
 finally:WORKER_ACTION_SCHEMA.reset(schema_token)
 return self.parse_action(response),response,prompt
m.PairedWorker.step=worker_step
class Complete(m.BaseAction):
 name:str='complete';description:str='Submit an exact final answer, brief explanation and optional confidence0-100. No reference answer or judge feedback is available.';parameters:dict={'type':'object','properties':{'answer':{'type':'string'},'explanation':{'type':'string'},'confidence':{'type':'number','minimum':0,'maximum':100}},'required':['answer'],'additionalProperties':False}
 async def __call__(self,answer='',explanation='',confidence=None,**extra):
  if extra or not isinstance(answer,str) or not answer.strip():return {'done':False,'error':'Use answer:string,explanation:string,confidence:number0-100 only'}
  if confidence is not None:
   try:confidence=float(str(confidence).rstrip('%'))
   except (TypeError,ValueError):return {'done':False,'error':'confidence must be a number0-100'}
   if not 0<=confidence<=100:return {'done':False,'error':'confidence must be0-100'}
  text=f'Explanation: {str(explanation)}\nAnswer: {answer.strip()}'
  if confidence is not None:text+=f'\nConfidence: {confidence:g}%'
  payload={'answer':answer.strip(),'explanation':str(explanation),'confidence':confidence,'response':text};atomic(ROOT/m.CV.get()['id']/'prediction.json',payload);return {'done':True,**payload}
class Main(m.FeedbackMain):
 def _format_subtask_history(self):return r.bounded(super()._format_subtask_history(),44000)
class Prompt:
 @staticmethod
 def build_prompt(instruction,meta,prior_context,attempt_index,max_attempts,sub_models,subtask_history='',model_to_alias=None,tools=None):
  example=json.dumps({'action':'delegate_task','reasoning':'Need actual image evidence.','params':{'tasks':[{'task_instruction':'Inspect the supplied question image and report the relevant observations.','context':'Use the actual provided image, not an imagined reconstruction.','model':'model_1','tools':['ImageAnalysisAction']}]}})
  complete=json.dumps({'action':'complete','reasoning':'The conclusion follows from the verified evidence.','params':{'answer':'<exact answer>','explanation':'<brief explanation>','confidence':80}})
  return r.bounded(f'''You are MainAgent of the original Orchestra-o1 academic reasoning team. Solve the question, using specialist model_1 (Qwen3-Omni) for independent reasoning, calculations, public-source research and image inspection as useful. Only delegated workers can call external tools. For an image question, delegate inspection of the actual provided image via ImageAnalysisAction. Do not invent image content or sources. Do not search benchmark answer dumps or question IDs. Final answer must be precise and directly address the requested quantity/name/option. Output exactly one JSON object. Allowed actions are delegate_task and complete. Never emit a tool name as the root action; wrap tool work in delegate_task. Delegation JSON example:{example}\nCompletion JSON example:{complete}\nConfidence must reflect uncertainty.\nQUESTION:{instruction}\nROUND:{attempt_index}/{max_attempts}\nWORKER RESULTS:{subtask_history}\nERROR FEEDBACK:{prior_context[-12000:]}''',47000)
def make_case(row):
 original=str(row['id']);ident='hle-'+hashlib.sha256(original.encode()).hexdigest()[:24];folder=ROOT/ident;(folder/'workspace').mkdir(parents=True,exist_ok=True);image=row.get('image');image_path=None
 if image:
  if isinstance(image,str) and image.startswith('data:'):raw=base64.b64decode(image.split(',',1)[1])
  elif isinstance(image,dict) and image.get('bytes'):raw=image['bytes']
  else:raise ValueError('Expected dataset-embedded image, not an external URL')
  loaded=Image.open(io.BytesIO(raw));loaded.verify();image_path=folder/'workspace/question-image';image_path.write_bytes(raw)
 question=row['question']+('\n[An actual question image is supplied. Ask model_1 to inspect it using ImageAnalysisAction.]' if image_path else '')
 return {'id':ident,'original_id':original,'question':question,'image_path':str(image_path) if image_path else None,'subject':row.get('category',row.get('subject')),'answer_type':row['answer_type']}
async def solve(row):
 case=make_case(row);folder=ROOT/case['id'];token=m.CV.set(case);start=time.time();rounds=[];result=None
 try:
  env=Env(case);runner=m.Runner();runner.step_timeout=1800;delegate=m.VerifiedDelegate(env=env,runner=runner,models=[m.SUB],benchmark_type='omnigaia',alias_to_model={'model_1':m.SUB});main=Main(llm=m.create_llm_instance(m.LLMsConfig.default().get(m.MAIN)),sub_models=[m.SUB],tools=[delegate,Complete()],subagent_tools=list(env.tools.values()),prompt_builder=Prompt,max_attempts=10,benchmark_type='omnigaia',mask_model_names=True);main.reset(env.get_basic_info())
  async def loop():
   nonlocal result
   for i in range(10):
    action,raw=await main.step(None,[])
    if action.get('error'):main.context='Last action protocol error: '+str(action['error'])+'\n'+main.context
    rounds.append({'round':i+1,'action':m._make_serializable(action),'raw_response':raw});atomic(folder/'trajectory.json',rounds)
    if action.get('action')=='complete' and (action.get('result') or {}).get('done'):result=action['result'];return
  await asyncio.wait_for(loop(),2700);status='answered' if result else 'no_answer';error=None
 except asyncio.TimeoutError:status='solve_budget_exhausted';error='2700-second wall-clock budget exhausted'
 except Exception as exc:status='infrastructure_error';error=type(exc).__name__+': '+str(exc)[:500]
 finally:m.CV.reset(token)
 counts={}
 if (folder/'events.jsonl').exists():
  for line in (folder/'events.jsonl').read_text().splitlines():
   e=json.loads(line);counts[e['kind']]=counts.get(e['kind'],0)+1
 output={'id':case['original_id'],'folder':case['id'],'status':status,'seconds':time.time()-start,'rounds':len(rounds),'error':error,'subject':case['subject'],'answer_type':case['answer_type'],'modality':'image' if case['image_path'] else 'text','delegated':counts.get('delegation_result',0)>0,'image_inspected':counts.get('image_analysis_completed',0)>0,'search_queries':search_counts.get(case['id'],0),'counts':counts,'scoring_status':'pending_official_judge' if result else 'no_final_answer','prediction':result};atomic(folder/'result.json',output);return output
async def preflight():
 report={};case={'id':'validation-tools','original_id':'SYNTHETIC-NOT-HLE','question':'A synthetic image shows a word. Read it exactly.'};folder=ROOT/case['id'];(folder/'workspace').mkdir(parents=True,exist_ok=True);im=Image.new('RGB',(700,220),'white');draw=ImageDraw.Draw(im)
 from PIL import ImageFont
 try:font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',70)
 except OSError:font=ImageFont.load_default(size=70)
 draw.text((40,50),'ORANGE',fill='black',font=font);path=folder/'workspace/image.png';im.save(path);case['image_path']=str(path);token=m.CV.set(case)
 try:
  code=await r.Execute()(code='print(int(input())+1)',stdin='2\n');assert code.get('success') and code['stdout'].strip()=='3',code;report['python_stdin']=True
  image=await Vision()(query='Read the single large word. Respond with that word only.');assert image.get('success') and 'ORANGE' in image['analysis'].upper(),image;report['live_omni_image']=True
  out=await Search()(query='site:lastexam.ai academic benchmark');assert out.get('success') and out['results'],out;report['live_search']=True
  done=await Complete()(answer='ORANGE',explanation='Observed the word in the supplied image.',confidence=100);assert done['done'] and 'Answer: ORANGE' in done['response'];report['official_answer_export']=True
 finally:m.CV.reset(token)
 assert safe_parse('{"action":"complete","params":"bad"}')['action']=='invalid_action';report['malformed_params_feedback']=True
 return report
async def main():
 global client
 lock=(D/'evaluation.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);p=D/'status.json';state=json.loads(p.read_text()) if p.exists() else {'started':time.time(),'dataset':'HLE official stratified20%','models':{'main':m.MAIN,'sub':m.SUB},'contexts':{'main':65536,'sub':65536},'framework':'Original Orchestra-o1 team with calculator, search, document and image tools','setting':'With-tools; not the official no-tools single-model baseline','concurrency':6,'main_requests':4,'sub_requests':2,'results':{}}
 state.update(pid=os.getpid(),phase='initializing',updated=time.time(),error=None,run_profile=json.loads((D/'run-profile.json').read_text()));save=lambda:atomic(p,{**state,'updated':time.time()});save()
 m.LLMsConfig._default_config=m.LLMsConfig({m.MAIN:{'api_key':m.KEY,'base_url':MAIN_URL,'temperature':.6,'top_p':.95},m.SUB:{'api_key':m.KEY,'base_url':SUB_URL,'temperature':0.,'top_p':1.}});m.main_gate=r.TokenBudgetGate('main',4,int(json.loads((D/'profile-validation.json').read_text())['main_admission_budget']));m.sub_gate=r.TokenBudgetGate('sub',2,70000)
 async with httpx.AsyncClient(timeout=600,follow_redirects=False,limits=httpx.Limits(max_connections=16)) as session:
  client=session
  if not state.get('preflight_passed'):
   state['phase']='validating_tools';save();state['preflight']=await preflight();state['preflight_passed']=True;save()
  rows=json.loads((D/'data/sample.public.json').read_text());report=json.loads((D/'data/sampling-report.json').read_text());assert len(rows)==report['sample_size'];state.update(total=len(rows),sampling=report,phase='running',active_tasks=[]);save()
  # Predeclare a10-question compatibility pilot, including up to2 image questions, without reading gold.
  rng=random.Random(42);images=[x for x in rows if x.get('image')];text=[x for x in rows if not x.get('image')];pilot=rng.sample(images,min(2,len(images)));pilot+=rng.sample(text,min(10-len(pilot),len(text)));pilot_ids=[str(x['id']) for x in pilot];others=[x for x in rows if str(x['id']) not in pilot_ids];rng.shuffle(others);state['pilot_ids']=pilot_ids;save()
  gate=asyncio.Semaphore(6)
  async def run(row):
   async with gate:
    ident=str(row['id'])
    if ident in state['results']:return
    state['active_tasks'].append(ident);save();case_id='hle-'+hashlib.sha256(ident.encode()).hexdigest()[:24];rp=ROOT/case_id/'result.json'
    if rp.exists():out=json.loads(rp.read_text())
    elif (ROOT/case_id/'prediction.json').exists():out={'id':ident,'folder':case_id,'status':'answered_before_restart','prediction':json.loads((ROOT/case_id/'prediction.json').read_text()),'scoring_status':'pending_official_judge'}
    else:
     folder=ROOT/case_id
     if folder.exists():
      diag=D/'interruption-diagnostics';diag.mkdir(exist_ok=True);folder.rename(diag/(case_id+'-'+str(time.time_ns())))
     out=await solve(row)
    state['results'][ident]=out;state['active_tasks'].remove(ident);state['completed']=len(state['results']);state['answered']=sum(bool(v.get('prediction')) for v in state['results'].values());save();print('HLE_TASK_FINISHED',ident,out['status'],state['completed'],'/',len(rows),flush=True)
    predictions={k:{'model':m.MAIN+' + '+m.SUB+' / Orchestra-o1','response':v['prediction']['response']} for k,v in state['results'].items() if v.get('prediction')};atomic(D/'predictions.json',predictions)
  await asyncio.gather(*(run(row) for row in pilot));state['pilot_finished']=True;state['pilot_answered']=sum(bool(state['results'][ident].get('prediction')) for ident in pilot_ids);save()
  if state['pilot_answered']==0:raise RuntimeError('Pilot produced no final answers; full subset blocked for review')
  await asyncio.gather(*(run(row) for row in others));state.update(phase='predictions_finished',finished=time.time(),active_tasks=[]);save()
if __name__=='__main__':
 try:asyncio.run(main())
 except Exception:
  p=D/'status.json';s=json.loads(p.read_text()) if p.exists() else {};s.update(phase='failed',error=traceback.format_exc(),updated=time.time());atomic(p,s);raise
