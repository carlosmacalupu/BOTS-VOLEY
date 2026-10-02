"""Pruebas: `python test_all.py` (o `pytest`). No usan internet."""
import csv
import math
import os
import random
import tempfile
from datetime import timezone, datetime, timedelta

import backtest
import bot
from apifootball import ApiError, ApiFootball, Cache, parse_fixture, to_games
from model import (MIN_GAMES, Game, evaluate_bet, forecast, live_forecast, market_prob, remaining_fraction,
                   score_grid, team_rates, total_distribution)
from store import Store, compute_stats


def games(gf, ga, n=12, home=True, age=10):
    return [Game(gf, ga, home if i % 2 == 0 else not home, age + i * 7) for i in range(n)]


def test_distribucion_suma_uno_y_escalera_monotona():
    g = score_grid(1.6, 1.1)
    assert abs(sum(map(sum, g)) - 1) < 1e-9
    assert abs(sum(total_distribution(g)) - 1) < 1e-9
    fc = forecast(games(2, 1), games(1, 1))
    ps = [fc.p_under[l] for l in sorted(fc.p_under)]
    assert all(a <= b for a, b in zip(ps, ps[1:]))
    assert abs(fc.p_over[0.5] - (1 - fc.p_zero)) < 1e-12


def test_determinista():
    a = forecast(games(2, 1), games(1, 2))
    b = forecast(games(2, 1), games(1, 2))
    assert a.p_over == b.p_over and a.ceiling_line == b.ceiling_line


def test_fuerte_local_mas_goles_esperados_que_debil_visitante():
    fc = forecast(games(3, 0), games(0, 3))
    assert fc.lam_home > fc.lam_away * 2


def test_encogimiento_sin_datos():
    att, dfn, sw = team_rates([], 1.3)
    assert abs(att - 1) < 1e-12 and abs(dfn - 1) < 1e-12 and sw == 0


def test_pocos_partidos_se_rechaza():
    try:
        forecast(games(1, 1, n=MIN_GAMES - 1), games(1, 1))
        assert False
    except ValueError:
        pass


def test_techo_y_piso_coherentes():
    fc = forecast(games(2, 2), games(2, 2))
    assert fc.p_ceiling >= 0.97 and fc.ceiling_line == fc.ceiling + 0.5
    assert fc.p_under[fc.ceiling_line] == fc.p_ceiling
    low = forecast(games(0, 0), games(0, 0))
    assert low.floor == 0  # equipos que no marcan: sin piso protegido


def test_valoracion():
    e = evaluate_bet(0.95, 1.10, 500, 0.02, 0.02)
    assert abs(e["ev"] - (0.95 * 1.10 - 1)) < 1e-12
    assert abs(e["win"] - 50) < 1e-9 and e["loss"] == 500
    assert e["value"] is True            # 0.93*1.10-1 = +2.3%
    assert evaluate_bet(0.95, 1.05, 500, 0.02, 0.02)["value"] is False
    assert evaluate_bet(0.90, 1.10)["value"] is False


def test_parseo_de_apuestas_y_consulta():
    assert bot.parse_bets("+0.5 1.10 | -5.5@1,12") == [("over", 0.5, 1.10), ("under", 5.5, 1.12)]
    assert bot.parse_bets("u5.5 1.2") == [("under", 5.5, 1.2)]
    fid, h, a, bets = bot.parse_query("Boca Juniors vs River Plate | +0.5 1.08")
    assert (fid, h, a, bets) == (None, "Boca Juniors", "River Plate", [("over", 0.5, 1.08)])
    assert bot.parse_query("123456")[0] == 123456
    assert bot.parse_query("Nicaragua | +0.5 1.1") == (None, "Nicaragua", "", [("over", 0.5, 1.1)])
    assert bot.parse_query("Nicaragua") == (None, "Nicaragua", "", [])
    for bad in ("A vs B | +0.7 1.1", "A vs B | +0.5 0.9"):
        try:
            bot.parse_query(bad)
            assert False, bad
        except ValueError:
            pass


def fx(i, home, away, ts=0, status="NS", season=2026, hid=1, aid=2):
    return {"id": i, "ts": ts, "status": status, "home_id": hid, "home": home, "away_id": aid,
            "away": away, "league": "Liga", "country": "Peru", "season": season, "gh": None, "ga": None}


def test_busqueda_difusa_de_equipos():
    f = [fx(1, "Club Alianza Lima", "Sporting Cristal"), fx(2, "Universitario de Deportes", "Melgar")]
    assert [x["id"] for x in bot.search_fixtures(f, "alianza lima", "cristal")] == [1]
    assert [x["id"] for x in bot.search_fixtures(f, "Universitario", "Melgar")] == [2]
    assert [x["id"] for x in bot.search_fixtures(f, "melgar", "universitario")] == [2]  # orden invertido
    assert bot.search_fixtures(f, "Boca", "River") == []
    assert [x["id"] for x in bot.search_fixtures(f, "cristal")] == [1]                 # un solo equipo


def api_row(i, ts, hid, aid, gh, ga, status="FT", ft=None, elapsed=None):
    return {"fixture": {"id": i, "timestamp": ts, "status": {"short": status, "elapsed": elapsed}},
            "league": {"name": "L", "country": "C", "season": 2026},
            "teams": {"home": {"id": hid, "name": f"T{hid}"}, "away": {"id": aid, "name": f"T{aid}"}},
            "goals": {"home": gh, "away": ga},
            "score": {"fulltime": ft if ft is not None else {"home": gh, "away": ga}}}


def test_parseo_api_usa_90_minutos_y_filtra_futuro():
    aet = parse_fixture(api_row(1, 1000, 1, 2, 3, 2, "AET", {"home": 1, "away": 1}))
    assert (aet["gh"], aet["ga"]) == (1, 1)
    rows = [parse_fixture(x) for x in [
        api_row(1, 1000, 1, 2, 2, 0), api_row(2, 2000, 3, 1, 1, 1),
        api_row(3, 9000, 1, 4, 5, 5),                    # posterior al kickoff
        api_row(4, 1500, 1, 5, None, None, "NS", {})]]   # sin jugar
    g = to_games(rows, 1, kickoff_ts=5000)
    assert [(x.gf, x.ga, x.home) for x in g] == [(1, 1, False), (2, 0, True)]


class FakeResp:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def test_cliente_cache_y_errores():
    calls = []

    def http(url, headers, params, timeout):
        calls.append(params)
        if params.get("season") == 1999:
            return FakeResp({"errors": {"plan": "Free plans do not have access to this season."}, "response": []})
        return FakeResp({"errors": [], "response": [api_row(1, 1000, 1, 2, 1, 0)]})

    api = ApiFootball("k", Cache(tempfile.mkdtemp()), http)
    api.get("/fixtures", {"team": 1, "season": 2026}, ttl=3600)
    api.get("/fixtures", {"team": 1, "season": 2026}, ttl=3600)
    assert len(calls) == 1  # la segunda salió de la caché
    try:
        api.get("/fixtures", {"team": 1, "season": 1999}, ttl=3600)
        assert False
    except Exception as e:
        assert "Free plans" in str(e)


def test_flujo_completo_partido_y_liquidacion():
    kick = int((datetime.now() + timedelta(hours=5)).timestamp())
    now = datetime.now().timestamp()
    hist = lambda tid, base: [api_row(100 + tid * 100 + i, int(now - (i + 1) * 7 * 86400), tid, 90 + i, base[i % 2], 1)
                              for i in range(14)]

    def http(url, headers, params, timeout):
        if "date" in params:
            return FakeResp({"errors": [], "response": [api_row(777, kick, 1, 2, None, None, "NS", {})]})
        if params.get("id") == 777:
            return FakeResp({"errors": [], "response": [api_row(777, kick - 99999, 1, 2, 2, 1)]})
        return FakeResp({"errors": [], "response": hist(params["team"], (2, 1))})

    folder = tempfile.mkdtemp()
    api, store = ApiFootball("k", Cache(os.path.join(folder, "c")), http), Store(folder)
    text, buttons = bot.answer("/partido T1 vs T2 | +0.5 1.10 | -5.5 1.12", api, store)
    assert buttons and buttons[0][0] == "📊 Ver detalle"
    assert "+0.5 goles:" in text and "Máximo: Under" in text and "Tu apuesta +0.5 @ 1.10" in text
    assert text == bot.answer("/partido T1 vs T2 | +0.5 1.10 | -5.5 1.12", api, store)[0]  # determinista
    recs = list(store.load().values())
    assert len(recs) == 1 and len(recs[0]["signals"]) == 2
    assert "Aún no hay partidos liquidados" in bot.answer("/stats", api, store)[0]
    data = store.load()
    data["777"]["kickoff_ts"] -= 10 * 3600
    store.save(data)
    assert "Resultados guardados" in bot.answer("/liquidar", api, store)[0]
    assert "1 partidos liquidados" in bot.answer("/stats", api, store)[0]


def test_estadisticas():
    recs = [{"p_over05": .9, "p_ceiling": .97, "ceiling_line": 5.5, "total_goals": 2,
             "signals": [{"side": "over", "line": .5, "odds": 1.1, "value": True}]},
            {"p_over05": .9, "p_ceiling": .97, "ceiling_line": 5.5, "total_goals": 0,
             "signals": [{"side": "over", "line": .5, "odds": 1.1, "value": True}]}]
    s = compute_stats(recs, 500)
    assert s["signals"]["n"] == 2 and abs(s["signals"]["profit"] - (50 - 500)) < 1e-9
    assert s["over05"]["hit"] == 0.5



