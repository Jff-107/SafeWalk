# SafeWalk — videos, cámaras y análisis de datos

## Mejoras recientes: detección y reproducción

El panel separa **videos grabados** (`/videos`) y **cámara en vivo** (`/en-vivo`). Ambos usan YOLOv8 para personas, bicicletas, autos, motocicletas, buses y camiones. Las cifras de peatones y vehículos indican detecciones del último fotograma analizado; los cruces se cuentan aparte cuando una trayectoria atraviesa la línea. SQLite guarda una muestra cada segundo y también cuando cambia el conteo.

Al subir un video hay tres modos:

| Modo | Análisis | Reproducción |
| --- | --- | --- |
| Mayor detalle | YOLO a 832 px, todos los fotogramas | Vista anotada mientras se analiza; puede ir lenta en CPU |
| Rápido | YOLO a 768 px sobre las muestras que alcanza a procesar | Reproducción independiente de hasta 30 FPS; cajas y conteos se actualizan con cada muestra |
| Calidad original | YOLO a 960 px, todos los fotogramas | Procesa primero y luego reproduce el MP4 anotado a la resolución original con controles nativos |

En los dos primeros modos puedes pausar, reanudar, retroceder o avanzar 10 segundos y usar la barra de posición. Al saltar se reinicia el seguimiento del tramo y se omiten los fotogramas saltados. El tercer modo muestra el progreso y, al terminar, habilita **Reproducir resultado con fluidez**; el historial abre ese reporte con un reproductor HTML5. Los contadores en vivo están fuera de la imagen. La cámara adapta la resolución enviada a la calidad de la conexión y puede llegar a 960 px.

**Límite práctico:** el video del modo rápido puede verse fluido aunque YOLO procese menos fotogramas; sus cajas son la detección más reciente y no representan cada imagen mostrada. El modo de calidad original tarda más en producir el archivo, pero permite reproducirlo fluidamente después. Se necesita reiniciar el servidor para aplicar cambios en Python.

## Avance: persistencia SQLite y reportes pandas

Cada análisis iniciado desde el panel guarda su video anotado en `outputs/videos/sesion_<id>.mp4`. En el historial, pulsa **Revisar** para abrir el video con controles de reproducción, retroceso/avance de 5 segundos y métricas sincronizadas con el tiempo del video. Los registros previos a esta función no tienen video procesado asociado.

**Resumen por análisis:** cada video tiene su propia sesión en SQLite. El resumen junto al video y el de su página **Revisar** usan únicamente los datos de esa sesión: máximo de personas y vehículos visibles en una muestra, cantidad de muestras con riesgo, cruces alcanzados, alertas generadas y FPS promedio. El máximo visible no equivale a personas o vehículos únicos. Al iniciar otro video el resumen comienza en cero y se actualiza mientras se guardan sus muestras.

El panel incorpora **Base de datos y explorador de registros**: selección de métricas o alertas, filtros por análisis, fechas y riesgo, paginación de 25 registros, indicadores de la selección y descargas CSV/ZIP que aplican los mismos filtros. El estado de SQLite muestra cantidades almacenadas, integridad y relaciones inválidas. Las copias completas se descargan usando la API de respaldo de SQLite.

Indica la **zona monitoreada** al subir un video o activar una cámara. Cada ejecución relaciona su zona y fuente con sus métricas y alertas. Los registros anteriores se conservan; su zona queda como `Sin especificar`.

Para obtener las bases y archivos para entregar:

```powershell
.venv\Scripts\python.exe preparar_entrega_datos.py
```

La carpeta `outputs/entrega_datos` contiene la copia real, una base de demostración separada, CSV, reportes, esquema SQL y el paquete `SafeWalk_entrega_datos.zip`. La base sintética tiene 2 sesiones, 240 métricas y 8 alertas identificadas como simuladas. No se añade al historial real ni prueba precisión de detección.

Consulta `database/README.md` para ver el modelo de datos y una secuencia de exposición, y `database/consultas.sql` para ejecutar consultas sobre cualquiera de las bases. La migración es automática al iniciar y conserva las filas existentes. Antes de esta ampliación se guardó una copia en `outputs/respaldos/safewalk_antes_avance_20261114.db`.

## Ampliación basada en la exposición S14

