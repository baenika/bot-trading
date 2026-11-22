#!/bin/bash
# Instala el Consejo en un servidor Ubuntu (22.04 o 24.04) para que funcione 24/7 sin la torre.
#
#   curl -fsSL https://raw.githubusercontent.com/baenika/bot-trading/consejo-v1/servidor/instalar.sh | sudo bash
#
# Qué hace: crea el usuario «consejo», baja el repo, instala Python, pone la hora de Madrid y deja
# los servicios listos. NO pide ni guarda claves de exchange: todo es papel (dinero ficticio).
# El token de Telegram lo pones tú después con:  sudo -u consejo python3 /home/consejo/bot-trading/configurar_telegram.py
set -euo pipefail
RAMA="${RAMA:-consejo-v1}"
REPO="https://github.com/baenika/bot-trading.git"
DIR=/home/consejo/bot-trading

[ "$(id -u)" = 0 ] || { echo "Lánzalo con sudo."; exit 1; }
echo "== Paquetes"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-pytest git ca-certificates >/dev/null
timedatectl set-timezone Europe/Madrid || true

echo "== Usuario y repo"
id consejo >/dev/null 2>&1 || useradd -m -s /bin/bash consejo
if [ -d "$DIR/.git" ]; then
    runuser -u consejo -- git -C "$DIR" fetch -q origin "$RAMA"
    runuser -u consejo -- git -C "$DIR" checkout -q "$RAMA"
    runuser -u consejo -- git -C "$DIR" merge -q --ff-only "origin/$RAMA"
else
    runuser -u consejo -- git clone -q -b "$RAMA" "$REPO" "$DIR"
fi
runuser -u consejo -- mkdir -p "$DIR/salida" "$DIR/datos"

echo "== Tests"
runuser -u consejo -- env PYTHONUTF8=1 python3 -m pytest -q "$DIR/tests" | tail -1

echo "== Servicios"
cp "$DIR"/servidor/consejo-*.service "$DIR"/servidor/consejo-*.timer /etc/systemd/system/
chmod +x "$DIR/servidor/actualizar.sh"
systemctl daemon-reload
systemctl enable -q consejo-cripto.service consejo-semanal.timer consejo-orb.timer consejo-actualizar.timer
systemctl start consejo-semanal.timer consejo-orb.timer consejo-actualizar.timer

echo "== Datos históricos para el autoajuste (unos minutos)"
runuser -u consejo -- bash -c "cd $DIR && PYTHONUTF8=1 python3 descargar_datos.py cripto" || \
    echo "   (no se pudieron bajar ahora; el autoajuste del domingo lo reintenta)"

if [ -f "$DIR/telegram.json" ]; then
    systemctl restart consejo-cripto.service
    echo "Listo: el Consejo está en marcha. Escribe /panel a tu bot en Telegram."
else
    cat <<FIN

Casi listo. Falta conectar tu bot de Telegram (una sola vez):
  1. Para el bot de la torre (solo una copia puede leer los comandos a la vez).
  2. Escribe «hola» a tu bot en Telegram.
  3. Aquí:  sudo -u consejo python3 $DIR/configurar_telegram.py   (pega el token; no se ve ni se sube)
     o copia el telegram.json de la torre a $DIR/
  4. sudo systemctl restart consejo-cripto
Opcionales:  sudo systemctl enable --now consejo-mercados consejo-memes
Ver qué hace:  journalctl -u consejo-cripto -f
FIN
fi
