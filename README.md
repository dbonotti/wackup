# 💬 WAckup 3.0 - Respaldo Local Pasivo, Copiloto CRM y Modo Equipo

> **WAckup 3.0** es la suite local definitiva para WhatsApp Web: combina un **respaldo pasivo e incremental offline** con un **Copiloto nativo en panel lateral (`chrome.sidePanel`)**, **Mini-CRM comercial**, métricas de atención y un **Modo Equipo en Red Local (LAN)** con semáforo anti-colisión en tiempo real.
>
> 🌐 **Repositorio Oficial:** [https://github.com/dbonotti/wackup](https://github.com/dbonotti/wackup)

---

> [!IMPORTANT]
> **REGLA DE ORO ANTI-BAN (100% Pasivo y Seguro):**  
> WAckup **NO** es un bot de envíos masivos ni una librería no oficial (no usa Baileys, Puppeteer ni WA-JS).
> - **Cero manipulación del DOM**: No inyecta botones, estilos ni paneles dentro de `web.whatsapp.com`.
> - **Cero eventos sintéticos**: Las respuestas rápidas y datos de clientes se copian con 1 clic al portapapeles del sistema (`navigator.clipboard.writeText`) para pegado humano con `Ctrl+V`.
> - **Lectura criptográfica limpia**: Captura claves AES temporales de `window.crypto.subtle` y lee pasivamente el almacenamiento local estándar `IndexedDB` (`model-storage`) del propio navegador.

---

## 🌟 Novedades y Capacidades de WAckup 3.0

1. **Paquete Portable "Doble Clic":**  
   Ya no requiere tener Python ni Git instalados en la PC. El paquete incluye un runtime Python embebido autónomo.
2. **Copiloto Nativo en Panel Lateral (`chrome.sidePanel`):**  
   Integrado limpiamente en la interfaz de Chromium (Chrome, Edge, Brave), sin tocar el diseño de WhatsApp Web. Incluye panel colapsable de ajustes rápidos en el encabezado con validación interactiva de servidor y carga dinámica de operadores oficiales.
3. **Radar de Atención Inteligente & Gestión de Pendientes:**  
   - Ventana operativa de los últimos 7 días para evitar sobrecarga de chats antiguos.
   - Descarte rápido en 1 clic y descarte fundamentado con registro en bitácora (*No requiere respuesta, Resuelto por otro canal, Personal, Spam*).
   - Control de permisos: bloqueo de descarte si el chat está asignado a otro compañero.
   - Acciones comerciales ágiles (*Posponer 3 días, Venta ganada, Venta perdida*) y notificación toast con **Deshacer (Undo)** inmediato.
4. **Resolución Automática de Contactos `@lid`:**  
   Identifica y traduce automáticamente chats con identificador `@lid` a su número telefónico real formateado, permitiendo copiar el número limpio en 1 clic para ubicarlo al instante en el buscador nativo de WhatsApp Web.
5. **Sistema de Etiquetas y Filtros Rápidos:**  
   - Filtro interactivo por clic en el Directorio de Etiquetas (`Alt + 3`) o desde el Copiloto (`Alt + 1`).
   - Búsqueda directa por `#etiqueta` o `tag:etiqueta` en la barra de búsqueda unificada.
   - Mini-pastillas visuales de etiquetas en cada conversación de la lista lateral y resultados de búsqueda.
   - Sincronización en tiempo real vía WebSockets (`tags_updated`) entre la extensión y el Visor Web.
6. **Visor Web con App Rail Lateral:**  
   Diseño responsivo (`1280x720` a `4K`) con 4 espacios de trabajo:
   - `💬 Conversaciones`: Visor histórico, reproductor multimedia con streaming HTTP Range y búsqueda indexada FTS5.
   - `🎯 Radar de Atención & Métricas`: Embudo de no respondidos, SLA de respuesta y mapa de calor de horarios pico.
   - `🏷️ CRM Comercial & Plantillas`: Tablero Kanban de ventas, notas compartidas y respuestas rápidas configurables.
   - `⚙️ Sistema y Red`: Copias de seguridad en caliente, logs en vivo, diagnóstico y configuración LAN.
7. **Modo Equipo en Red Local (LAN) y Conexión Asistida:**  
   - Múltiples compañeros en la misma oficina pueden conectarse simultáneamente al Visor y al Copiloto usando un **PIN simple de 6 dígitos**.
   - **Flujo guiado en la Extensión**: Validación de URL y PIN en 1 clic que carga la lista oficial de operadores autorizados (`known_operators`), evitando discrepancias o nombres no registrados.
   - **Gestión de concurrencia y sesiones activas**: Indicadores de operadores `(En línea)` en tiempo real y herramientas para liberar o desconectar sesiones colgadas (`⚡ Liberar`) tanto desde la extensión como desde el Visor central.
8. **Semáforo Anti-Colisión y Bitácora en Tiempo Real:**  
   Vía Server-Sent Events (`SSE`), visualiza quién está viendo cada chat para no pisarse al responder (con discriminación inteligente para no alertar sobre uno mismo en doble pantalla), y registra una bitácora cronológica inmutable por contacto.
9. **Actualizador Nativo por HTTPS:**  
   Comprueba y aplica actualizaciones desde GitHub Releases sin Git, generando antes un backup consistente con `sqlite3.Connection.backup()`.
10. **Bandeja del Sistema (System Tray):**  
    Ejecución silenciosa junto al reloj de Windows sin ventanas de consola CMD abiertas.

---

## 🚀 Puesta en Marcha (Menos de 60 Segundos)

### Opción A: Paquete Portable (Recomendado para Usuarios)
1. Descarga el archivo `WAckup_Portable_v3.0.0.zip` desde [GitHub Releases](https://github.com/dbonotti/wackup/releases).
2. Descomprímelo en cualquier carpeta de tu equipo (ej. `C:\WAckup`).
3. Haz doble clic en **`Iniciar_WAckup.vbs`** (o `iniciar_visor.bat`).
4. Se abrirá automáticamente el **Asistente de Bienvenida** en tu navegador.

### Opción B: Entorno de Desarrollo (Git / Python)
```bash
git clone https://github.com/dbonotti/wackup.git
cd wackup
pip install -r requirements.txt
python whatsapp_viewer.py --console
```

---

## 🔌 Instalación de la Extensión Chromium

1. Abre tu navegador (Google Chrome, Microsoft Edge o Brave) y accede a:
   - `chrome://extensions` (Chrome / Brave) o `edge://extensions` (Edge).
2. Activa el interruptor **"Modo de desarrollador"** (esquina superior derecha).
3. Haz clic en **"Cargar descomprimida"** y selecciona la carpeta **`extension`** del proyecto *(el Visor Web incluye un botón para copiar la ruta exacta en 1 clic)*.
4. Abre [WhatsApp Web](https://web.whatsapp.com) y haz clic en el ícono de WAckup en la barra del navegador para abrir el **Copiloto Lateral**.

---

## 🌐 Modo Equipo en Red Local (LAN)

Para compartir el CRM y el historial con otras PCs de la oficina:
1. **En la PC principal**:
   - Ingresa a **`⚙️ Sistema y Red`** (`Alt + 4`) en el Visor Web.
   - Da de alta a los integrantes del equipo en la sección **`Operadores y Equipo de Trabajo`** (`+ Nuevo Operador`).
   - Haz clic en **`Cambiar a Modo LAN`**: el servidor mostrará el **PIN de Oficina** y las direcciones IP locales disponibles (ej. `http://192.168.1.50:8080`).
2. **En las PCs de los compañeros**:
   - **Desde el Visor Web**: Ingresan a la URL de la PC principal e introducen el PIN seleccionando su nombre oficial de la lista.
   - **Desde la Extensión (Copiloto lateral)**: Abren el ícono ⚙️ en el encabezado, ingresan la URL y el PIN de oficina, y presionan **`Conectar y Cargar Operadores`**. El sistema validará la conexión y desplegará la lista oficial de operadores autorizados con su estado en vivo (`En línea` / disponible).
   - **Liberación de sesiones colgadas**: Si una sesión previa quedó abierta por error en otra máquina, tanto el administrador en el Visor como el usuario en la extensión pueden presionar **`Liberar sesión`** para reanudar el trabajo de inmediato.

---

## 📁 Estructura del Proyecto

```
wackup/
├── assets/                  # Recursos gráficos e iconos del sistema
├── data/                    # DATOS DEL USUARIO (Protegidos por .gitignore)
│   ├── <account_id>/        # Bases SQLite, multimedia, notas y metadatos
│   └── system_settings.json # Configuración de red LAN, PIN y operadores
├── extension/               # Extensión Manifest V3 para Chromium
│   ├── manifest.json        # Permisos sidePanel y storage
│   ├── crypto_sync.js       # Interceptor pasivo de WebCrypto en MAIN
│   ├── bridge.js            # Lectura pasiva de IndexedDB y envío a background
│   ├── background.js        # Service Worker central y enrutador seguro
│   └── sidepanel/           # UI nativa del Copiloto en panel lateral
├── web/                     # Frontend desacoplado del Visor Web
│   ├── index.html           # Layout moderno con App Rail vertical (58px)
│   ├── css/styles.css       # Estilos responsivos con Modo Claro y Oscuro
│   └── js/app.js            # Lógica reactiva, SSE, Kanban y CRM
├── tools/                   # Herramientas de empaquetado y release
│   └── build_portable.py    # Constructor del ZIP portable con Python embebido
├── tests/                   # Batería completa de pruebas unitarias y de integración
├── Iniciar_WAckup.vbs       # Lanzador silencioso en bandeja de Windows
├── iniciar_visor.bat        # Lanzador para diagnóstico por consola
├── tray_manager.py          # Gestor de la bandeja del sistema (System Tray)
├── sse_hub.py               # Servidor de eventos en tiempo real (SSE)
├── updater.py               # Actualizador nativo por HTTPS (GitHub Releases)
├── db_manager.py            # SQLite WAL, FTS5 y contexto colaborativo
├── whatsapp_viewer.py       # Servidor HTTP local multi-hilo
└── VERSION                  # Versión canónica del sistema (3.0.0)
```

---

## 🛡️ Seguridad y Copias de Respaldo

- **Blindaje de la carpeta `data/`:** Todos los datos personales residen exclusivamente en `data/` y nunca se sobrescriben al actualizar.
- **Backups consistentes en caliente:** Cada actualización genera automáticamente una copia de seguridad en `data/backups/` mediante la API nativa `sqlite3.Connection.backup()`.
- **Modo Local por defecto:** De fábrica, el servidor solo escucha en `127.0.0.1`. El acceso remoto solo se habilita si el usuario activa expresamente el Modo LAN con PIN numérico.

---

## 📄 Licencia

Desarrollado bajo licencia MIT. Software libre para uso personal y comercial.
