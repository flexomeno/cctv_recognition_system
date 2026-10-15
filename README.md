# Monitor CCTV

Lee hasta 10 cámaras RTSP, detecta personas y vehículos, cuenta cada visita una sola vez por cámara y graba un video de 10 segundos cuando la visita se confirma.

Está pensado para un Intel Celeron J4005 con 8 GB de RAM, sin GPU dedicada. Usa el substream de la cámara (cerca de 640×360) y corre una sola inferencia a la vez.

Una visita es un objeto seguido en la misma cámara. Si sale de cuadro más de unos segundos y vuelve a entrar, cuenta como otra visita. No reconoce la misma cara ni la misma placa entre visitas.

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

Edita `config.yaml` con las URLs reales. Ese archivo no se versiona porque lleva usuario y clave. El primer arranque descarga YOLOv8n (pesos de Ultralytics, licencia AGPL-3.0) en `models/yolov8n.onnx` y lo ejecuta a 320 px.

## Uso

```bash
python -m app --config config.yaml
```

Ctrl+C detiene el monitor y cierra el clip que estuviera grabándose. Cada 60 segundos escribe en consola cuántas visitas van hoy por cámara y clase.

Los videos quedan en `data/clips/<cámara>/`. El inventario queda en `data/inventory.db`.

Cuando un clip termina, se envía a Telegram si `config.yaml` tiene `telegram.bot_token` y `telegram.chat_id`. El mensaje lleva la cámara, las detecciones de esos 10 segundos y el video. Crea el bot con @BotFather, escríbele `/start` y el chat id sale de `https://api.telegram.org/bot<token>/getUpdates`.

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

El primer arranque descarga el modelo YOLO, así que el pod necesita salida a internet esa vez. Las cámaras están en `192.168.110.0/24`; el pod tiene que poder llegar a esa red. Si al reiniciar no hay imagen, revisa esa ruta. Para cambiar cámaras o el token de Telegram, vuelve a crear el Secret y reinicia el deployment:

```bash
sudo k3s kubectl -n cctv create secret generic cctv-config \
  --from-file=config.yaml=config.yaml --dry-run=client -o yaml | sudo k3s kubectl apply -f -
sudo k3s kubectl -n cctv rollout restart deployment/cctv-monitor
```

## Qué cuenta

Clases: persona, bicicleta, auto, moto, bus y camión.

Una visita se confirma cuando el mismo objeto aparece en al menos dos análisis seguidos. Si se pierde menos de 2 segundos, sigue siendo la misma. Al cerrarse se guarda cámara, clase, inicio, fin, confianza y la ruta del clip.

El clip dura 10 segundos: unos 3 segundos previos a la confirmación y el resto hasta completar los 10. Si en esa ventana entran más visitas de la misma cámara, comparten el clip.

## Límites

- Con 10 cámaras la detección va por turnos. Un cruce muy rápido puede no alcanzar los dos frames de confirmación.
- Si la cámara se configura en main stream, el CPU se queda decodificando y deja de analizar a tiempo.
- No hay reconocimiento facial ni lectura de placas.