# ---------- en vivo, resultado y opciones ----------
def test_live_forecast():
    g = games(2, 1)
    f60 = live_forecast(g, g, cur_goals=2, elapsed=60)
    assert abs(sum(f60.p_total) - 1) < 1e-9
    assert f60.p_over[0.5] == 1.0 and f60.p_over[1.5] == 1.0 and f60.p_under[1.5] == 0.0
    assert 0 < f60.p_over[2.5] < 1 and f60.live and f60.cur_goals == 2
    f89 = live_forecast(g, g, cur_goals=2, elapsed=89)
    assert f89.p_over[2.5] < 0.15 < f60.p_over[2.5]       # al final, casi no caben más goles
    assert f89.ceiling >= 2 and f89.p_under[2.5] > 0.85
    f0 = live_forecast(g, g, cur_goals=0, elapsed=0)       # minuto 0 = PRE
    pre = forecast(g, g)
    assert abs(f0.p_over[1.5] - pre.p_over[1.5]) < 0.02
    assert remaining_fraction(0) > remaining_fraction(45) > remaining_fraction(90) >= 0.03


def test_live_el_marcador_sube_las_probabilidades():
    g = games(1, 1)
    base = live_forecast(g, g, 0, 70).p_over[0.5]
    assert live_forecast(g, g, 0, 20).p_over[0.5] > base     # menos tiempo, menos chance
    assert live_forecast(g, g, 1, 70).p_over[1.5] == base    # un gol ya marcado: +1.5 = lo que era +0.5


EVENTS: dict = {}


def make_world():
    now = datetime.now().timestamp()
    live_ts, ns_ts = int(now - 3800), int(now + 120)   # por jugar: en 2 minutos (siempre es 'hoy')
    day = [api_row(1, live_ts, 1, 2, 1, 0, "2H", {}, elapsed=63) | {"teams": {"home": {"id": 1, "name": "Nicaragua"}, "away": {"id": 2, "name": "Haiti"}}},
           api_row(2, ns_ts, 3, 4, None, None, "NS", {}) | {"teams": {"home": {"id": 3, "name": "Nicaragua U20"}, "away": {"id": 4, "name": "Cuba U20"}}},
           api_row(3, ns_ts, 5, 6, None, None, "NS", {}) | {"teams": {"home": {"id": 5, "name": "Peru"}, "away": {"id": 6, "name": "Chile"}}},
           api_row(555, int(now - 9000), 7, 8, 2, 1, "FT")]
    hist = lambda tid: [api_row(100 + tid * 100 + i, int(now - (i + 1) * 7 * 86400), tid, 90 + i, (2, 1)[i % 2], 1) for i in range(14)]

    def http(url, headers, params, timeout):
        if "date" in params:
            return FakeResp({"errors": [], "response": day})
        if "id" in params:
            return FakeResp({"errors": [], "response": [r for r in day if r["fixture"]["id"] == params["id"]]})
        if "fixture" in params:
            return FakeResp({"errors": [], "response": EVENTS.get(params["fixture"], [])})
        return FakeResp({"errors": [], "response": hist(params["team"])})

    folder = tempfile.mkdtemp()
    return ApiFootball("k", Cache(os.path.join(folder, "c")), http), Store(folder)


def test_un_nombre_con_varios_partidos_da_botones_y_el_boton_analiza_en_vivo():
    api, store = make_world()
    text, buttons = bot.answer("Nicaragua | +1.5 1.80", api, store)
    assert "2 partidos" in text and len(buttons) == 2
    assert buttons[0][0].startswith("🔴 63' 1-0") and buttons[1][0].startswith("⏳")   # en vivo primero
    out, det_btn = bot.answer_callback(buttons[0][1], api, store)                     # las cuotas sobreviven al botón
    assert "🔴 63'" in out and "Nicaragua 1-0 Haiti" in out and "Ya cumplido: +0.5" in out
    assert "Tu apuesta +1.5 @ 1.80" in out and "En vivo uso el pronóstico previo + marcador y minuto." in out
    assert det_btn and det_btn[0][1].startswith("d:1:")
    detail, none_btn = bot.answer_callback(det_btn[0][1], api, store)                # «Ver detalle»
    assert none_btn is None and "cuota justa" in detail and "Escalera Under" in detail
    assert store.load() == {}                                                         # en vivo no se registra
    pre = bot.answer_callback(buttons[1][1], api, store)[0]
    assert "Nicaragua U20 vs Cuba U20" in pre and "Tu apuesta +1.5 @ 1.80" in pre and "+0.5 goles:" in pre
    assert "1. " in text and "2. " in text and "escribe el número" in text
    typed = bot.answer("2", api, store)[0]                                            # elegir escribiendo el número
    assert "Nicaragua U20 vs Cuba U20" in typed and "Tu apuesta +1.5 @ 1.80" in typed
    assert "entre 1 y 2" in bot.answer("9", api, store)[0]


def test_un_solo_partido_se_analiza_directo_y_sin_coincidencia_se_avisa():
    api, store = make_world()
    text, buttons = bot.answer("peru", api, store)
    assert buttons and "Peru vs Chile" in text and "+0.5 goles:" in text            # trae el botón de detalle
    assert len(store.load()) == 1                                                    # PRE sí se registra
    assert "No encontré" in bot.answer("Zzzzz", api, store)[0]
    assert "al menos 3 letras" in bot.answer("ab", api, store)[0]


def test_partido_terminado_muestra_resultado_y_apuestas():
    api, store = make_world()
    text = bot.answer("/partido 555 | +0.5 1.10 | -2.5 1.50", api, store)[0]
    assert "TERMINÓ" in text and "2-1" in text and "Total a los 90 minutos: 3" in text
    assert "+0.5 @ 1.10: ✅ GANADA +S/50" in text and "-2.5 @ 1.50: ❌ PERDIDA -S/500" in text


def test_hoy_y_vivo():
    api, store = make_world()
    vivo = bot.answer("/vivo", api, store)[0]
    assert "Nicaragua vs Haiti" in vivo and "Peru" not in vivo
    hoy = bot.answer("/hoy", api, store)[0]
    assert "🔴 63' 1-0" in hoy and "⏳" in hoy


def test_en_vivo_apuesta_ya_cumplida_no_es_valor():
    api, store = make_world()
    out = bot.answer("/partido 1 | +0.5 1.01", api, store)[0]
    assert "Tu apuesta +0.5 @ 1.01: ya cumplida" in out



# ---------- veredicto simple, segunda opinión (IA) y resumen del backtest ----------
def test_veredicto_umbrales():
    assert bot.verdict(0.96) == "✅ APOSTAR" and bot.verdict(0.93) == "🟡 DUDOSO" and bot.verdict(0.80) == "❌ NO APOSTAR"
    assert bot.verdict(0.99, small=True) == "🟡 DUDOSO"          # muestra corta: nunca ✅
    assert bot.p_txt(0.9999) == "99%"                             # nunca redondea hacia arriba


def make_ai_http(over, under, status=200, payload=None):
    calls = []

    def http(url, headers, json, timeout):
        calls.append(json)
        body = payload if payload is not None else {"output": [{"type": "message", "content": [{"type": "output_text",
              "text": "Aquí va: " + __import__("json").dumps({"over": over, "under": under, "resumen": "Dos ataques en forma.",
                                                                "riesgos": ["rotaciones"], "faltan_datos": ["alineación"]})}]}]}
        return FakeResp(body) if status == 200 else type("R", (), {"status_code": status, "json": lambda self: {}})()
    http.calls = calls
    return http


def test_parseo_de_la_respuesta_de_openai():
    from ai import parse_opinion, cross_check
    op = parse_opinion('texto {"over": {"0.5": 0.95, "1.5": 80}, "under": {"5.5": 0.97}, "resumen": "ok"} fin')
    assert op["over"] == {0.5: 0.95, 1.5: 0.80} and op["under"] == {5.5: 0.97}      # 80 -> 0.80
    assert parse_opinion('{"over": {"0.5": 0.5, "1.5": 0.9}}')["over"][1.5] == 0.5   # coherente: no sube con la línea
    assert parse_opinion("no es json") is None and parse_opinion('{"over": {"0.5": 700}}') is None
    assert cross_check(0.96, 0.90) == (0.90, False) and cross_check(0.96, 0.80) == (0.80, True)
    assert cross_check(0.96, None) == (0.96, False)


