# Vigilancia del DVR

En el NUC corre Kerberos Agent, la versión libre, un contenedor por canal. Cuando hay movimiento graba un clip corto y otro contenedor lo manda al mismo chat de Telegram. No usa Kerberos Hub.

## Por qué Kerberos

El primer monitor era este repositorio: Python, comparación de frames y un pod en k3s. Servía para no identificar personas ni placas, solo para avisar movimiento en el exterior. En el NUC se quedó corto.

El equipo es un Celeron J4005, dos núcleos, 8 GB, sin GPU. El pod tenía un tope de 1 CPU para no quitarle recursos a Grafana, InfluxDB, Mosquitto y Telegraf. Con los cinco canales del DVR en alta (`subtype=0`), decodificar llenaba ese núcleo. El programa pedía 8 fps y analizaba cerca de 1,2 fps por cámara. Un cruce de menos de un segundo caía entre dos frames. Bajar el umbral y comparar contra un frame anterior (`frame_gap`) hizo el detector más sensible, y el ritmo no subió: el límite era la decodificación, no el tamaño del bloque.

Kerberos Agent ya hace esa vigilancia. Un proceso por cámara, RTSP en H.264 o H.265, clip con unos segundos antes y después del movimiento, y un umbral de píxeles cambiados. En el NUC, los agentes juntos usan poco CPU porque miran el substream y solo comparan keyframes.

Elegimos la versión libre, `kerberos/agent`, con `AGENT_OFFLINE=true`. Kerberos Hub sí envía Telegram, pero es el servicio de pago: el agente sube la grabación a la nube y Hub manda el aviso. El agente libre no tiene canal de Telegram. El video lo manda `deploy/kerberos/sender.py`, un contenedor que mira los mp4 ya cerrados y llama al mismo bot. Nada de eso abre un puerto hacia internet; la interfaz queda en la LAN.

El agente configura un solo RTSP. Por eso hay un contenedor por canal, no uno con los seis streams. Los puertos 8091–8096 evitan el 80, que puede usar el balanceador de k3s.

| Canal | Interfaz | Flujo |
| --- | --- | --- |
| dvr_ch1 | http://192.168.110.144:8091 | substream, H.265 352×240 |
| dvr_ch2 | http://192.168.110.144:8092 | substream, H.265 352×240 |
| dvr_ch3 | http://192.168.110.144:8093 | substream, H.264 640×360 |
| dvr_ch4 | http://192.168.110.144:8094 | main, H.264 1920×1080 |
| dvr_ch5 | http://192.168.110.144:8095 | substream, H.264 640×480 |
| dvr_ch6 | http://192.168.110.144:8096 | substream, H.264 640×480 |

El canal 4 quedó en el flujo alto porque su substream no contestó: cuatro intentos seguidos terminaron por tiempo de espera. El resto, incluido el canal 6, usa `subtype=1`. La región es el cuadro completo, el clip dura como máximo 15 segundos (5 antes y 8 después) y cada agente borra grabaciones al pasar de 400 MB. La zona horaria es `America/Bogota`. El umbral es 150 píxeles en el canal 1 y 300 en los canales 2 a 6. Las URLs, el token del bot y la clave de la interfaz están solo en el NUC, en `/home/agente/kerberos/`.

El namespace `cctv` se eliminó del NUC para que el pod no siguiera decodificando los mismos canales. `energy-monitoring` no se tocó. Los manifiestos de `deploy/k8s` siguen en el repo como referencia; `deploy/install.sh` ya no es el camino de despliegue.

## Cómo se desplegaron

En el NUC, con Docker, no dentro de k3s. El plugin `docker compose` no está instalado, así que `deploy/kerberos/up.sh` hace `docker run`. Las imágenes son `kerberos/agent:latest` y `python:3.11-slim`. Todo vive en `/home/agente/kerberos/` y en la red `kerberos-net`.

```text
/home/agente/kerberos/
  up.sh
  panel.py               copia de deploy/kerberos/panel.py
  sender.py              copia de deploy/kerberos/sender.py
  telegram.env           token y chat id, no se versiona
  sent/sent.json         mp4 que ya se mandaron
  dvr_ch1/agent.env      un archivo por canal, no se versiona
  dvr_ch1/config/        umbral, zona y duraciones; no se versiona
  dvr_ch1/recordings/
  dvr_ch2/ ... dvr_ch6/
```

`agent.env` sale de `deploy/kerberos/agent.env.example`. `telegram.env` sale de `deploy/kerberos/telegram.env.example`. Para repetir el arranque, con esos archivos ya creados:

```bash
cd /home/agente/kerberos
sh up.sh
```

Eso crea ocho contenedores, todos con `--restart unless-stopped`. Docker arranca con el NUC, así que lo que estaba encendido vuelve solo. Un canal apagado sigue apagado.

Seis se llaman `kerberos-dvr-ch1` … `kerberos-dvr-ch6`. Cada uno publica su puerto 80 en 8091–8096, lee su `agent.env` y monta dos carpetas: `recordings` en `/home/agent/data/recordings` y `config` en `/home/agent/data/config`. El umbral, la zona, la duración y el interruptor de movimiento viven en `config/config.json`. Por eso **Save** en el tablero de Kerberos sí se queda, también después de un reinicio. La URL, el modo offline, la zona horaria y la clave del tablero siguen en `agent.env`. Esas sí exigen volver a crear el contenedor: `docker restart` no relee ese archivo.

El canal 6 se agregó sobre el mismo DVR (`channel=6`, substream). Su umbral inicial es 300 y queda en `dvr_ch6/config/config.json`, no en `agent.env`, para que el tablero pueda cambiarlo. El contenedor `kerberos-telegram` monta también `dvr_ch6/recordings`, y el panel lo lista en el puerto 8096.

`kerberos-telegram` reenvía los mp4. `kerberos-panel`, en http://192.168.110.144:8090, enciende o apaga cada canal, tiene un enlace a su tablero, y el usuario y la clave están en campos ocultos con un ojo para mostrarlos. Apagar un canal hace `docker stop`. No decodifica ni manda videos hasta que se enciende de nuevo.

## Cómo se articula con sender.py

Kerberos no llama a Telegram. Cuando cierra un mp4 lo deja en la carpeta de grabaciones de ese canal. `up.sh` monta las seis carpetas, solo lectura, dentro de `kerberos-telegram`:

```text
dvr_ch1/recordings  ->  /recordings/dvr_ch1
dvr_ch2/recordings  ->  /recordings/dvr_ch2
...
dvr_ch6/recordings  ->  /recordings/dvr_ch6
sender.py           ->  /sender.py
sent/               ->  /sent
```

El contenedor ejecuta `python -u /sender.py`. El script recorre `/recordings`, espera a que el mp4 deje de crecer y llama a `sendVideo` con el bot de `telegram.env`. El nombre de la carpeta (`dvr_ch1`, `dvr_ch2`, …) es el de la cámara en el mensaje.

La descripción lleva la cámara, la hora de toma en UTC−5 y los píxeles que abrieron el clip. Esos píxeles son el penúltimo número del archivo. En `…_335_15894.mp4`, 335 son los píxeles y 15894 es la duración en milisegundos.

Manda primero el clip más antiguo que todavía no se haya enviado. No descarta los atrasados: mientras se calibra el umbral, la cola se vacía en orden. Un `docker restart kerberos-telegram` alcanza para cargar un `sender.py` nuevo, porque el script está montado y no va horneado en la imagen.

El umbral se cambia en el tablero del canal, en **Recording threshold**, y se guarda con **Save**. Un número más bajo produce más videos, también con viento. Kerberos compara keyframes, no cada frame: en estos substreams hay cerca de uno por segundo. La zona se dibuja en ese mismo tablero. Sin una zona, Kerberos no detecta movimiento; la que está guardada ahora es el cuadro completo.

## Detector en Python

El código de `app/` es el monitor anterior. Sigue pudiendo correr en una máquina con Python, y ya no es lo que vigila el NUC.

Lee hasta 10 cámaras RTSP y, cuando un bloque del cuadro se mueve, graba 10 segundos y puede mandarlos a Telegram. No clasifica personas, vehículos ni animales.

### Requisitos

- Python 3.11 o 3.12. Python 3.14 no tiene paquete de `onnxruntime`, y `pip install` falla con "No matching distribution"
- En Mac, OpenCV no abre RTSP. El programa usa ffmpeg, que entra con `imageio-ffmpeg`
- En el NUC, cinco flujos principales no dan los 8 fps pedidos. El substream baja la carga

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
python -m app --config config.yaml
```

`config.yaml` no se versiona. La sensibilidad está en `motion`: `frame_gap` es contra cuántos frames atrás se compara, `diff_threshold` cuánto tiene que cambiar un píxel y `min_area_ratio` el bloque mínimo. El inventario queda en `data/inventory.db` y los clips en `data/clips/<cámara>/`.

```bash
python -m app.report --config config.yaml
```
