# El Consejo 24/7 en un servidor (sin la torre encendida)

Todo sigue en **papel**: dinero ficticio, sin claves de exchange, sin órdenes reales.

## Qué hace en el servidor

| Servicio | Qué hace | Por defecto |
|---|---|---|
| `consejo-cripto` | Papel en vivo de BTC y ETH cada 5 min, avisos de **opción de compra** y el mando de Telegram (`/panel`, `/analizar oro`…) | encendido |
| `consejo-semanal.timer` | Domingos 22:00 (Madrid): baja velas, autoajuste e informe semanal al móvil | encendido |
| `consejo-orb.timer` | L-V 22:30: estrategia de las 9:30 (SPY/QQQ) | encendido |
| `consejo-actualizar.timer` | Cada día 05:00: baja la última versión, pasa los tests y reinicia (te avisa de las novedades) | encendido |
| `consejo-mercados` | Papel de índices, ETFs y oro (solo avisa; en el backtest no tuvo ventaja) | apagado |
| `consejo-memes` | Memecoins en papel + grabación | apagado |

Solo **una** copia del bot puede leer los comandos de Telegram a la vez: cuando arranque en el
servidor, cierra el `papel.py` de la torre (si no, Telegram responde «409» y los botones fallan).

## Pasos (unos 20 minutos, una sola vez)

1. **Crea el servidor**: Ubuntu 24.04, la máquina más pequeña vale (1 vCPU y 1 GB bastan).
   Añade tu clave SSH o apunta la contraseña que te den.
2. **Entra** desde el ordenador (PowerShell): `ssh ubuntu@IP-DEL-SERVIDOR` (en Hetzner el usuario es `root`).
3. **Instala**:
   ```
   curl -fsSL https://raw.githubusercontent.com/baenika/bot-trading/consejo-v1/servidor/instalar.sh | sudo bash
   ```
4. **Telegram**, una de dos:
   - Copia el `telegram.json` de la torre (desde la carpeta del bot en la torre):
     `scp telegram.json ubuntu@IP:/tmp/` y en el servidor
     `sudo install -o consejo -m 600 /tmp/telegram.json /home/consejo/bot-trading/ && rm /tmp/telegram.json`
   - O créalo allí: `sudo -u consejo python3 /home/consejo/bot-trading/configurar_telegram.py`
5. **Cierra el bot de la torre** y arranca el del servidor: `sudo systemctl restart consejo-cripto`
6. En Telegram: escribe `/panel` al bot. Te llega «Consejo cripto en marcha (papel)».

### Tu grupo de Telegram

1. Añade el bot al grupo.
2. Escribe en el grupo `/vincular@CHICOV1Bot` (tiene que ser **tu** cuenta).
3. Desde ese momento las opciones de compra, los avisos y los informes van al grupo. En el grupo
   solo obedece tus comandos y botones; si alguien más escribe, lo ignora.
   `/desvincular` lo devuelve a tu chat privado.

### Comandos desde el móvil

- `/analizar` → BTC, ETH, S&P 500, Nasdaq y oro: precio, tendencia, RSI, volatilidad, si el Scout ve
  algo raro y, si lo ve, el plan (entrada, stop, objetivo) con la probabilidad de la máquina del tiempo.
- `/analizar oro btc` → solo esos (también plata, nasdaq, sp500, dow, ibex, dax, petroleo, eurusd, bonos).
- `/cartera`, `/parametros`, `/avisos`, `/panel` (botones).

### Avisos que llegan solos

- 🟢 **OPCIÓN DE COMPRA**: el Consejo dice COMPRAR (el papel entra solo, tú no haces nada).
- 🟡 **POSIBLE COMPRA**: espera un retroceso a un precio concreto.
- 📥 entradas y 🟢/🔴 cierres en papel, ⚠️ errores, 📡 datos que no llegan, ⛔ paradas, 🆕 versión nueva,
  ⚙️ autoajuste, informe semanal los domingos.

## Mantenimiento

- Ver qué hace: `journalctl -u consejo-cripto -f`
- Reiniciar: `sudo systemctl restart consejo-cripto`
- Encender las mesas opcionales: `sudo systemctl enable --now consejo-mercados consejo-memes`
- Llevarte la cartera en papel de la torre (opcional): copia `salida/papel_cripto.json` a
  `/home/consejo/bot-trading/salida/` antes del paso 5.
- El panel web no se abre a internet a propósito (no tiene contraseña). Desde Telegram ves lo mismo.