def test_cliente_openai_cache_reintento_sin_web_y_errores():
    from ai import OpenAIClient
    f = {"id": 9, "home": "A", "away": "B", "league": "L", "country": "C"}
    http = make_ai_http({"0.5": 0.96, "1.5": 0.7}, {"5.5": 0.98})
    cl = OpenAIClient("k", "modelo-x", Cache(tempfile.mkdtemp()), http=http)
    a, b = cl.opinion(f, [0.5, 1.5], [5.5]), cl.opinion(f, [0.5, 1.5], [5.5])
    assert a == b and len(http.calls) == 1                       # misma consulta: sale de la caché
    assert http.calls[0]["tools"] == [{"type": "web_search"}] and http.calls[0]["model"] == "modelo-x"
    assert "cuotas" in http.calls[0]["input"] and "A vs B" in http.calls[0]["input"]
    # si la herramienta web da error 400, reintenta sin web y funciona
    inner = make_ai_http({"0.5": 0.9}, {"5.5": 0.98})
    seen = []

    def picky(url, headers, json, timeout):
        seen.append("tools" in json)
        return inner(url, headers, json, timeout) if "tools" not in json else type("R", (), {"status_code": 400, "json": lambda self: {}})()
    nw = OpenAIClient("k", "m", Cache(tempfile.mkdtemp()), http=picky)
    assert nw.opinion(f, [0.5], [5.5])["over"] == {0.5: 0.9} and seen == [True, False]
    bad = OpenAIClient("k", "m", Cache(tempfile.mkdtemp()), http=make_ai_http({}, {}, status=401))
    assert bad.opinion(f, [0.5], [5.5]) is None and "401" in bad.last_error
    junk = OpenAIClient("k", "m", Cache(tempfile.mkdtemp()), http=make_ai_http({}, {}, payload={"output": []}))
    assert junk.opinion(f, [0.5], [5.5]) is None and junk.last_error == "respuesta no válida"


class FakeAI:
    def __init__(self, over=None, under=None, err="timeout"):
        self.op = None if over is None else {"over": over, "under": under, "resumen": "Resumen IA.", "riesgos": ["r1"], "faltan": []}
        self.last_error = err

    def opinion(self, fx, over_lines, under_lines, live=None, data=None):
        return self.op


def high_scoring_world():
    api, store = make_world()
    return api, store


def test_cotejo_bot_e_ia():
    api, store = make_world()
    try:
        bot.AI = FakeAI({0.5: 0.99, 1.5: 0.40}, {5.5: 0.99})
        out = bot.answer("peru", api, store)[0]
        assert "(bot " in out and "IA 99%)" in out and "IA 40%)" in out           # muestra ambas lecturas
        assert "bot e IA coinciden" in out
        bot.AI = FakeAI()                                                         # IA activa pero no responde
        out2 = bot.answer("peru", api, store)[0]
        assert "IA sin respuesta" in out2 and "La IA no respondió (timeout)" in out2 and "✅ APOSTAR" not in out2
        bot.AI = FakeAI({0.5: 0.50, 1.5: 0.30}, {5.5: 0.50})                      # IA muy por debajo: manda la prudente
        assert "⚠️ difieren" in bot.answer("peru", api, store)[0]
    finally:
        bot.AI = None


def test_cotejo_el_veredicto_toma_la_lectura_mas_prudente():
    try:
        bot.AI = FakeAI({0.5: 0.97}, {})
        assert bot.verdict_x(0.97, 0.97, False) == "✅ APOSTAR"
        assert bot.verdict_x(0.97, 0.91, False) == "🟡 DUDOSO"          # IA más baja
        assert bot.verdict_x(0.97, 0.80, False) == "❌ NO APOSTAR"
        assert bot.verdict_x(0.97, None, False) == "🟡 DUDOSO"          # IA activa sin respuesta
        assert bot.verdict_x(0.80, 0.99, False) == "❌ NO APOSTAR"      # la IA sola no sube un NO
        bot.AI = None
        assert bot.verdict_x(0.97, None, False) == "✅ APOSTAR"         # sin IA configurada: solo modelo
    finally:
        bot.AI = None


def test_el_registro_guarda_la_ia_y_stats_compara():
    api, store = make_world()
    try:
        bot.AI = FakeAI({0.5: 0.9, 1.5: 0.6}, {5.5: 0.98})
        bot.answer("peru", api, store)
    finally:
        bot.AI = None
    rec = list(store.load().values())[0]
    assert rec["p_ai05"] == 0.9 and rec["p_ai15"] == 0.6
    data = store.load()
    k = next(iter(data))
    data[k]["total_goals"] = 2
    s = compute_stats(list(data.values()), 500)
    assert s["ai"]["n"] == 1 and set(s["ai"]) == {"n", "brier_model", "brier_ai", "brier_combo"}


def test_resumen_del_backtest():
    rows = [{"p05": 0.96, "p15": 0.5, "p_ceil": 0.98, "ceil": 5.5, "total": 3} for _ in range(40)]
    rows += [{"p05": 0.96, "p15": 0.5, "p_ceil": 0.98, "ceil": 5.5, "total": 0}]
    rows += [{"p05": 0.80, "p15": 0.5, "p_ceil": 0.98, "ceil": 5.5, "total": 1} for _ in range(10)]
    txt = backtest.summary(rows)
    assert "51 partidos pasados" in txt and "✅ APOSTAR (≥95%): 41 partidos → ocurrió 97.6% · cuota de equilibrio 1.03  ✅ se sostiene" in txt
    assert "❌ NO APOSTAR (<90%): 10 partidos" in txt and "no ganancia" in txt and "Cuota de equilibrio" in txt
    assert "Cuota de equilibrio" not in backtest.summary(rows, footer=False)
    bad = backtest.summary([{"p05": 0.97, "p15": 0.5, "p_ceil": 0.98, "ceil": 5.5, "total": 0 if i % 2 else 2} for i in range(60)])
    assert "NO se sostiene" in bad



# ---------- liquidación automática ----------
def _ft_world(fid, home_goals, away_goals):
    row = api_row(fid, int(datetime.now().timestamp()) - 20000, 5, 6, home_goals, away_goals, "FT")
    row["teams"] = {"home": {"id": 5, "name": "Peru"}, "away": {"id": 6, "name": "Chile"}}

    def http(url, headers, params, timeout):
        return FakeResp({"errors": [], "response": [row] if params.get("id") == fid else []})
    return ApiFootball("k", Cache(tempfile.mkdtemp()), http)


def test_liquidacion_automatica_guarda_resultado_y_formatea_aviso():
    api, store = make_world()
    bot.answer("peru", api, store)                                       # registra el análisis PRE (id 3)
    data = store.load()
    data["3"]["kickoff_ts"] -= 10 * 3600
    data["3"]["p_ai05"] = 0.97
    store.save(data)
    assert bot.settle_pending(api, store) == []                          # el partido sigue 'por jugar'
    assert "aún no terminaron" in bot.answer("/liquidar", api, store)[0]
    done = bot.settle_pending(_ft_world(3, 3, 0), store)
    assert len(done) == 1 and store.load()["3"]["total_goals"] == 3
    msg = bot.format_settled(done)
    assert "Peru 3-0 Chile" in msg and "+0.5 ✅" in msg and "IA 97%" in msg and "/stats" in msg
    assert bot.settle_pending(_ft_world(3, 3, 0), store) == []           # ya liquidado: no se repite


def test_liquidacion_anula_aplazados_y_muy_viejos():
    store = Store(tempfile.mkdtemp())
    now = datetime.now().timestamp()
    base = {"p_over05": .9, "p_over15": .6, "ceiling_line": 5.5, "p_ceiling": .97, "signals": [], "total_goals": None}
    store.save({"10": {**base, "id": 10, "kickoff_ts": now - 20 * 3600}, "11": {**base, "id": 11, "kickoff_ts": now - 6 * 86400}})

    def http(url, headers, params, timeout):
        i = params["id"]
        return FakeResp({"errors": [], "response": [api_row(i, int(now), 1, 2, None, None, "PST" if i == 10 else "NS", {})]})
    api = ApiFootball("k", Cache(tempfile.mkdtemp()), http)
    assert bot.settle_pending(api, store) == []
    data = store.load()
    assert data["10"]["void"] and data["11"]["void"] and store.pending(now) == []
    assert compute_stats(list(data.values())) == {"n": 0, "pending": 0}  # los anulados no cuentan


def test_error_de_api_no_rompe_ni_pierde_pendientes():
    store = Store(tempfile.mkdtemp())
    now = datetime.now().timestamp()
    store.save({"7": {"id": 7, "kickoff_ts": now - 20 * 3600, "total_goals": None}})
    limited = ApiFootball("k", Cache(tempfile.mkdtemp()),
                          lambda url, headers, params, timeout: type("R", (), {"status_code": 429, "json": lambda self: {}})(),
                          sleep=lambda s: None)
    assert bot.settle_pending(limited, store) == []
    assert len(store.pending(now)) == 1 and not store.load()["7"].get("void")   # sigue pendiente para la próxima vuelta


