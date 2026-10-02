from __future__ import annotations

import tempfile

import bot
from apifootball import Cache
from profiles import classify, PROFILES, study_quality
from sovereign import build, protected_floor, protected_ceiling, ladder
from test_legacy_claude import make_world


def test_categories_are_specialized():
    assert classify("Nicaragua U20", "Cuba U20", "U20 League").code == "youth"
    assert classify("Chelsea Women", "Arsenal Women", "WSL").code == "women"
    assert classify("River Reserve", "Boca Reserve", "Reserve League").code == "reserve"
    assert classify("Peru", "Chile", "Friendly").code == "friendly"
    assert classify("Peru", "Chile", "World Cup Qualifiers").code == "senior"
    assert PROFILES["youth"].half_life_days < PROFILES["senior"].half_life_days


def test_haiti_regression_floor_is_one_if_p0_62():
    dist = [0.062, 0.12, 0.18, 0.20, 0.16, 0.10, 0.08, 0.04, 0.025, 0.013]
    z = sum(dist); dist = [x/z for x in dist]
    assert protected_floor(dist, 0.08) == 1


def test_ceiling_comes_from_tail_not_old_reasoning():
    # 8+ = 3.9% => 7 is NOT protected at 3%; 9+ = 1.4% => ceiling 8.
    dist = [0.062, 0.10, 0.14, 0.17, 0.17, 0.13, 0.10, 0.089, 0.025, 0.014]
    z = sum(dist); dist = [x/z for x in dist]
    ce, pc = protected_ceiling(dist, 0.03)
    assert ce >= 8 and 1-pc <= 0.03 + 1e-12


def test_all_markets_come_from_one_distribution():
    dist = [0.07,0.15,0.22,0.20,0.14,0.10,0.08,0.025,0.015]
    z=sum(dist); dist=[x/z for x in dist]
    over, under = ladder(dist)
    assert abs(over[0.5] - (1-dist[0])) < 1e-12
    assert over[1.5] <= over[0.5]
    assert under[4.5] <= under[5.5] <= under[6.5] <= under[7.5]


def test_ai_fusion_stays_coherent():
    model = [0.10,0.18,0.24,0.20,0.13,0.08,0.04,0.02,0.01]
    # full over ladder from a second independent view
    op = {"over": {0.5:.95,1.5:.82,2.5:.61,3.5:.40,4.5:.24,5.5:.13,6.5:.07,7.5:.035,8.5:.015,9.5:.006},
          "under": {}, "quality": 85}
    s = build(model, op, PROFILES["senior"], 0.08, 0.03)
    assert abs(sum(s.dist)-1) < 1e-9
    assert s.p_over[1.5] <= s.p_over[0.5]
    assert s.p_under[4.5] <= s.p_under[5.5] <= s.p_under[6.5]
    assert s.floor in (0,1,2)


def test_pre_anchor_prevents_second_ai_call():
    api, store = make_world()
    class FakeAI:
        def __init__(self): self.calls=0; self.last_error=""
        def opinion(self, fx, over, under, live, data):
            self.calls += 1
            ov={l:max(0.001, .97 - .13*i) for i,l in enumerate(over)}
            un={l:min(.999, 1-ov.get(l,0.001)) for l in under}
            return {"over":ov,"under":un,"quality":88,"profile":"ESTABLE","resumen":"ok","riesgos":[],"faltan":[]}
    old=bot.AI
    ai=FakeAI(); bot.AI=ai
    try:
        fx=api.fixture_by_id(3, ttl=0)
        t1,_=bot.analyze_fixture(api,store,fx,[])
        # Si el ancla funciona, la segunda lectura no debe volver a pedir historiales.
        api.team_games = lambda *a, **k: (_ for _ in ()).throw(AssertionError("gastó API-Football pese al PRE ancla"))
        t2,_=bot.analyze_fixture(api,store,fx,[])
        assert ai.calls == 1
        assert "⚓ ANCLA" in t2
        # porcentajes y corredor del primer PRE se conservan
        key1=[x for x in t1.splitlines() if "+0.5 GOLES FT" in x][0]
        key2=[x for x in t2.splitlines() if "+0.5 GOLES FT" in x][0]
        assert key1 == key2
    finally:
        bot.AI=old


def test_detail_reuses_snapshot_without_ai():
    api, store = make_world()
    bot.ACTIVE_FID[0]=None
    old=bot.AI; bot.AI=None
    try:
        fx=api.fixture_by_id(3, ttl=0)
        bot.analyze_fixture(api,store,fx,[])
        text,_=bot.answer("detalle",api,store)
        assert "AUDITORÍA · MISMO PRE ANCLA" in text
        assert "IA no disponible" in text
    finally:
        bot.AI=old


def test_quality_is_not_same_for_all_categories():
    senior=study_quality(PROFILES["senior"],20,20,True,2,2,90)
    youth=study_quality(PROFILES["youth"],20,20,True,0,1,70)
    assert senior > youth
    assert youth <= PROFILES["youth"].quality_cap


def test_output_has_full_over_ladder():
    api, store = make_world(); old=bot.AI; bot.AI=None
    try:
        text,_=bot.answer('/partido 3',api,store)
        for x in ('+0.5','+1.5','+2.5','+3.5','+4.5','+5.5'):
            assert x in text
        assert 'LÍMITE VERDE OVER' in text and 'UNDER NATURAL' in text
    finally: bot.AI=old


if __name__ == '__main__':
    tests=[(n,f) for n,f in sorted(globals().items()) if n.startswith('test_')]
    for n,f in tests:
        f(); print('OK ',n)
    print(f'\n{len(tests)} pruebas V9 pasaron')
