import os,tempfile,importlib.util
os.environ['TELEGRAM_BOT_TOKEN']='test'
os.environ['VOLEY_DATA_DIR']=tempfile.mkdtemp(prefix='voley_selftest_')
spec=importlib.util.spec_from_file_location('bot_test2','bot.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
RENDERED='[51232 Campeonato Oficial ULP #1 jue 1 oct 21:30 Superiores Primera · Femenino ULP HARRODS](https://metrovoley.com.ar/matches/276419)'
calls={'direct':0,'reader':0,'openai':0}
def fail_direct(*a,**k): calls['direct']+=1; raise TimeoutError()
def reader(*a,**k): calls['reader']+=1; return RENDERED
def boom(*a,**k): calls['openai']+=1; raise AssertionError('OpenAI no debe ejecutarse al localizar')
b._links_from=fail_direct;b._reader_text=reader;b.engine.response_json=boom
rows=b.fmv_fast_search('Universitario de La Plata','2026-10-01')
assert rows
b.remember_matches(rows,'Universitario de La Plata')
first=dict(calls)
mem=b.memory_matches('ULP','2026-10-01')
assert mem and mem[0]['teams']['away']['name']=='Harrods',mem
assert calls==first,(first,calls)
assert calls['openai']==0,calls
print('OK — primera búsqueda por Reader; segunda desde memoria; OpenAI=0')