def test_registro_seguro_entre_hilos():
    import threading
    store = Store(tempfile.mkdtemp())
    store.save({str(i): {"id": i, "kickoff_ts": 0, "total_goals": None} for i in range(60)})
    ts = [threading.Thread(target=lambda r=r: [store.settle(i, 2) for i in r]) for r in (range(0, 60, 6), range(1, 60, 6), range(2, 60, 6),
          range(3, 60, 6), range(4, 60, 6), range(5, 60, 6))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert all(r["total_goals"] == 2 for r in store.load().values())            # ninguna actualización se perdió


def test_ciclo_automatico_avisa_por_telegram():
    api, store = make_world()
    bot.answer("peru", api, store)
    data = store.load()
    data["3"]["kickoff_ts"] -= 10 * 3600
    store.save(data)
    sent, sleeps = [], []
    real_sleep, real_send, real_allowed, real_notify = bot.time.sleep, bot.tg_send, bot.ALLOWED, bot.AUTO_NOTIFY

    class Stop(Exception):
        pass

    def fake_sleep(sec):
        sleeps.append(sec)
        if len(sleeps) >= 2:
            raise Stop()
    try:
        bot.AUTO_NOTIFY = True      # el aviso es opcional (por defecto apagado)
        bot.time.sleep, bot.tg_send, bot.ALLOWED = fake_sleep, (lambda chat, text, buttons=None: sent.append((chat, text))), {"123"}
        try:
            bot.auto_settle_loop(_ft_world(3, 2, 1), store)
        except Stop:
            pass
    finally:
        bot.time.sleep, bot.tg_send, bot.ALLOWED, bot.AUTO_NOTIFY = real_sleep, real_send, real_allowed, real_notify
    assert sleeps[0] == 120 and sleeps[1] == 60 * 60
    assert len(sent) == 1 and sent[0][0] == "123" and "Peru 2-1 Chile" in sent[0][1]
    assert store.load()["3"]["total_goals"] == 3



# ---------- cuota mínima y ligas extra ----------
def test_cuota_minima_junto_al_apostar():
    assert bot.min_odds(0.96) == 1.09          # (1.02) / (0.96 - 0.02) = 1.085 -> sube a 1.09
    assert bot.min_odds(0.99) == 1.06 and bot.min_odds(0.955) == 1.10
    line = bot.market_line("+0.5 goles", 0.96, None, "over", 0.5, False)
    assert "✅ APOSTAR" in line and "solo si tu casa paga ≥1.09" in line
    assert "solo si" not in bot.market_line("+1.5 goles", 0.80, None, "over", 1.5, False)   # ❌ no lleva cuota
    assert "solo si" not in bot.market_line("+0.5 goles", 0.99, None, "over", 0.5, True)    # 🟡 (pocos datos) tampoco
    try:
        bot.AI = FakeAI({0.5: 0.955}, {})                                                    # manda la lectura más prudente
        assert "≥1.10" in bot.market_line("+0.5 goles", 0.99, {"over": {0.5: 0.955}, "under": {}}, "over", 0.5, False)
    finally:
        bot.AI = None


def test_el_mensaje_explica_la_cuota_minima_solo_si_hay_apostar():
    import types
    fx_ = {"home": "A", "away": "B", "league": "L", "ts": datetime.now().timestamp()}
    hi, lo = games(3, 0, n=14), games(0, 3, n=14)
    fc = forecast(hi, games(0, 0, n=14))
    txt = bot.simple_pre(fx_, fc, 14, 14, [])
    assert "✅ APOSTAR" in txt and "cuota mínima en tu casa" in txt and "paga ≥" in txt   # el techo sale ✅
    few = bot.simple_pre(fx_, fc, 10, 10, [])                    # pocos datos: nada llega a ✅ ni se pide cuota mínima
    assert "✅ APOSTAR" not in few and "cuota mínima en tu casa" not in few and "Pocos datos" in few


NEW_CSV = (
    "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PSCH,PSCD,PSCA\n"
    "Brazil,Serie A,2020,01/12/2020,20:00,Flamengo,Santos,2,1,H,1.5,3,6\n"        # anterior a 'since'
    "Brazil,Serie A,2022,10/04/2022,20:00,Flamengo,Santos,2,1,H,1.5,3,6\n"
    "Brazil,Serie A,2022,17/04/2022,20:00,Santos,Flamengo,,,,,,\n"                 # sin jugar
    "Brazil,Serie A,2022,24/04/2022,20:00,Santos,Palmeiras,0,0,D,3,3,2\n"
)


def test_carga_de_ligas_extra_y_descarga_con_refresco():
    import time as _t
    import backtest as bt
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "new_BRA.csv")
    open(path, "w", encoding="utf-8").write(NEW_CSV)
    rows = bt.load_new_csv("BRA", folder)                      # archivo reciente: no descarga
    assert [(r["home"], r["away"], r["hg"], r["ag"]) for r in rows] == [("Flamengo", "Santos", 2, 1), ("Santos", "Palmeiras", 0, 0)]
    assert rows[0]["o25"] is None and rows[0]["date"] == datetime(2022, 4, 10)

    class Resp:
        def __init__(self, ok, content=b""):
            self.ok, self.content = ok, content

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("HTTP 500")
    old = _t.time() - 3 * 86400
    os.utime(path, (old, old))                                  # archivo viejo
    bt.download_if_stale("u", path, getter=lambda url, timeout: Resp(False))
    assert open(path, encoding="utf-8").read() == NEW_CSV      # falla la descarga: se queda con la copia
    bt.download_if_stale("u", path, getter=lambda url, timeout: Resp(True, b"nuevo"))
    assert open(path, "rb").read() == b"nuevo"                  # descarga ok: reemplaza
    try:
        bt.download_if_stale("u", os.path.join(folder, "no_existe.csv"), getter=lambda url, timeout: Resp(False))
        assert False
    except RuntimeError:
        pass                                                    # sin copia y sin descarga: error claro


def test_prueba_por_grupos_con_ligas_que_fallan():
    import backtest as bt
    d = tempfile.mkdtemp()
    synthetic_csv(os.path.join(d, "2223_X.csv"), seed=2, seasons=2, teams=14)
    base = bt.load_csv("2223", "X", d)
    real_a, real_b = bt.load_csv, bt.load_new_csv

    def fake_csv(season, league, folder):
        if season == "2526":
            raise OSError("no existe")
        return base

    def fake_new(code, folder, since=None):
        if code in ("ARG", "BRA"):
            return base
        raise OSError("sin conexión")
    try:
        bt.load_csv, bt.load_new_csv = fake_csv, fake_new
        groups, failed = bt.run_groups(d)
    finally:
        bt.load_csv, bt.load_new_csv = real_a, real_b
    assert len(groups) == 2 and groups[0][0].startswith("ligas grandes de Europa") and groups[1][0].startswith("otras ligas")
    assert len(groups[0][1]) > 100 and len(groups[1][1]) > 100
    assert failed == 5 + (len(bt.OTHER_LEAGUES) - 2)            # 5 ligas × 2526 + las extra que no cargaron



# ---------- una sola regla para mensaje, registro y /stats ----------
def test_regla_unica_de_apuesta():
    try:
        bot.AI = None
        assert bot.decide(0.96, "over", 0.5, 1.10, None, False)["value"] is True
        assert bot.decide(0.96, "over", 0.5, 1.05, None, False)["value"] is False   # cuota por debajo de la mínima (1.09)
        assert bot.decide(0.96, "over", 0.5, 1.10, None, True)["value"] is False    # pocos datos
        assert bot.decide(0.90, "over", 0.5, 1.50, None, False)["value"] is False   # probabilidad baja, aunque la cuota sea buena
        bot.AI = FakeAI({0.5: 0.90}, {})
        assert bot.decide(0.96, "over", 0.5, 1.30, {"over": {0.5: 0.90}, "under": {}}, False)["value"] is False   # la IA ve menos
        ok = bot.decide(0.96, "over", 0.5, 1.10, {"over": {0.5: 0.97}, "under": {}}, False)
        assert ok["value"] is True and ok["p_ai"] == 0.97
        assert bot.decide(0.96, "over", 1.5, 1.10, {"over": {0.5: 0.97}, "under": {}}, False)["value"] is False  # la IA no dio esa línea
        assert bot.decide(1.0, "over", 0.5, 1.01, None, False)["value"] is False   # ya cumplida: no hay nada que apostar
    finally:
        bot.AI = None


def test_el_registro_guarda_la_decision_y_stats_mide_lo_recomendado():
    api, store = make_world()
    out = bot.answer("peru | +0.5 1.50 | +1.5 3.00", api, store)[0]
    assert "no la cumple" in out and "conviene" not in out.replace("cumple", "")    # mensaje con la regla única
    sig = list(store.load().values())[0]["signals"]
    assert [x["value"] for x in sig] == [False, False] and all("p_ai" in x for x in sig)
    rec = {"p_over05": .96, "p_ceiling": .98, "ceiling_line": 5.5, "total_goals": 2,
           "signals": [{"side": "over", "line": .5, "odds": 1.10, "value": True},
                       {"side": "over", "line": 1.5, "odds": 2.0, "value": False}]}
    store.save({"1": rec, "2": {**rec, "total_goals": 0, "id": 2}})
    txt = bot.stats(store)
    assert "habría recomendado (✅ y con cuota suficiente): 2 · acertadas 1" in txt and "ROI" in txt   # el ❌ no cuenta


# ---------- backtest sobre datos sintéticos con verdad conocida ----------
def pois(rng, lam):
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def synthetic_csv(path, seed, seasons=3, teams=16):
    rng = random.Random(seed)
    att = {t: rng.uniform(0.7, 1.4) for t in range(teams)}
    dfn = {t: rng.uniform(0.7, 1.4) for t in range(teams)}
    rows, day = [], datetime(2022, 8, 1)
    for _ in range(seasons):
        for h in range(teams):
            for a in range(teams):
                if h == a:
                    continue
                lh, la = 1.3 * att[h] * dfn[a] * 1.12, 1.3 * att[a] * dfn[h] / 1.12
                rows.append((day + timedelta(days=rng.randint(0, 270)), f"T{h}", f"T{a}", pois(rng, lh), pois(rng, la)))
        day += timedelta(days=365)
    rows.sort()
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "AvgC>2.5", "AvgC<2.5"])
        for d, h, a, hg, ag in rows:
            w.writerow([d.strftime("%d/%m/%Y"), h, a, hg, ag, 1.9, 1.9])


