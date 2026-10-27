# 💬 WAckup - WhatsApp Backup Local & Incremental

> **WAckup** (WhatsApp Backup): Sistema pasivo, privado e incremental para respaldar y visualizar conversaciones de WhatsApp Web en tu propio equipo, con soporte multi-cuenta automático, descifrado multimedia local, búsqueda indexada ultrarrápida y visor web moderno.
>
> 🌐 **Repositorio:** [https://github.com/dbonotti/wackup](https://github.com/dbonotti/wackup)

> [!IMPORTANT]
> **Seguridad y Cero Riesgo de Suspensión:**  
> A diferencia de librerías no oficiales (como Baileys o WA-JS) o bots automatizados, este sistema **no emite peticiones no autorizadas a Meta ni simula clics**. Opera de forma estrictamente pasiva leyendo el almacenamiento local estándar del navegador (`IndexedDB`) e interceptando de forma transparente las claves criptográficas temporales del cliente (`WebCrypto`).

---

## 🚀 Requisitos Previos

* **Python 3.8 o superior** (al instalar en Windows, marcar la casilla *"Add Python to PATH"*).
* **Navegador web basado en Chromium**: Google Chrome, Brave, Microsoft Edge u Opera.
* **Git** (recomendado para clonar y recibir actualizaciones en 1 clic): [https://git-scm.com](https://git-scm.com).

---

## 🛠️ Puesta en Marcha (Guía Rápida)

### Paso 0: Clonar o descargar el repositorio
```bash
git clone https://github.com/dbonotti/wackup.git
cd wackup
```
*(O descarga el archivo ZIP desde [GitHub](https://github.com/dbonotti/wackup) y descomprímelo en tu equipo).*

### Paso 1: Iniciar el servidor local
Haz doble clic en **`iniciar_visor.bat`**.
* *En el primer arranque, el lanzador instalará automáticamente las dependencias de Python necesarias (`cryptography`).*
* Se abrirá la terminal con el **Panel de Control** y tu navegador web en: [http://127.0.0.1:8080](http://127.0.0.1:8080).

### Paso 2: Cargar la extensión en el navegador
1. Abre tu navegador y dirígete a:
   * **Chrome:** `chrome://extensions/`
   * **Brave:** `brave://extensions/`
   * **Edge:** `edge://extensions/`
2. Activa el interruptor **"Modo de desarrollador"** en la esquina superior derecha.
3. Haz clic en el botón **"Cargar descomprimida"** (*Load unpacked*).
4. Selecciona la carpeta **`extension`** dentro de este proyecto *(el visor web incluye un botón "📋 Copiar ruta" para pegarla directamente)*.

### Paso 3: Abrir WhatsApp Web
1. Ingresa a [https://web.whatsapp.com](https://web.whatsapp.com) y vincula tu cuenta normalmente mediante código QR.
2. Observarás un widget en la esquina inferior izquierda: **"WAckup"**, mostrando el número o perfil de tu cuenta activa.
3. Abre cualquier chat para capturar la clave de sesión (`🎯 ¡Clave maestra verificada!`).
4. Haz clic en:
   * **`[Todo (Media)]`**: Para sincronizar el historial completo y encolar la descarga y descifrado de fotos, audios y documentos.
   * **`[Novedades]`**: Para respaldar únicamente mensajes recientes de la última hora.
   *(La extensión continuará respaldando automáticamente nuevos mensajes cada 60 segundos de forma pasiva y desatendida).*

---

## ⌨️ Panel de Control y Atajos de Teclado

La consola de `iniciar_visor.bat` funciona como un centro interactivo mientras el servidor está activo:

| Atajo | Acción | Descripción |
| :---: | :--- | :--- |
| **`[V]`** | **Abrir Visor** | Abre o enfoca el visor web en tu navegador predeterminado. |
| **`[L]`** | **Ver Logs en Vivo** | Abre una ventana independiente con el seguimiento de sincronización en tiempo real. |
| **`[U]`** | **Buscar Actualizaciones** | Comprueba si hay mejoras en GitHub y permite aplicarlas interactivamente. |
| **`Ctrl + C`** | **Detener Servidor** | Cierra de manera limpia y ordenada el servidor SQLite. |

---

## 🔄 Actualizaciones Seguras en 1 Clic

El sistema está diseñado para que actualizar nunca ponga en riesgo tu información:

* **Protección de Datos Garantizada:** Toda la información del usuario (base de datos, archivos multimedia, backups y logs) se almacena en la carpeta unificada `data/` y no forma parte de Git. Antes de descargar cualquier actualización, se genera automáticamente una copia de respaldo preventiva en `data/backups/`.
* **Portabilidad Total:** Para mover todas tus conversaciones y archivos a otro equipo o hacer una copia de seguridad externa, basta con copiar la carpeta `data/`.
* **Actualización del Servidor (`iniciar_visor.bat`):** Cada vez que inicias el visor, comprueba en ~1 segundo si hay versiones nuevas y te ofrece instalarlas.
* **Actualización Asistida de la Extensión:** Si hay una versión más reciente de la extensión en disco, el widget en WhatsApp Web mostrará el botón `[🔄 Actualizar a v...]`. Al hacer clic, se recarga la extensión en memoria y la pestaña automáticamente, sin pasos manuales en la configuración del navegador.

---

## ✨ Características Principales

* **📱 Soporte Multi-Cuenta Automático:** Detección y aislamiento nativo por número telefónico. Si cambias de cuenta o utilizas varios números de WhatsApp Web en el mismo equipo, cada cuenta tiene su propia base de datos, multimedia y respaldos en `data/<número>/`. El visor web incluye un selector para alternar entre cuentas en 1 clic.
* **🎨 Visor Web Inspirado en WhatsApp:** Modo claro y oscuro (`☀️` / `🌙`), avatares dinámicos, citas y respuestas interactivas, estados de lectura `✓✓` y paginación fluida para chats con decenas de miles de mensajes.
* **🔐 Descifrado Multimedia Local y Streaming HTTP Range:** Descarga de archivos cifrados (`.enc`) desde los servidores de WhatsApp y descifrado local con HKDF-SHA256 + AES-256-CBC. Soporta reproducción continua de video y audio con salto temporal instantáneo (HTTP 206).
* **🎙️ Reproductor de Notas de Voz:** Control de velocidad (`1x` ➔ `1.5x` ➔ `2x`) y encadenamiento automático entre audios consecutivos.
* **🔍 Búsqueda Rápida de Texto Completo (FTS5):** Motor indexado SQLite con soporte insensible a tildes y mayúsculas.
* **🏷️ Filtros Dinámicos:** Filtrado por fecha (`Desde` / `Hasta`) y por tipo de archivo (`📷 Fotos`, `🎤 Audios`, `📄 Documentos`, `🎬 Videos`).
* **📦 Exportación Completa:** Exporta cualquier conversación a formato HTML ligero o a un archivo `.html` 100% autónomo con todas las fotos y audios incrustados en Base64 para visualizarlos sin servidor.
* **💾 Copias de Seguridad Manuales:** Generación de snapshots atómicos y descarga de backups de la base de datos en formato `.db`.

---

## 📁 Estructura del Proyecto

```text
wackup/
├── data/                          # Carpeta unificada de usuario (BD, multimedia, backups, logs)
│   ├── <numero_o_cuenta_1>/       # Carpeta aislada de la primera cuenta (ej. 5491112345678)
│   │   ├── whatsapp_backup.db     # Base de datos SQLite local (WAL mode, FTS5)
│   │   ├── account.json           # Metadatos del perfil de la cuenta (nombre, teléfono)
│   │   ├── media/                 # Multimedia descifrado organizado por tipo
│   │   │   ├── images/
│   │   │   ├── stickers/
│   │   │   ├── audio/
│   │   │   ├── video/
│   │   │   └── documents/
│   │   └── backups/               # Copias de seguridad de esta cuenta
│   ├── <numero_o_cuenta_2>/       # Carpeta aislada de una segunda cuenta (si existe)
│   │   ├── whatsapp_backup.db
│   │   ├── account.json
│   │   ├── media/
│   │   └── backups/
│   └── logs/                      # Registro de sincronizaciones (sync.log)
├── extension/                     # Extensión para el navegador (Manifest V3)
│   ├── manifest.json              # Declaración de permisos y configuración de la extensión
│   ├── background.js              # Service worker puente hacia el servidor local
│   ├── bridge.js                  # Conector seguro entre contextos de página y extensión
│   └── content.js                 # Extractor pasivo incremental, multi-cuenta y widget visual
├── config.py                      # Rutas centralizadas y gestión multi-cuenta
├── whatsapp_viewer.py             # Servidor local HTTP, selector de cuentas y visor web
├── db_manager.py                  # Gestor de base de datos SQLite (WAL mode, FTS5)
├── media_manager.py               # Descifrador de multimedia (HKDF-SHA256 + AES-256-CBC)
├── iniciar_visor.bat              # Lanzador con autoinstalador, panel de control y actualizador
├── ver_logs.bat                   # Monitor en vivo de logs de sincronización
├── requirements.txt               # Dependencias mínimas de Python (cryptography)
└── README.md                      # Documentación y guía de uso
```
