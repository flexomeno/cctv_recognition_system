# Vigilancia del DVR

En el NUC corre Kerberos Agent, la versión libre, un contenedor por canal. Cuando hay movimiento graba un clip corto y otro contenedor lo manda al mismo chat de Telegram. No usa Kerberos Hub.

## Por qué Kerberos

El primer monitor era este repositorio: Python, comparación de frames y un pod en k3s. Servía para no identificar personas ni placas, solo para avisar movimiento en el exterior. En el NUC se quedó corto.

El equipo es un Celeron J4005, dos núcleos, 8 GB, sin GPU. El pod tenía un tope de 1 CPU para no quitarle recursos a Grafana, InfluxDB, Mosquitto y Telegraf. Con los cinco canales del DVR en alta (`subtype=0`), decodificar llenaba ese núcleo. El programa pedía 8 fps y analizaba cerca de 1,2 fps por cámara. Un cruce de menos de un segundo caía entre dos frames. Bajar el umbral y comparar contra un frame anterior (`frame_gap`) hizo el detector más sensible, y el ritmo no subió: el límite era la decodificación, no el tamaño del bloque.

Kerberos Agent ya hace esa vigilancia. Un proceso por cámara, RTSP en H.264 o H.265, clip con unos segundos antes y después del movimiento, y un umbral de píxeles cambiados. En el NUC, los cinco agentes juntos usan alrededor del 8% de CPU porque miran el substream y solo comparan keyframes.

Elegimos la versión libre, `kerberos/agent`, con `AGENT_OFFLINE=true`. Kerberos Hub sí envía Telegram, pero es el servicio de pago: el agente sube la grabación a la nube y Hub manda el aviso. El agente libre no tiene canal de Telegram. El video lo manda `deploy/kerberos/sender.py`, un contenedor que mira los mp4 ya cerrados y llama al mismo bot. Nada de eso abre un puerto hacia internet; la interfaz queda en la LAN.

El agente configura un solo RTSP. Por eso hay un contenedor por canal, no uno con los cinco streams. Los puertos 8091–8095 evitan el 80, que puede usar el balanceador de k3s.

| Canal | Interfaz | Flujo |
| --- | --- | --- |
| dvr_ch1 | http://192.168.110.144:8091 | substream, H.265 352×240 |
| dvr_ch2 | http://192.168.110.144:8092 | substream, H.265 352×240 |
| dvr_ch3 | http://192.168.110.144:8093 | substream, H.264 640×360 |
| dvr_ch4 | http://192.168.110.144:8094 | main, H.264 1920×1080 |
| dvr_ch5 | http://192.168.110.144:8095 | substream, H.264 640×480 |

El canal 4 quedó en el flujo alto porque su substream no contestó: cuatro intentos seguidos terminaron por tiempo de espera. El resto usa `subtype=1`. La región es el cuadro completo, el umbral es 150 píxeles, el clip dura como máximo 15 segundos (5 antes y 8 después) y cada agente borra grabaciones al pasar de 400 MB. La zona horaria es `America/Bogota`. Las URLs, el token del bot y la clave de la interfaz están solo en el NUC, en `/home/agente/kerberos/`.

El namespace `cctv` se eliminó del NUC para que el pod no siguiera decodificando los mismos canales. `energy-monitoring` no se tocó. Los manifiestos de `deploy/k8s` siguen en el repo como referencia; `deploy/install.sh` ya no es el camino de despliegue.

Para repetir el arranque, en el NUC:

```bash
# agent.env y telegram.env se copian desde los ejemplos y no se commitean
sh deploy/kerberos/up.sh
```

`AGENT_CAPTURE_PIXEL_CHANGE` es la sensibilidad. Un número más bajo produce más videos, también con viento. Kerberos compara keyframes, no cada frame: en estos substreams hay cerca de uno por segundo.

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