def test_backtest_sintetico_calibra_y_gana_a_la_constante():
    folder = tempfile.mkdtemp()
    synthetic_csv(os.path.join(folder, "2223_X.csv"), seed=1)
    matches = backtest.load_csv("2223", "X", folder)
    rows = backtest.run_backtest(matches)
    assert len(rows) > 500
    ys = [1 if r["total"] > 2.5 else 0 for r in rows]
    p25 = [r["p25"] for r in rows]
    base = sum(ys) / len(ys)
    assert backtest.brier(p25, ys) < backtest.brier([base] * len(ys), ys)  # mejor que una constante
    assert abs(sum(p25) / len(p25) - base) < 0.04                             # bien calibrado en media
    p05 = [r["p05"] for r in rows]
    y05 = [1 if r["total"] >= 1 else 0 for r in rows]
    assert abs(sum(p05) / len(p05) - sum(y05) / len(y05)) < 0.03
    assert "Prueba contra el MERCADO REAL" in backtest.report(rows)


def test_ia_colgada_no_bloquea_y_se_recuerda_el_fallo():
    import time as _t
    from ai import OpenAIClient
    calls = []

    def hang(*a, **k):
        calls.append(1)
        _t.sleep(5)

    cl = OpenAIClient("k", "m", Cache(tempfile.mkdtemp()), http=hang, deadline=0.3, fail_memory=60)
    fx = {"id": 9, "home": "A", "away": "B", "league": "L", "country": "C"}
    t0 = _t.time()
    assert cl.opinion(fx, [0.5], [2.5]) is None
    assert _t.time() - t0 < 2 and "tiempo" in cl.last_error
    t1 = _t.time()
    assert cl.opinion(fx, [0.5], [2.5]) is None      # segunda vez: respuesta inmediata, sin reintentar
    assert _t.time() - t1 < 0.1 and len(calls) == 1


def test_en_vivo_recuerda_el_pre_y_la_expulsion_limita_a_dudoso():
    api, store = make_world()
    bot.answer("peru", api, store)                              # el pre queda registrado
    sin_reg = bot.analyze_fixture(api, store, api.fixture_by_id(1), [])[0]
    assert "Antes del inicio el modelo daba" in sin_reg                              # sin registro previo: lo recalcula
    import model
    fc = model.forecast([model.Game(2, 1, True, 10 * i) for i in range(1, 15)],
                        [model.Game(1, 1, False, 10 * i) for i in range(1, 15)])
    store.log(api.fixture_by_id(1), fc, 0, [])                  # como si se hubiera consultado antes de empezar
    out = bot.analyze_fixture(api, store, api.fixture_by_id(1), [])[0]
    assert "📌 Antes del partido el bot dio" in out and "🟥" not in out
    EVENTS[1] = [{"type": "Card", "detail": "Red Card", "time": {"elapsed": 40}, "team": {"name": "Haiti"}},
                 {"type": "Card", "detail": "Yellow Card", "time": {"elapsed": 12}, "team": {"name": "Haiti"}}]
    try:
        api.cache = type(api.cache)(tempfile.mkdtemp())          # caché limpia
        out = bot.analyze_fixture(api, store, api.fixture_by_id(1), [])[0]
        assert "🟥 Expulsión: Haiti (40')" in out and "✅ APOSTAR" not in out
    finally:
        EVENTS.clear()


def test_linea_de_rango_y_mejores():
    import model
    api, store = make_world()
    text, _ = bot.answer("peru", api, store)
    assert "Rango 1–" in text and "cuota justa" in text                       # PRE trae el rango
    fc = model.forecast([model.Game(2, 1, True, 10 * i) for i in range(1, 15)],
                        [model.Game(1, 1, False, 10 * i) for i in range(1, 15)])
    assert abs(model.range_prob(fc) - (fc.p_ceiling - fc.p_total[0])) < 1e-9
    live, _ = bot.answer("Nicaragua | +1.5 1.80", api, store)
    out = bot.analyze_fixture(api, store, api.fixture_by_id(1), [])[0]
    assert "Rango" not in out                                                   # ya hay gol: no aplica
    res = bot.scan_best(api, store)                                             # sin IA configurada
    assert "Revisé" in res and "Peru vs Chile" in res and "Techo Under" in res
    assert "Nicaragua vs Haiti" not in res                                      # en juego: solo con «vivo»
    assert "Nicaragua vs Haiti" in bot.scan_best(api, store, include_live=True)
    assert "Estimación, no garantía" in res
    assert "Nicaragua U20" not in res and "Excluí amistosos de clubes y juveniles" in res   # juveniles fuera del estudio
    assert len(store.load()) >= 1                                               # lo revisado en PRE se registra
    assert "/mejores" in bot.HELP
    from ai import OpenAIClient
    old_ai, old_safe, old_ex = bot.AI, bot.SAFE_PROB, bot.SCAN_EXCLUDE
    try:   # con IA y umbral bajo aparecen las combinadas
        bot.SCAN_EXCLUDE = []
        bot.SAFE_PROB = 0.88
        bot.AI = OpenAIClient("k", "m", Cache(tempfile.mkdtemp()),
                              http=make_ai_http({"0.5": 0.92, "1.5": 0.8}, {str(x + 0.5): 0.99 for x in range(2, 10)}))
        res = bot.scan_best(api, store)
        assert "Combinadas posibles" in res and "Los 2 mejores +0.5 juntos" in res and "MÁS ALTA" in res
    finally:
        bot.AI, bot.SAFE_PROB, bot.SCAN_EXCLUDE = old_ai, old_safe, old_ex


def test_diagnostico_oddspapi_con_respuestas_simuladas():
    import oddspapi
    calls = []

    def http(url, headers, params, timeout):
        calls.append(url)
        assert headers in ({"X-API-Key": "K"}, {})
        if "v5" in url and url.endswith("/bookmakers"):
            return type("R", (), {"status_code": 404, "json": lambda s: {}})()      # v5 no existe: cae a v4
        if url.endswith("/bookmakers"):
            return FakeResp([{"slug": "betano.pe", "name": "Betano PE"}, {"slug": "pinnacle", "name": "Pinnacle"}, {"slug": "x"}])
        if url.endswith("/fixtures"):
            assert params["from"].endswith("Z") and params["sportId"] == 10        # v4 exige from/to en ISO
            return FakeResp([{"fixtureId": "id1", "participant1Name": "A", "participant2Name": "B", "hasOdds": True},
                             {"fixtureId": "id2", "participant1Name": "C", "participant2Name": "D", "hasOdds": False}])
        if url.endswith("/odds") and "v4" in url:
            assert "pinnacle" in params["bookmakers"] and params["fixtureId"] == "id1"
            return FakeResp({"bookmakerOdds": {"pinnacle": {"markets": {
                "1010": {"outcomes": {"1010": {"players": {"0": {"price": 1.9, "active": True}}}}},
                "1016": {"outcomes": {"1016": {"players": {"0": {"price": 1.04, "active": True}}},
                                      "1017": {"players": {"0": {"price": 9.0, "active": False}}}}}}}}})
        if url.endswith("/fixtures/odds"):
            assert "pinnacle" in params["bookmakers"]
            return FakeResp({"odds": {"pinnacle": {"k1": {"marketId": 1010, "outcomeId": 1010, "price": 1.9},
                                                    "k2": {"marketId": 1016, "outcomeId": 1016, "price": 1.04}}}})
        if url.endswith("/markets"):
            return FakeResp([{"marketId": 1016, "marketType": "totals", "period": "fulltime", "handicap": 0.5},
                             {"marketId": 1010, "marketType": "totals", "period": "fulltime", "handicap": 2.5}])
        raise AssertionError(url)

    api = oddspapi.OddsPapi("K", http=http)
    out = oddspapi.discover(api, lambda s: None)
    assert "pinnacle, betano.pe" in out and "Betano: betano.pe" in out and "1016" in out and "pinnacle: 2 mercados de totales" in out, out
    assert api.used >= 5 and "de 250" in out                                    # 404 de v5 + 4 consultas, y se recuerda la base buena
    assert all("v5" not in u for u in calls[1:])
    assert oddspapi.OddsPapi(' "K" ').key == "K"                                # limpia comillas y espacios
    only_query = oddspapi.OddsPapi("K", http=lambda url, headers, params, timeout: FakeResp([{"slug": "pinnacle"}]) if params.get("apiKey") == "K"
                                   else type("R", (), {"status_code": 401, "json": lambda s: {}})())
    assert only_query.get("/bookmakers")[0]["slug"] == "pinnacle"                # cae a apiKey por URL
    bad = oddspapi.OddsPapi("K", http=lambda url, headers, params, timeout: type("R", (), {"status_code": 401, "json": lambda s: {}})())
    try:
        oddspapi.discover(bad, lambda s: None)
        assert False
    except oddspapi.OddsError as e:
        assert "clave" in str(e)


