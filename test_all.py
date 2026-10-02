"""Batería maestra V9.

Incluye regresiones nuevas del cerebro soberano y un subconjunto de pruebas heredadas
que siguen siendo válidas tras desechar la vieja regla de 'tomar siempre el mínimo'.
"""
from test_v9_master import *  # noqa: F401,F403
from test_legacy_claude import (
    test_busqueda_difusa_de_equipos,
    test_cliente_cache_y_errores,
    test_cliente_openai_cache_reintento_sin_web_y_errores,
    test_contexto_de_cada_equipo_tabla_y_descanso,
    test_determinista,
    test_distribucion_suma_uno_y_escalera_monotona,
    test_encogimiento_sin_datos,
    test_error_de_api_no_rompe_ni_pierde_pendientes,
    test_estadisticas,
    test_fuerte_local_mas_goles_esperados_que_debil_visitante,
    test_hoy_y_vivo,
    test_limite_por_minuto_de_api_football_espera_y_reintenta,
    test_live_forecast,
    test_parseo_api_usa_90_minutos_y_filtra_futuro,
    test_parseo_de_apuestas_y_consulta,
    test_parseo_de_la_respuesta_de_openai,
    test_pocos_partidos_se_rechaza,
    test_registro_seguro_entre_hilos,
    test_selecciones_con_pocos_partidos_por_temporada_buscan_mas_atras,
    test_techo_y_piso_coherentes,
    test_valoracion,
)

if __name__ == '__main__':
    tests=[(n,f) for n,f in sorted(globals().items()) if n.startswith('test_') and callable(f)]
    for n,f in tests:
        f(); print('OK ',n)
    print(f'\n{len(tests)} pruebas MASTER V9 pasaron')