- Cámara de laptop o celular mediante `getUserMedia` del navegador. El tamaño de los JPEG se adapta a la conexión y el servidor limita el lado mayor a 960 px.
- Cola de cámara de un solo fotograma: se reemplaza la imagen pendiente para evitar acumular retraso. Se detiene la sesión con error si no llegan imágenes durante 30 segundos.
- En cámara, las cajas aparecen sobre el mismo fotograma que analizó YOLO. Mientras carga el modelo o si el análisis demora, se muestra una vista previa sin cajas para evitar marcarlas sobre objetos que ya se movieron.
- Modo rápido: un hilo reproduce el video a su ritmo original, hasta 30 FPS, mientras YOLO analiza las muestras recientes a 768 px. Las cajas se conservan brevemente y pueden omitirse detecciones entre muestras.
- Modo mayor detalle: inferencia a 832 px en todos los fotogramas; modo de calidad original: inferencia a 960 px y reproducción posterior del resultado H.264.
- Visor de JPEG completos mediante `/api/video/frame`: solicita una imagen nueva a la vez, libera la anterior y reintenta si falla. Evita que el navegador mantenga una carga MJPEG indefinida. `/video_feed` se conserva por compatibilidad.
- SQLite con sesiones, métricas y alertas relacionadas por `sesion_id`. Los registros anteriores se conservan sin sesión; no se les atribuye una fuente inventada.
- Pandas para exportar CSV, agrupar por minuto y calcular estadísticas. Matplotlib genera el gráfico de tendencias del último análisis.
- Historial de las últimas 50 sesiones, descarga de una copia coherente de SQLite y CSV actualizados al descargarlos.
- Gráfico en vivo con consulta automática cada segundo a `/api/tendencias`, limitado a las últimas 120 muestras de la última sesión. El PNG de Matplotlib se conserva como gráfico por minuto para consultar/exportar, sin regenerarlo continuamente durante la reproducción.
- Indicadores separados de FPS de reproducción y FPS de YOLO. Los FPS de reproducción corresponden a imágenes generadas en el servidor; la red y el navegador pueden reducir lo que se ve en pantalla.

La exposición S14 describe herramientas y posibles algoritmos, pero no contiene criterios de aceptación de los sprints 1–3. La siguiente distribución sirve como propuesta para revisar contra el backlog del curso:

| Sprint propuesto | Funcionalidad disponible | Validación pendiente con datos reales |
| --- | --- | --- |
| 1 | Videos, captura de cámara, detección de personas y vehículos con YOLO/OpenCV | Precisión de detección en tránsito local |
| 2 | Seguimiento, conteo de cruces, riesgo por proximidad y alertas visuales | Errores de seguimiento y calibración del riesgo |
| 3 | SQLite, pandas, reportes, tendencias con Matplotlib e historial web | Aceptación del docente y demostración en laptop/celular |

YOLO utiliza un modelo preentrenado. El proyecto no entrena un clasificador propio de riesgo. SVM, KNN, regresión logística, árboles y K-Means aparecen en la exposición como posibilidades; no están implementados. Tampoco se calcula una probabilidad de accidente ni se comparan intersecciones: faltan datos etiquetados y metadatos de ubicación para ello.

### Usar la cámara

#### Celular como cámara remota: enlace y QR

El modo móvil incorpora una página ligera para el teléfono, vista previa, selección frontal/trasera, permiso de cámara y botones de transmisión. La laptop recibe los JPEG, ejecuta YOLO y muestra las detecciones, alertas y estadísticas. El celular no necesita instalar una aplicación.

1. Detén el servidor anterior con `Ctrl+C` para trabajar con un solo panel.
2. Ejecuta desde la raíz del proyecto:

   ```powershell
   .venv\Scripts\python.exe iniciar_movil.py
   ```

3. La terminal mostrará un enlace **para la laptop**. Ábrelo; te vinculará y mostrará el panel con el QR.
4. Escanea el QR con el celular o copia en él el segundo enlace de la terminal. Ábrelo en el navegador del teléfono.
5. Elige **Trasera**, pulsa **Conectar y transmitir** y permite el acceso a la cámara.
6. Mantén la página abierta y el teléfono desbloqueado. Observa las detecciones en el panel de la laptop, en el puerto **5050**.
7. Usa **Detener cámara** para finalizar la captura; `Ctrl+C` en la terminal del modo móvil cierra también el túnel y el servidor.

Ambos dispositivos necesitan Internet; pueden estar en la misma Wi-Fi o en redes diferentes. El enlace HTTPS temporal usa [Cloudflare Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/). Las imágenes pasan por Cloudflare para llegar a la laptop. El enlace contiene una clave aleatoria de vinculación; no lo publiques. La clave cambia al reiniciar y el panel, los reportes y las APIs del modo móvil requieren vinculación. Esto es un modo de demostración académica, sin usuarios ni roles de producción.