def _odds_http(prices, lines_meta=None, calls=None):
    """OddsPapi simulado: v4, partido 'Nicaragua U20 vs Cuba U20' con totales de Pinnacle."""
    meta = lines_meta or [
        {"marketId": 1000, "marketType": "totals", "period": "fulltime", "handicap": 0.5, "playerProp": False,
         "outcomes": [{"outcomeId": 1000, "outcomeName": "Over"}, {"outcomeId": 1001, "outcomeName": "Under"}]},
        {"marketId": 1002, "marketType": "totals", "period": "fulltime", "handicap": 1.5, "playerProp": False,
         "outcomes": [{"outcomeId": 1002, "outcomeName": "Over"}, {"outcomeId": 1003, "outcomeName": "Under"}]},
        {"marketId": 1016, "marketType": "totals", "period": "fulltime", "handicap": 5.5, "playerProp": False,
         "outcomes": [{"outcomeId": 1016, "outcomeName": "Over"}, {"outcomeId": 1017, "outcomeName": "Under"}]}]

    def http(url, headers, params, timeout):
        if calls is not None:
            calls.append(url.split("/")[-1])
        if url.endswith("/bookmakers"):
            return FakeResp([{"slug": "pinnacle"}, {"slug": "betano"}, {"slug": "betfair-ex"}])
        if url.endswith("/markets"):
            return FakeResp(meta)
        if url.endswith("/fixtures"):
            return FakeResp([{"fixtureId": "idX", "participant1Name": "Nicaragua U20", "participant2Name": "Cuba U20",
                              "startTime": datetime.now(timezone.utc).isoformat(), "hasOdds": True},
                             {"fixtureId": "idY", "participant1Name": "Otro FC", "participant2Name": "Nadie", "hasOdds": True}])
        if url.endswith("/odds"):
            assert params["fixtureId"] == "idX" and "pinnacle" in params["bookmakers"] and "betano" not in params["bookmakers"]
            mk = {}
            for mid, (po, pu) in prices.items():
                mk[str(mid)] = {"outcomes": {str(mid): {"players": {"0": {"price": po, "active": True}}},
                                             str(mid + 1): {"players": {"0": {"price": pu, "active": True}}}}}
            return FakeResp({"bookmakerOdds": {"pinnacle": {"markets": mk}}})
        raise AssertionError(url)
    return http


def test_mercado_tercera_lectura():
    import oddspapi
    assert abs(oddspapi.devig(1.05, 10.0) - (1 / 1.05) / (1 / 1.05 + 0.1)) < 1e-9
    assert oddspapi.devig(1.5, 1.5) is None and oddspapi.devig(None, 2.0) is None        # margen absurdo o dato roto
    calls = []
    http = _odds_http({1000: (1.04, 14.0), 1002: (1.30, 3.60), 1016: (14.0, 1.02)}, calls=calls)
    cache = Cache(tempfile.mkdtemp())
    feed = oddspapi.OddsFeed(oddspapi.OddsPapi("K", http=http, cache=cache), cache, daily_cap=10, log=lambda s: None)
    fx = {"id": 2, "home": "Nicaragua U20", "away": "Cuba U20", "ts": datetime.now().timestamp() + 120}
    mk = feed.probs(fx)
    assert mk and mk["books"] == ["pinnacle"] and 0.5 in mk["over"] and abs(mk["over"][0.5] + mk["under"][0.5] - 1) < 1e-9
    assert mk["over"][0.5] > 0.9 and mk["under"][5.5] > 0.9 and mk["over"][1.5] < mk["over"][0.5]
    n = len(calls)
    assert feed.probs(fx) == mk and len(calls) == n                                     # segunda vez: todo de caché
    assert feed.probs({**fx, "home": "Real Madrid", "away": "Barcelona"}) is None and "no encontrado" in feed.last_error
    capped = oddspapi.OddsFeed(oddspapi.OddsPapi("K", http=http, cache=Cache(tempfile.mkdtemp())),
                               Cache(tempfile.mkdtemp()), daily_cap=0, log=lambda s: None)
    assert capped.probs(fx) is None and "tope diario" in capped.last_error             # protege el cupo mensual
    # en el bot: tres lecturas, la más prudente manda, y se registra
    api, store = make_world()
    old = bot.ODDS_FEED
    try:
        bot.ODDS_FEED = feed
        text = bot.answer("/partido 2", api, store)[0]
        assert "mercado" in text and "Mercado = cuotas de pinnacle" in text
        rec = store.load()["2"]
        assert rec["p_mkt05"] is not None and rec["mkt_books"] == ["pinnacle"]
        # si el mercado discrepa mucho del bot, nunca sube a APOSTAR
        assert bot.blend(0.97, 0.97, 0.80) == (0.80, True) and bot.blend(0.97, None, None) == (0.97, False)
        assert bot.verdict_x(0.97, 0.97, False, 0.80) == "🟡 DUDOSO" or bot.verdict_x(0.97, 0.97, False, 0.80).startswith("❌")
        assert bot.verdict_x(0.97, 0.97, False, 0.96).startswith("✅")
    finally:
        bot.ODDS_FEED = old


def test_selecciones_con_pocos_partidos_por_temporada_buscan_mas_atras():
    now = datetime.now().timestamp()
    row = lambda i, days: api_row(5000 + i, int(now - days * 86400), 9, 90 + i, 1, 1)
    calls = []

    def http(url, headers, params, timeout):
        calls.append(dict(params))
        if "last" in params:
            return FakeResp({"errors": [], "response": [row(100 + i, 10 + 7 * i) for i in range(20)]})
        k = {2026: 0, 2025: 1, 2024: 2}[params["season"]]
        return FakeResp({"errors": [], "response": [row(10 * k + i, 30 + 200 * k + 20 * i) for i in range(3)]})   # 3 por año

    api = ApiFootball("k", Cache(tempfile.mkdtemp()), http)
    g = api.team_games(9, 2026, now)
    assert len(g) == 9 and any("season" in c and c["season"] == 2024 for c in calls)    # 3 años atrás
    assert not any("last" in c for c in calls)                                            # con 9 ya no hace falta 'last'

    def http2(url, headers, params, timeout):
        if "last" in params:
            return FakeResp({"errors": [], "response": [row(100 + i, 10 + 7 * i) for i in range(20)]})
        return FakeResp({"errors": [], "response": [row(i, 30 + 20 * i) for i in range(2)]})   # solo 2 por temporada
    api2 = ApiFootball("k", Cache(tempfile.mkdtemp()), http2)
    assert len(api2.team_games(9, 2026, now)) >= 8                                          # rescata con 'last'


def test_combinada_acumulada_con_botones_y_estudio_diario():
    from ai import OpenAIClient
    api, store = make_world()
    old_ai, old_safe, old_allowed, old_send, old_ex = bot.AI, bot.SAFE_PROB, set(bot.ALLOWED), bot.tg_send, bot.SCAN_EXCLUDE
    sent = []
    try:
        bot.SCAN_EXCLUDE = []                      # el partido 2 es sub-20: aquí no queremos el aviso de juvenil
        bot.AI = OpenAIClient("k", "m", Cache(tempfile.mkdtemp()),
                              http=make_ai_http({"0.5": 0.97, "1.5": 0.8}, {str(x + 0.5): 0.99 for x in range(2, 10)}))
        bot.SAFE_PROB = 0.88
        text, buttons = bot.answer("/partido 2", api, store)
        data = [d for _, d in buttons]
        assert "c:2:u" in data and any(t.startswith("➕") for t, _ in buttons)           # botón para añadir el techo ✅
        out, btn = bot.answer_callback("c:2:u", api, store)
        assert "➕ Añadida" in out and "Mi combinada de hoy (1 selección)" in out and btn == [("🗑 Vaciar mi combinada", "c:0:clear")]
        assert "ya estaba" in bot.answer_callback("c:2:u", api, store)[0]                 # no se duplica
        bot.answer("/partido 3", api, store)
        out2, _ = bot.answer_callback("c:3:u", api, store)
        assert "(2 selecciones)" in out2 and "Probabilidad de ganar todas" in out2 and "cuota justa" in out2, out2
        assert "Mi combinada de hoy (2" in bot.answer("/combinada", api, store)[0]
        assert store.combo_items("1999-01-01") == []                                       # otro día: vacía
        assert "no tengo guardada" in bot.answer_callback("c:999:o", api, store)[0].lower()
        assert "vaciada" in bot.answer("/limpiar", api, store)[0] and "vacía" in bot.answer("/combinada", api, store)[0]
        # estudio diario: se envía solo a los chats permitidos, con título y botones
        bot.ALLOWED = {"42"}
        bot.tg_send = lambda chat, text, buttons=None: sent.append((chat, text, buttons))
        bot.daily_report(api, store)
        assert sent and sent[0][0] == "42" and "Estudio del día" in sent[0][1] and "Revisé" in sent[0][1]
        assert sent[0][2] and all(d.startswith("c:") for _, d in sent[0][2])
        assert "/combinada" in bot.HELP and "/limpiar" in bot.HELP
    finally:
        bot.AI, bot.SAFE_PROB, bot.ALLOWED, bot.tg_send, bot.SCAN_EXCLUDE = old_ai, old_safe, old_allowed, old_send, old_ex


