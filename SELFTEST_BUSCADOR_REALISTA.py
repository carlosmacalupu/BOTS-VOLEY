"""Prueba offline del buscador FMV con fecha fija 01/10/2026.
Fuerza fallo del origen directo y exige que el Reader encuentre ULP-HARRODS.
No depende de la fecha real del día en que se ejecute el self-test.
"""
import os,tempfile,importlib.util
os.environ['TELEGRAM_BOT_TOKEN']='test'
os.environ['VOLEY_DATA_DIR']=tempfile.mkdtemp(prefix='voley_selftest_')
spec=importlib.util.spec_from_file_location('bot_test','bot.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
RENDERED='''
# Club Universitario de La Plata
## Próximos partidos
[51232 Campeonato Oficial ULP #1 jue 1 oct 21:30 Superiores Primera · Femenino ULP HARRODS](https://metrovoley.com.ar/matches/276419)
'''
def fail_direct(*a,**k): raise TimeoutError('origin blocked')
b._links_from=fail_direct
b._reader_text=lambda url,timeout=10: RENDERED
b.sources.search_extra=lambda *a,**k:[]
b.sources.public_calendars.flashscore_candidates=lambda *a,**k:[]
rows=b.fmv_fast_search('Universitario de La Plata','2026-10-01')
assert rows, 'sin fixture FMV'
m=rows[0]
assert m['teams']['home']['name']=='Universitario de La Plata',m
assert m['teams']['away']['name']=='Harrods',m
assert b.sources.day_of(m)=='2026-10-01',m
print('OK — origen FMV directo caído; Reader localizó ULP vs Harrods sin OpenAI')