El lanzador usa `tools/cloudflared.exe` si está presente; este ejecutable no se incluye en Git. En otro equipo, instala las dependencias Python y descarga cloudflared desde su [sitio oficial](https://developers.cloudflare.com/tunnel/downloads/) o instala `Cloudflare.cloudflared` mediante winget. No se necesita abrir puertos del router ni instalar certificados en el teléfono.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
winget install --id Cloudflare.cloudflared --exact
.venv\Scripts\python.exe iniciar_movil.py --check
```

Si cloudflared ya está en `tools/`, omite la instalación con winget. El parámetro `--check` comprueba dependencias sin publicar el servidor.

Si no aparece el QR, abre el panel desde el enlace **para laptop** de la terminal del modo móvil; el panel normal en el puerto 5000 pertenece a otra instancia. Si el teléfono bloquea la cámara, abre el enlace en su navegador completo, revisa el permiso y cierra otras apps que la estén usando. Si el túnel no inicia, revisa la conexión y posibles archivos de configuración previos de cloudflared. Un túnel temporal puede tardar algunos segundos en responder.

El enlace y el acceso se verifican mediante pruebas automatizadas. La transmisión física debe comprobarse con un teléfono real.

#### Cómo viaja la imagen y cómo se reduce la demora

`Cámara del celular → JPEG por HTTPS → Cloudflare → Flask en la laptop → vista en vivo + YOLO por separado`.

El QR solo abre el enlace de vinculación. La cámara se obtiene mediante el navegador; no existe una conexión USB ni una conexión directa entre dispositivos en este modo. La vuelta por Internet añade latencia incluso si ambos equipos usan la misma Wi-Fi.

La transmisión permite como máximo dos peticiones simultáneas y no acumula una cola de fotografías pendientes. El móvil captura imágenes nuevas cuando hay espacio, reduce resolución y calidad cuando la respuesta tarda y reintenta después de fallos temporales. Se identifica cada imagen con un número de secuencia y una clave de sesión: si llega una imagen antigua después de una nueva, se descarta. El límite adaptable es de 640, 480 o 384 px en el lado mayor; una imagen más pequeña reduce detalle para objetos lejanos.

La laptop muestra los fotogramas recibidos en un hilo independiente de YOLO, incluso mientras el modelo se prepara. YOLO consume la imagen pendiente más reciente, descarta muestras que han esperado más de 750 ms y reutiliza el modelo al reconectar. Las cajas antiguas se ocultan después de medio segundo y cuando cambia la forma de la imagen. La vista puede seguir fluida aunque las detecciones se actualicen más lentamente.

El teléfono muestra el tiempo de respuesta de sus peticiones (ida, recepción y respuesta; no es una medición exacta de latencia de pantalla). El panel muestra FPS recibidos y la edad de la última imagen. Si no llegan imágenes recientes, lo señala en el estado. Estos indicadores permiten distinguir una conexión lenta de una inferencia lenta. No se puede garantizar demora cero ni recuperar movimiento que no llegó por la red.

Pruebas del transporte JavaScript con Node.js:

```powershell
node --test tests/camera_transport.test.cjs
node --test tests/camera_browser.test.cjs
```

El cliente conserva el contexto del `fetch` del navegador al enviar imágenes. No depende de que `currentTime` avance para capturar un flujo de cámara. Las páginas usan scripts con versión para impedir que una vinculación nueva reutilice código antiguo. El contador de imágenes entregadas indica respuestas correctas del servidor; no mide la cantidad de inferencias YOLO.

#### Orientación y fluidez del celular

Activa la rotación automática del teléfono. La captura sigue la orientación de la pantalla; si el navegador ya entrega un video horizontal, se conserva sin girarlo otra vez. Si mantiene una captura vertical, el canvas compensa el giro antes de generar el JPEG. El botón **Girar imagen 90°** permite corregir manualmente una orientación bloqueada o invertida. El panel de la laptop adapta su proporción al fotograma recibido y limita la altura de las imágenes verticales, sin estirarlas.

Al cambiar las dimensiones de la captura se reinicia el seguimiento de IDs para no interpretar el giro como un cruce vial. Se conservan los totales de la sesión, pero el conteo sigue siendo aproximado.

En conexiones de respuesta rápida se intenta capturar hasta 30 FPS. La codificación se incluye en el intervalo de captura, se evitan duplicados mediante `requestVideoFrameCallback` cuando está disponible y los mensajes del teléfono se actualizan a menor frecuencia para reducir trabajo de la interfaz. Se mantiene un máximo de dos envíos pendientes. Los FPS efectivos dependen del teléfono, la red, el túnel y la laptop; no se garantiza llegar a 30 FPS.

```powershell
node --test tests/camera_geometry.test.cjs tests/camera_browser.test.cjs tests/camera_transport.test.cjs
```

#### Cámara local o despliegue propio

En la laptop, ejecuta `python app.py`, abre **http://localhost:5000**, pulsa **Activar cámara** y concede permiso. Usa **Apagar cámara** antes de cambiar de fuente. Se permite un análisis simultáneo en todo el servidor.

Para un celular, ambos equipos deben poder acceder al servidor y la página debe servirse mediante **HTTPS con certificado confiable** (por ejemplo, un proxy HTTPS configurado para la red). Una dirección `http://192.168...:5000` normalmente no permite acceso a la cámara. `localhost` en el celular representa al propio celular, no a la laptop.

Para que Flask escuche en la red, antes de ejecutarlo en PowerShell:

```powershell
$env:SAFEWALK_HOST = "0.0.0.0"
python app.py
```

Esto habilita escucha en red; no configura HTTPS ni el firewall. El servidor de desarrollo es para una demostración en red de confianza: no tiene autenticación y no debe publicarse directamente en Internet. No se ha validado aún la cámara física de la laptop ni un teléfono real.

### Verificación automatizada

```powershell
python -m unittest discover -s tests -v
```

Las pruebas usan bases temporales. Comprueban persistencia por sesión, migración repetible, reportes, gráfico PNG, copia SQLite y validación de cámara. También ejecutan el YOLO real sobre un video sintético; esta prueba comprueba la integración, no la precisión ni los FPS con tránsito real.

## Funciones incluidas
- Carga de un video desde la página web (MP4, AVI, MOV, MKV o WEBM; máximo 500 MB).
- Procesamiento del video cargado con el modelo YOLOv8 incluido en `data/models/yolov8n.pt`.
- Video procesado visible en el navegador, con bounding boxes, clases, nivel de riesgo, línea de conteo y alertas visuales.
- Conteo aproximado de cruces peatonales y vehiculares mediante seguimiento ByteTrack.
- Panel de indicadores, estado del análisis, historial de alertas y descarga de CSV/SQLite.
- Registro de métricas y alertas en SQLite y exportación a `outputs/metricas.csv`, `outputs/alertas.csv` y `outputs/resumen.csv`.

> **Alcance académico:** el nivel de riesgo es una heurística basada en proximidad entre cajas, no una probabilidad validada de colisión. El conteo depende de la continuidad de los IDs de seguimiento y de la posición de la línea. Los resultados deben validarse con videos reales.

## 1. Requisitos
- Python 3.10 o superior recomendado.
- El modelo `data/models/yolov8n.pt` está incluido en el ZIP.
- Un video de tránsito propio para probar el sistema. El ZIP no incluye videos por tamaño y privacidad.

## 2. Instalación en Windows
Abre la carpeta `SafeWalk` en VS Code y ejecuta en la terminal:

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Ejecutar la interfaz web
Desde la carpeta raíz `SafeWalk`:

```powershell
.venv\Scripts\activate
python app.py
```

Abre **http://127.0.0.1:5000** en el navegador. En la sección **Monitoreo de video con YOLO**, selecciona un video y pulsa **Subir y analizar**. El sistema guardará una copia en `data/videos/uploads/` y comenzará a analizarlo; la imagen procesada aparecerá dentro de la página. Puedes detener el análisis con el botón correspondiente. Mantén abierta la terminal del servidor.

La carga está limitada a 500 MB. Para archivos de video muy grandes, reduce la resolución o duración antes de subirlos. El análisis se ejecuta localmente en el equipo; el video no se envía a un servicio externo.

## 4. Ejecutar la versión de escritorio (alternativa)
También se conserva el modo de ventana OpenCV:

```powershell
python -m src.main data/videos/videoTransito.mp4
```

Presiona `q` para cerrar esa ventana. Para webcam compatible, `python -m src.main 0`.

## 5. Archivos de salida
- `outputs/safewalk.db`: SQLite con tablas `sesiones`, `metricas` y `alertas`.
- `outputs/metricas.csv`: conteo, riesgos y FPS por registro.
- `outputs/alertas.csv`: fecha, nivel y descripción de las alertas.
- `outputs/resumen.csv`: indicadores generales de la ejecución.

## 6. Pruebas para la entrega
1. Subir un video válido y comprobar que se muestra procesado en el navegador.
2. Confirmar las cajas YOLO para peatones y vehículos.
3. Verificar etiquetas de riesgo Bajo/Medio/Alto y alertas visuales cuando corresponda.
4. Confirmar que los contadores se actualizan y que se registra el flujo aproximado.
5. Descargar los CSV y comprobar que contienen registros de la ejecución.
6. Probar un formato no admitido y verificar que el sistema muestre un mensaje.
7. Tomar capturas reales de la página y del análisis para el informe.

No uses videos privados ni subas videos o bases de datos al repositorio si contienen información sensible.