def test_limite_por_minuto_de_api_football_espera_y_reintenta():
    esperas, n = [], [0]

    def http(url, headers, params, timeout):
        n[0] += 1
        if n[0] <= 2:
            return FakeResp({"errors": {"rateLimit": "Too many requests. You have exceeded the limit of requests per minute"}, "response": []})
        return FakeResp({"errors": [], "response": [{"ok": 1}]})

    api = ApiFootball("k", Cache(tempfile.mkdtemp()), http, sleep=esperas.append)
    assert api.get("/fixtures", {"id": 1}, ttl=0) == [{"ok": 1}]
    assert n[0] == 3 and esperas == [12, 30]                      # esperó 12 s y 30 s y luego pasó
    always = ApiFootball("k", Cache(tempfile.mkdtemp()),
                         lambda *a, **k: FakeResp({"errors": {"rateLimit": "x"}, "response": []}), sleep=lambda s: None)
    try:
        always.get("/fixtures", {"id": 2}, ttl=0)
        assert False
    except ApiError as e:
        assert "rateLimit" in str(e)
    pausas = []
    paced = ApiFootball("k", Cache(tempfile.mkdtemp()), lambda *a, **k: FakeResp({"errors": [], "response": [1]}),
                        min_gap=5, sleep=pausas.append)
    paced.get("/a", {}, ttl=0)
    paced.get("/b", {}, ttl=0)
    assert pausas and 0 < pausas[0] <= 5                          # pausa mínima entre consultas reales


def test_estudio_por_liga_gasta_una_consulta_por_liga():
    now = datetime.now().timestamp()
    calls = []

    def lg_row(i, ts, h, a, gh, ga, st="FT", lid=77):
        r = api_row(i, int(ts), h, a, gh, ga, st)
        r["league"] = {"id": lid, "name": "Liga Pro", "country": "X", "season": 2026}
        r["teams"] = {"home": {"id": h, "name": f"Eq{h}"}, "away": {"id": a, "name": f"Eq{a}"}}
        return r

    # liga de 6 equipos con historial; hoy juegan 3 partidos de esa liga + 1 amistoso de clubes + 1 juvenil
    past = [lg_row(100 + k, now - (k + 1) * 3 * 86400, 1 + k % 6, 1 + (k + 1) % 6, 2, 1) for k in range(60)]
    today = [lg_row(900 + j, now + 600 + j * 60, 1 + 2 * j, 2 + 2 * j, None, None, "NS") for j in range(3)]
    fr = lg_row(950, now + 700, 7, 8, None, None, "NS", lid=5); fr["league"]["name"] = "Friendlies Clubs"
    yo = lg_row(951, now + 710, 9, 10, None, None, "NS", lid=6); yo["league"]["name"] = "Copa U19"

    def http(url, headers, params, timeout):
        calls.append(dict(params))
        if "date" in params:
            return FakeResp({"errors": [], "response": today + [fr, yo]})
        if "league" in params:
            return FakeResp({"errors": [], "response": past + today})
        raise AssertionError(f"consulta por equipo inesperada: {params}")        # no debe pedir equipo por equipo

    folder = tempfile.mkdtemp()
    api, store = ApiFootball("k", Cache(os.path.join(folder, "c")), http), Store(folder)
    old_ai = bot.AI
    try:
        bot.AI = None
        text, buttons = bot.scan(api, store)
    finally:
        bot.AI = old_ai
    league_calls = [c for c in calls if "league" in c]
    assert len(league_calls) == 1 and league_calls[0] == {"league": 77, "season": 2026}   # UNA consulta para los 3 partidos
    assert "Revisé 3 partidos de 1 ligas" in text and "Excluí amistosos de clubes y juveniles" in text
    assert "Eq1 vs Eq2" in text and "Friendlies" not in text and "U19" not in text
    assert len(store.load()) == 3                                                         # se registran los mejores para medir
    assert "Mercado apagado" in text                                                      # el estudio dice qué pasó con el mercado
    assert any(w in bot.SCAN_EXCLUDE for w in ("women", "friendlies clubs", "u19")) and "ii" in bot.SCAN_EXCLUDE


def test_la_ia_recibe_datos_reales_y_hay_linea_de_historial():
    from collections import namedtuple
    import ai as aimod
    G = namedtuple("G", "gf ga home age_days")
    hg = [G(2, 1, True, 5), G(0, 0, False, 12), G(1, 0, True, 20)]
    ag = [G(3, 3, True, 4), G(0, 1, False, 9)]
    d = {"home": bot.team_digest("Alfa", hg), "away": bot.team_digest("Beta", ag)}
    assert d["home"]["with_goal"] == 2 and d["home"]["zero"] == 1 and d["home"]["n"] == 3
    fx = {"home": "Alfa", "away": "Beta", "league": "L", "country": "C"}
    prompt = aimod.build_prompt(fx, [0.5], [4.5], None, d)
    assert "DATOS REALES" in prompt and "2-1 (L)" in prompt and "Alfa" in prompt and "Beta" in prompt
    assert "DATOS REALES" not in aimod.build_prompt(fx, [0.5], [4.5])
    line = bot.hist_line(hg, ag)
    assert "4 de 5" in line and "80%" in line and "0-0: 1" in line
    assert bot.hist_line([], []) == ""
    d["news"] = {"injuries": {"Alfa": ["Pérez"]}, "lineups": {"Beta": ["Gómez", "Ruiz"]}}
    p2 = aimod.build_prompt(fx, [0.5], [4.5], None, d)
    assert "Bajas/lesionados de Alfa: Pérez" in p2 and "Alineación confirmada de Beta: Gómez, Ruiz" in p2
    def http(url, headers, params, timeout):
        if url.endswith("/injuries"):
            return FakeResp({"response": [{"team": {"name": "Alfa"}, "player": {"name": "Pérez"}}], "errors": []})
        return FakeResp({"response": [{"team": {"name": "Beta"}, "startXI": [{"player": {"name": "Gómez"}}]}], "errors": []})
    sn = ApiFootball("k", Cache(tempfile.mkdtemp()), http, min_gap=0, sleep=lambda s: None).squad_news(9)
    assert sn["injuries"] == {"Alfa": ["Pérez"]} and sn["lineups"] == {"Beta": ["Gómez"]}
    def http2(url, headers, params, timeout):
        st = lambda a, b, c: [{"type": "Total Shots", "value": a}, {"type": "Shots on Goal", "value": b}, {"type": "Corner Kicks", "value": c}]
        return FakeResp({"response": [{"statistics": st(5, 2, 3)}, {"statistics": st(4, None, "1")}], "errors": []})
    ls = ApiFootball("k", Cache(tempfile.mkdtemp()), http2, min_gap=0, sleep=lambda s: None).live_stats(9)
    assert ls == {"shots": 9, "sot": 2, "corners": 4}


