# Monitor CCTV

Lee hasta 10 cámaras RTSP en el exterior y, cuando un bloque del cuadro se mueve, graba un video de 10 segundos y lo envía a Telegram. No clasifica personas, vehículos ni animales: el viento mueve puntos sueltos y eso no abre un clip; un animal, una persona o un vehículo dejan una mancha continua.

Está pensado para un Intel Celeron J4005 con 8 GB de RAM, sin GPU dedicada. La comparación de frames es liviana y corre en cada imagen nueva.

## Requisitos

- Python 3.11 o 3.12. Python 3.14 no tiene paquete de `onnxruntime`, y `pip install` falla con "No matching distribution"
- En Mac, OpenCV no abre RTSP. El programa usa ffmpeg, que entra con `imageio-ffmpeg`
- Las cámaras en RTSP, con el substream y no el main stream 1080p o 4K

Ejemplos de substream (el segundo flujo, el de menor resolución):

- Hikvision: `rtsp://usuario:clave@192.168.1.101:554/Streaming/Channels/102`
- Dahua: `rtsp://usuario:clave@192.168.1.101:554/cam/realmonitor?channel=1&subtype=1`
- Genérico: `rtsp://usuario:clave@192.168.1.101:554/stream2`

## Instalación

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Edita `config.yaml` con las URLs reales. Ese archivo no se versiona porque lleva usuario y clave. La sensibilidad está en `motion`: bajar `diff_threshold` o `min_area_ratio` produce más videos, incluido el movimiento de hojas.

## Uso

```bash
python -m app --config config.yaml
```

Ctrl+C detiene el monitor y cierra el clip que estuviera grabándose. Cada 60 segundos escribe en consola cuántos videos van hoy por cámara.

Los videos quedan en `data/clips/<cámara>/`. El inventario queda en `data/inventory.db`.

Cuando un clip termina, se envía a Telegram si `config.yaml` tiene `telegram.bot_token` y `telegram.chat_id`. El mensaje lleva la cámara y la hora. Crea el bot con @BotFather, escríbele `/start` y el chat id sale de `https://api.telegram.org/bot<token>/getUpdates`.

```bash
python -m app.report --config config.yaml
python -m app.report --config config.yaml --desde 2026-10-01
```

## Kubernetes en el NUC

Todo queda en el namespace `cctv`. No usa el namespace `energy-monitoring` ni el volumen `video-capture-storage`. Un solo pod, volumen propio de 20 GB en `local-path`, y un tope de 1 CPU y 1,5 GB para dejar el otro núcleo a Grafana, InfluxDB, Mosquitto y Telegraf. Las claves van en un Secret armado desde `config.yaml`, no dentro de la imagen. En este NUC el `kubectl` del usuario no autentica; el script usa `sudo k3s kubectl`.

El repositorio es público y `config.yaml` no se sube. En el NUC, después del pull, copia ese archivo al clon y construye ahí. Hace falta Docker o nerdctl, y k3s:

```bash
git clone https://github.com/flexomeno/cctv_recognition_system.git
cd cctv_recognition_system
# config.yaml se copia desde la Mac; no está en GitHub
sh deploy/install.sh
sudo k3s kubectl -n cctv logs -f deployment/cctv-monitor
```

Las cámaras están en `192.168.110.0/24`; el pod tiene que poder llegar a esa red. Si al reiniciar no hay imagen, revisa esa ruta. Para cambiar la sensibilidad, las cámaras o el token de Telegram, vuelve a crear el Secret y reinicia el deployment:

```bash
sudo k3s kubectl -n cctv create secret generic cctv-config \
  --from-file=config.yaml=config.yaml --dry-run=client -o yaml | sudo k3s kubectl apply -f -
sudo k3s kubectl -n cctv rollout restart deployment/cctv-monitor
```

## Cámaras del NUC

El DVR Dahua `192.168.110.110` aporta cinco canales, `dvr_ch1` a `dvr_ch5`, por el flujo principal (`subtype=0`). Las URLs viven solo en `config.yaml`. Con movimiento, las cinco se revisan en cada frame nuevo. Decodificar cinco flujos en alta resolución es lo que más CPU usa; si el NUC se queda corto, el mismo canal en `subtype=1` baja la carga.

## Sensibilidad

`motion.diff_threshold` (1–255) es cuánto tiene que cambiar un píxel. `motion.min_area_ratio` es la fracción del cuadro que tiene que ocupar un solo bloque. `motion.confirm_frames` es cuántos frames seguidos hace falta ver ese bloque. `motion.cooldown_s` espera entre un video y el siguiente de la misma cámara.

El clip dura 10 segundos: unos 3 segundos previos al movimiento y el resto hasta completar los 10.

## Límites

- Una ráfaga de viento sobre un árbol grande puede formar un bloque y abrir un clip. Sube `min_area_ratio` o `diff_threshold` si pasa seguido.
- Un animal que cruza muy pegado al borde y ocupa menos del bloque mínimo no abre clip. Baja `min_area_ratio`.
- Cinco flujos principales a la vez pueden llenar el núcleo asignado al pod. El substream deja margen.
