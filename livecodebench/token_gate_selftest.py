import asyncio,contextvars,json
from pathlib import Path
# Test the exact gate class without loading model weights or accessing the network.
source=Path(__file__).with_name('run_lcb_o1.py').read_text();start=source.index('class TokenBudgetGate:');end=source.index('\ndef bounded',start)
class Events:
 @staticmethod
 def event(*args,**kwargs):pass
namespace={'asyncio':asyncio,'contextvars':contextvars,'time':__import__('time'),'request_token_cost':contextvars.ContextVar('test_cost',default=1),'m':Events()};exec(source[start:end],namespace);Gate=namespace['TokenBudgetGate'];cost=namespace['request_token_cost']
async def main():
 gate=Gate('test',4,100);peak={'requests':0,'tokens':0}
 print('TEST_STAGE admission',flush=True)
 async def request(tokens):
  cost.set(tokens)
  async with gate:
   peak['requests']=max(peak['requests'],gate.running);peak['tokens']=max(peak['tokens'],gate.tokens);assert gate.running<=4 and gate.tokens<=100;await asyncio.sleep(.01)
 await asyncio.gather(*(request(n) for n in [10]*8+[60]*4));assert peak['requests']==4 and gate.tokens==0 and gate.running==0
 print('TEST_STAGE waiting_cancel',flush=True)
 cost.set(90)
 async with gate:
  waiting=asyncio.create_task(request(60));await asyncio.sleep(.01);assert not waiting.done();waiting.cancel()
  try:await waiting
  except asyncio.CancelledError:pass
  assert gate.running==1 and gate.tokens==90
 print('TEST_STAGE active_cancel',flush=True)
 async def cancelled_active():
  cost.set(40)
  async with gate:await asyncio.sleep(10)
 running=asyncio.create_task(cancelled_active());await asyncio.sleep(.01);running.cancel()
 try:await running
 except asyncio.CancelledError:pass
 assert gate.tokens==0 and gate.running==0
 print('TEST_STAGE FIFO_fairness',flush=True)
 order=[]
 async def fair_request(tokens,label):
  cost.set(tokens)
  async with gate:order.append(label);await asyncio.sleep(.01)
 cost.set(60)
 async with gate:
  large=asyncio.create_task(fair_request(70,'large'));await asyncio.sleep(.01);small=asyncio.create_task(fair_request(20,'small'));await asyncio.sleep(.01);assert not order,'Small request bypassed waiting long-context request'
 await asyncio.gather(large,small);assert order==['large','small'];assert gate.tokens==0 and gate.running==0
 print('TOKEN_GATE_SELFTEST_PASSED',json.dumps(peak))
asyncio.run(main())