def test_segunda_ia_claude_y_union_prudente():
    import ai as aimod
    seen = {}
    def http(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return FakeResp({"content": [{"type": "text", "text": '{"over": {"0.5": 0.9}, "under": {"4.5": 0.95}, "resumen": "ok"}'}]})
    c = aimod.ClaudeClient("sk", "claude-x", Cache(tempfile.mkdtemp()), use_web=False, http=http)
    op = c.opinion({"id": 1, "home": "A", "away": "B", "league": "L", "country": "C"}, [0.5], [4.5])
    assert op["over"][0.5] == 0.9 and seen["url"].endswith("/v1/messages") and seen["headers"]["x-api-key"] == "sk"
    assert "tools" not in seen["body"]
    a = {"over": {0.5: 0.97}, "under": {4.5: 0.9}, "resumen": "a", "riesgos": ["x"], "faltan": []}
    b = {"over": {0.5: 0.90}, "under": {4.5: 0.95}, "resumen": "b", "riesgos": ["y"], "faltan": []}
    m = aimod.merge_opinions(a, b)
    assert m["over"][0.5] == 0.90 and m["under"][4.5] == 0.9 and m["riesgos"] == ["x", "y"]
    assert aimod.merge_opinions(a, None) is a and aimod.merge_opinions(None, None) is None


def test_get_opinion_con_dos_ias_no_se_cae():
    class C:
        last_error = ""
        def __init__(self, p): self.p = p
        def opinion(self, fx, over, under, live=None, data=None):
            return {"over": {0.5: self.p}, "under": {under[0]: 0.97}, "resumen": "", "riesgos": [], "faltan": []}
    class FC:
        cur_goals = 0
        ceiling_line = 4.5
    old = (bot.AI, bot.AI2, bot.API)
    try:
        bot.AI, bot.AI2, bot.API = C(0.95), C(0.80), None
        op = bot.get_opinion({"id": 1, "home": "A", "away": "B"}, FC(), None)
        assert op["over"][0.5] == 0.80
        bot.AI, bot.AI2 = None, C(0.7)
        assert bot.get_opinion({"id": 1, "home": "A", "away": "B"}, FC(), None)["over"][0.5] == 0.7
    finally:
        bot.AI, bot.AI2, bot.API = old


def test_entrada_en_vivo_pre_mas_live():
    from model import Game
    games = [Game(gf=2, ga=1, home=bool(i % 2), age_days=7 * (i + 1)) for i in range(20)]
    def fx(i, status, el, ch=0, ca=0, home="Alfa", away="Beta"):
        return {"id": i, "ts": 1, "status": status, "elapsed": el, "cur_h": ch, "cur_a": ca, "home_id": i * 2, "away_id": i * 2 + 1,
                "home": home, "away": away, "league": "Liga", "country": "X", "season": 2026}
    class Api:
        def fixtures_by_date(self, d, tz):
            return [fx(1, "1H", 25), fx(2, "1H", 25, 1, 0, "C", "D"), fx(3, "1H", 5, 0, 0, "E", "F"),
                    fx(4, "1H", 25, 0, 0, "Gi W", "Hi W"), fx(5, "NS", None, 0, 0, "I", "J")]
        def team_games(self, tid, season, ts, n=20, **k):
            return games
        def live_stats(self, fid):
            return {"shots": 9, "sot": 4, "corners": 3}
    old = (bot.AI, bot.AI2)
    try:
        bot.AI = bot.AI2 = None
        text = bot.entry_radar(Api(), None)
    finally:
        bot.AI, bot.AI2 = old
    assert "Alfa vs Beta" in text and "minuto 25" in text and ("cuota ≥" in text or "paga ≥" in text)
    assert "C vs D" not in text and "E vs F" not in text and "Gi W" not in text   # con gol, muy temprano, mujeres: fuera
    assert "ritmo" in text and "banca ≤" in text and "👉" in text
    v, p, need = bot.entry_verdict(0.5, None, False)
    assert v.startswith("❌")
    assert bot.entry_verdict(0.93, None, False, "bajo")[0].startswith("🟡")
    assert bot.entry_verdict(0.93, None, False, "alto")[0].startswith("🟢")
    assert bot.pace_info({"shots": 12, "sot": 5, "corners": 4}, 25)[0] == "alto"
    assert bot.pace_info({"shots": 1, "sot": 0, "corners": 0}, 25)[0] == "bajo"
    assert bot.pace_info({"shots": 6, "sot": 2, "corners": 2}, 25)[0] == "normal"
    assert bot.pace_info({}, 25)[0] is None
    assert bot.entry_verdict(0.83, 0.86, False, "alto")[0].startswith("🟡 SOLO")
    assert bot.entry_verdict(0.70, 0.72, False, "alto")[0].startswith("❌")
    assert bot.kelly_pct(0.90, 1.05) == 0.0 and 0 < bot.kelly_pct(0.92, 1.25) <= 0.02


def test_escribir_el_equipo_en_vivo_0_0_da_decision_de_entrada():
    api, store = make_world()
    fx = next(f for f in bot.gather(api) if f["status"] in bot.LIVE_OK)
    fx = {**fx, "status": "1H", "elapsed": 22, "cur_h": 0, "cur_a": 0}
    text, _ = bot.analyze_fixture(api, store, fx, [])
    assert "🎯 ¿Qué línea?" in text and "+0.5:" in text and "+1.5:" in text and "+2.5:" in text and "paga ≥" in text and "banca ≤" in text
    assert "VENTANA ABIERTA" in text and "quedan ~18 min" in text
    fx2 = {**fx, "cur_h": 1}
    t2 = bot.analyze_fixture(api, store, fx2, [])[0]
    assert "🎯 ¿Qué línea?" in t2 and "+1.5:" in t2 and "+0.5:" not in t2


def test_amistoso_o_juvenil_avisa_y_no_da_verde():
    api, store = make_world()
    fx = next(f for f in bot.gather(api) if f["status"] in bot.LIVE_OK)
    fx = {**fx, "status": "1H", "elapsed": 29, "cur_h": 0, "cur_a": 0, "league": "Friendlies Clubs"}
    text, _ = bot.analyze_fixture(api, store, fx, [])
    assert "Amistoso, juvenil o reserva" in text and "🟢" not in text and "✅ APOSTAR" not in text


def test_el_aviso_de_ventana_cambia_con_el_minuto():
    import model
    g = [model.Game(2, 1, bool(i % 2), 7 * (i + 1)) for i in range(20)]
    def txt(el):
        return "\n".join(bot.entry_lines(model.live_forecast(g, g, 0, el), None, False, None))
    assert "AÚN NO" in txt(5) and "faltan ~7 min" in txt(5)
    assert "VENTANA ABIERTA" in txt(30) and "quedan ~10 min" in txt(30)
    assert "VENTANA CERRADA" in txt(55)


def test_meta_y_banca():
    folder = tempfile.mkdtemp()
    old = bot.ODDS_CACHE
    try:
        bot.ODDS_CACHE = Cache(folder)
        api, store = make_world()
        assert "S/500" in bot.answer("/banca 500", api, store)[0]
        m = bot.answer("/meta 25 1.05", api, store)[0]
        assert "apostar S/500" in m and "100% de tu banca" in m and "95.2%" in m and "banca de S/25000" in m
        m2 = bot.answer("/meta 25 1.25 97", api, store)[0]
        assert "apostar S/100" in m2 and "20% de tu banca" in m2
        assert "Uso:" in bot.answer("/meta", api, store)[0]
        assert bot.get_bankroll() == 500.0
        import model
        g = [model.Game(2, 1, bool(i % 2), 7 * (i + 1)) for i in range(20)]
        assert "(S/" in "\n".join(bot.entry_lines(model.live_forecast(g, g, 0, 20), None, False, None))
    finally:
        bot.ODDS_CACHE = old


def test_frase_en_lenguaje_de_calle():
    import model
    g = [model.Game(2, 1, bool(i % 2), 7 * (i + 1)) for i in range(20)]
    pre = model.forecast(g, g)
    s = bot.habla(pre, None)
    assert s.startswith("🗣️") and "gol" in s and "de cada 10 partidos así" in s
    live = model.live_forecast(g, g, 1, 30)
    assert "otro gol" in bot.habla(live, None)
    weak = model.live_forecast([model.Game(0, 0, True, 7 * (i + 1)) for i in range(20)], [model.Game(0, 0, False, 7 * (i + 1)) for i in range(20)], 0, 70)
    assert "Difícil" in bot.habla(weak, None) or "Dudoso" in bot.habla(weak, None)
    api, store = make_world()
    assert "🗣️" in bot.answer("peru", api, store)[0]


def test_detalle_muestra_el_trabajo_del_bot():
    api, store = make_world()
    txt = bot.analyze_fixture(api, store, api.fixture_by_id(1), [], detail=True)[0]
    assert "🔬 Lo que revisé:" in txt and "goles a favor" in txt and "Escalera de «más de»" in txt and "+1.5:" in txt
    pre = bot.analyze_fixture(api, store, api.fixture_by_id(3), [], detail=True)[0]
    assert "🔬 Lo que revisé:" in pre


def test_revision_completa_de_todas_las_lineas():
    api, store = make_world()
    txt = bot.answer("peru", api, store)[0]
    assert "📋 Revisé todas las líneas" in txt and "Más de:" in txt and "Menos de:" in txt
    assert "+0.5 " in txt and "+4.5 " in txt and "-1.5 " in txt and "-7.5 " in txt


def test_contexto_de_cada_equipo_tabla_y_descanso():
    import model
    rows = [{"team_id": i, "rank": i, "points": 70 - 3 * i, "played": 30, "desc": d}
            for i, d in enumerate(["", "Promotion - Champions League", "Promotion - Champions League", "", "", "", "", "", "", "",
                                   "", "", "", "", "", "", "", "Relegation", "Relegation", "Relegation"], 1)]
    assert "zona de descenso" in bot.team_context("X", 19, rows)
    assert "zona alta" in bot.team_context("X", 2, rows)
    assert "mitad de tabla" in bot.team_context("X", 10, rows) or "todavía se juega" in bot.team_context("X", 10, rows)
    assert bot.team_context("X", 999, rows) == ""
    class A:
        def standings_rows(self, lid, season):
            return rows
    g = [model.Game(1, 1, True, 3.0 + 7 * i) for i in range(5)]
    fx = {"id": 77, "league_id": 1, "season": 2026, "home": "H", "away": "A", "home_id": 19, "away_id": 2}
    ctx = bot.context_lines(A(), fx, g, g)
    assert any("descenso" in x for x in ctx) and any("descanso corto" in x for x in ctx)
    bot.CONTEXT_CACHE[77] = ctx
    assert bot.ctx_short(fx)[0].startswith("🧭")


def test_el_contexto_resta_confianza_y_no_se_filtra():
    pen, why = bot.ctx_penalty(["A: último partido hace 2 días (descanso corto: ...)", "B → mitad de tabla: puede jugar con menos urgencia"])
    assert abs(pen - 0.03) < 1e-9 and why == ["descanso corto", "equipo sin urgencia"]
    assert bot.ctx_penalty(["nada"]) == (0.0, [])
    tok = bot.CTX_PEN.set(0.03)
    try:
        assert abs(bot.blend(0.96, None)[0] - 0.93) < 1e-9
    finally:
        bot.CTX_PEN.reset(tok)
    assert bot.blend(0.96, None)[0] == 0.96
    api, store = make_world()
    old = bot.context_lines
    try:
        bot.context_lines = lambda *a, **k: ["Peru: último partido hace 2 días (descanso corto: puede rotar)"]
        txt = bot.answer("peru", api, store)[0]
    finally:
        bot.context_lines = old
    assert "Ajuste de prudencia: −1.5 pts" in txt and "descanso corto" in txt
    assert bot.CTX_PEN.get() == 0.0


def test_el_bot_no_escribe_solo_por_defecto():
    assert bot.DAILY_REPORT_HOUR == -1 and bot.AUTO_NOTIFY is False         # solo responde cuando se le pregunta


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in tests:
        fn()
        print("OK ", name)
    print(f"\n{len(tests)} pruebas pasaron")

