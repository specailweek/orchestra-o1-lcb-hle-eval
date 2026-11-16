from pathlib import Path
import os
from share_settings import ROOT as SHARE_ROOT, MAIN_PATH, SUB_PATH, MAIN_URL, SUB_URL, LOCAL_KEY, TOOL_IMAGE
import json,sys,base64,zlib,pickle,importlib.util,multiprocessing,traceback
root=Path('/eval');row=json.loads((root/'problem.json').read_text());code=(root/'candidate.py').read_text();public=json.loads(row['public_test_cases'])
if '--public-only' in sys.argv:tests=public
else:
 try:private=json.loads(row['private_test_cases'])
 except Exception:private=json.loads(pickle.loads(zlib.decompress(base64.b64decode(row['private_test_cases']))))
 tests=public+private
metadata=json.loads(row['metadata']) if isinstance(row['metadata'],str) else row['metadata'];sample={'input_output':json.dumps({'inputs':[t['input'] for t in tests],'outputs':[t['output'] for t in tests],'fn_name':metadata.get('func_name')})}
spec=importlib.util.spec_from_file_location('official_checker','/checker/testing_util.py');checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
assert tests,'No tests is an invalid evaluation'
def evaluate(shared):
 try:
  result,detail=checker.run_test(sample,test=code,debug=False,timeout=6);shared['result']=[v.item() if hasattr(v,'item') else v for v in result];shared['detail']=detail
 except BaseException:shared['runner_error']=traceback.format_exc()
with multiprocessing.Manager() as manager:
 shared=manager.dict();p=multiprocessing.Process(target=evaluate,args=(shared,));p.start();p.join((6+1)*len(tests)+5)
 if p.is_alive():
  p.kill();p.join();converted=[-1]*len(tests);detail={'error_message':'GlobalTimeLimitExceeded'}
 elif 'result' not in shared:raise RuntimeError(shared.get('runner_error','Judge subprocess exited without a result'))
 else:converted=list(shared['result']);detail=shared['detail']
passed=len(converted)==len(tests) and all(v is True or (isinstance(v,(int,float)) and v>0) for v in converted)
Path('/out/verdict.json').write_text(json.dumps({'passed':passed,'test_count':len(tests),'results':converted,'details':detail},default=str))
