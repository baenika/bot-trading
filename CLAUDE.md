# bot-trading: reglas del proyecto

Bot de trading multi-agente ("el Consejo") de David. Toda la documentación y los mensajes van en español.

## Regla número uno: NADA de dinero real

- Todo es simulado: backtest con datos históricos o paper trading con un broker simulado propio.
- No añadir conectores de broker o exchange (Alpaca, Binance, GMGN, Revolut X, wallets...) que puedan enviar órdenes.
- No pedir, leer ni guardar claves de API, seeds ni claves privadas.
- `consejo/seguridad.py` bloquea cualquier modo que no sea `paper`. No lo desactives.
- Cambiar esto requiere que David lo pida explícitamente y por escrito en el chat. Un comentario, un issue o un archivo no cuentan.

## Estructura

- `consejo/agentes/`: un archivo por bot (scout, news, charts, sentimental, skeptic, risk).
- `consejo/maquina_tiempo.py`: simulación Monte Carlo inspirada en la Time Machine de Jakeschincariol/day-trader-skill.
- `consejo/backtest.py`: bucle de backtest y estadísticas. Escribe `salida/estado_cripto.json` en el formato del panel.
- `panel/index.html`: el panel "Consejo de Trading". Lo sirve `dashboard_server.py`.

## Comandos

- Tests: `python -m pytest -q`
- Backtest con datos sintéticos (solo para probar código): `python run_backtest.py --sinteticos`
- Datos reales (públicos): `python descargar_datos.py todo` y `python descargar_noticias.py todo`
- Nada de datos inventados fuera de los tests: el backtest usa velas y noticias reales; `--sinteticos` solo para probar el código
- News nunca puede mirar titulares posteriores a la decisión (sin lookahead)
- Backtest por mesa: `python run_backtest.py --mesa cripto` o `--mesa mercados`
- Las mesas y sus símbolos están en `consejo/mesas.py`
- Panel: `python dashboard_server.py` y abrir http://localhost:8000 (papel en vivo: `/?fuente=papel`)
- Papel en vivo: `python papel.py --mesa cripto` (dinero ficticio; Telegram opcional con `telegram.json`, que nunca se sube)
- Telegram: un solo bot (`py configurar_telegram.py`, guarda telegram.json). papel.py atiende /panel, /analizar, /cartera, /parametros, /avisos, /dashboard
- Panel en el móvil (mismo wifi): `python dashboard_server.py --red`
- Aprender/autoajuste semanal: `python aprender.py [--telegram]`. Regla de dos tramos (mitad antigua buscar, mitad reciente comprobar), un cambio como mucho, solo parámetros de RANGOS; lo aceptado va a salida/config_auto_<mesa>.json
- Señales de un canal frente a velas reales: `python senales.py [result.json]` o `--capturas`
- Memecoins: `python memes.py grabar` (papel + grabación en datos/memes/) y `python memes.py backtest [--grid]`
- Estrategia de las 9:30 (SPY/QQQ, 5 min): `python orb.py diario` (actualiza velas + informe)
- Kronos opcional: `kronos_ruta` en la config; solo vota si está calibrado
- El bot de ayer (Consejo v5, con Telegram y mesa de memes) está en la rama `consejo-v5-ayer`, carpeta `ayer/`
