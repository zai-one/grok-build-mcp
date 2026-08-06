# Guía de instalación (español)
> ### ⛔ Primero el CLI
>
> Este MCP **no funciona** sin **Grok CLI** y `grok login` en la misma máquina/usuario. Ver [START_HERE.md](../START_HERE.md). Skill: `install-grok-mcp`.


`grok-delegate` es un **servidor MCP local por stdio**. Habla con los hosts MCP
por stdin/stdout y reutiliza la sesión **ya iniciada del Grok CLI** en la
máquina. No implementa OAuth dentro de la configuración MCP y **no debe**
recibir claves API ni `GROK_AGENT_SECRET` en archivos JSON del host.

Versión del paquete: **0.5.0**

---

## Requisitos previos

| Requisito | Notas |
|---|---|
| **Python 3.10+** | `python3 --version` o `py -3 --version` |
| **Grok CLI** | Instalado y en el `PATH` (o defina `GROK_DELEGATE_BIN`) |
| **Grok CLI con sesión** | Complete el login normal del CLI una vez en esta máquina |
| **git** | Necesario para worktrees y readback |
| **Clon de este repositorio** | Instalación desde fuente más abajo |

Verifique el CLI y la sesión (nunca pegue tokens en el chat o en la config):

```bash
grok --version
grok models    # debe funcionar cuando la sesión local del CLI es válida
```

---

## Instalación desde el código fuente

```bash
git clone <REPO_URL> <REPO_PATH>
cd <REPO_PATH>
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e .
# opcional: dependencias de prueba
pip install -e ".[test]"
```

La instalación editable expone el script de consola `grok-delegate` (entrada:
`grok_delegate.server:main`).

---

## Cómo ejecutar el servidor

El servidor es un proceso **stdio** de larga duración. Los hosts MCP lo lanzan;
también puede iniciarlo manualmente para depurar (esperará en stdin).

```bash
# Tras pip install -e .
grok-delegate

# Formas equivalentes por módulo
python -m grok_delegate.server
python -m grok_delegate
```

Comprobaciones de operador (no a través del host MCP):

```bash
python -m grok_delegate --self-test
python -m grok_delegate --smoke-delegate   # smoke plan-only en vivo opcional
python -m grok_delegate --help
```

Entorno mínimo útil (las rutas son marcadores de posición):

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_REPO_ROOT="<PROJECT_ROOT>"
export GROK_DELEGATE_LANES_PARENT="<LANES_PARENT>"
export GROK_DELEGATE_JOBS_DIR="<JOBS_DIR>"
# opcional
# export GROK_DELEGATE_BIN="grok"
# export PYTHONPATH="<REPO_PATH>"   # solo si no está instalado editable
```

Allowlist vacío → fail-closed (`ALLOWED_ROOTS_EMPTY`).

---

## Conectar Claude Desktop

Edite la configuración MCP de Claude Desktop (la ruta depende del SO; el nombre
típico es `claude_desktop_config.json`). Fusione la entrada `mcpServers` —
**sin secretos**:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "grok-delegate",
      "args": [],
      "env": {
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Si el script de consola no está en el PATH que usa Claude, invoque el
intérprete explícitamente:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Reinicie Claude Desktop tras guardar. Véase también
`examples/claude_desktop.mcp.json`.

---

## Conectar Claude Code

`.mcp.json` a nivel de proyecto en la raíz (o la ubicación documentada por
Claude Code):

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Plantilla: `examples/claude-code.mcp.json`.

---

## Conectar Codex CLI

```bash
codex mcp add grok-delegate \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- grok-delegate
```

O forma por módulo:

```bash
codex mcp add grok-delegate \
  --env "PYTHONPATH=<REPO_PATH>" \
  --env "GROK_DELEGATE_ALLOWED_ROOTS=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_REPO_ROOT=<PROJECT_ROOT>" \
  --env "GROK_DELEGATE_LANES_PARENT=<LANES_PARENT>" \
  --env "GROK_DELEGATE_JOBS_DIR=<JOBS_DIR>" \
  -- python -m grok_delegate.server
```

Verifique con: `codex mcp list`. Plantilla: `examples/codex.cli.example.sh`.

**Nunca** pase `--env GROK_AGENT_SECRET=...` a `codex mcp add`.

---

## Conectar Cursor

Configuración MCP de Cursor (usuario o proyecto `mcp.json` — siga la
documentación actual de Cursor para la ruta exacta):

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Plantilla: `examples/cursor.mcp.json`.

---

## Conectar VS Code / Continue

### VS Code (compilaciones con MCP / Copilot MCP)

Donde el producto documente un JSON de servidores MCP (ajustes de usuario o
espacio de trabajo), añada una entrada stdio con las mismas claves `env`
**no secretas**:

```json
{
  "mcp": {
    "servers": {
      "grok-delegate": {
        "type": "stdio",
        "command": "python",
        "args": ["-m", "grok_delegate.server"],
        "env": {
          "PYTHONPATH": "<REPO_PATH>",
          "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
          "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
          "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
          "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
        }
      }
    }
  }
}
```

Los nombres de campo pueden variar según la versión de VS Code: conserve
**command / args / env** y el transporte stdio; no añada URL remota ni campos
OAuth para este servidor.

### Continue

En la configuración MCP / de servidores de Continue (YAML o JSON según la
versión), registre un servidor MCP **stdio** que apunte a `grok-delegate` o
`python -m grok_delegate.server` con las mismas variables de entorno. No
configure endpoints HTTP/SSE para este paquete.

---

## ChatGPT / OpenAI

Los conectores MCP personalizados de ChatGPT están pensados para endpoints
**HTTP remotos** (o hosting similar). **Este paquete es un proceso stdio
local** y no es un servidor MCP remoto alojado por OpenAI.

Opciones:

1. **Preferido:** use `grok-delegate` solo con hosts **locales** que lancen
   servidores MCP por stdio (Claude Desktop, Claude Code, Codex CLI, Cursor,
   VS Code / Continue local).
2. **Si** lo expone mediante un **puente MCP de confianza** que usted opera
   (stdio en la máquina ↔ frontend remoto), trate ese puente como
   infraestructura de alto riesgo: nunca ponga secretos OAuth, claves API ni
   `GROK_AGENT_SECRET` en configuración remota o compartida; minimice las
   raíces permitidas; prefiera secretos WebSocket solo en memoria de proceso
   en la máquina que ejecuta Grok.

Esta guía **no** inventa pasos de UI de ChatGPT que no existan en la
superficie de este repositorio. Siga la documentación actual de OpenAI para
cualquier función de MCP remoto; es un producto aparte de este servidor local.

---

## JSON genérico de host MCP

Cualquier host que pueda lanzar un servidor MCP local por stdio:

```json
{
  "mcpServers": {
    "grok-delegate": {
      "command": "python",
      "args": ["-m", "grok_delegate.server"],
      "env": {
        "PYTHONPATH": "<REPO_PATH>",
        "GROK_DELEGATE_ALLOWED_ROOTS": "<PROJECT_ROOT>",
        "GROK_DELEGATE_REPO_ROOT": "<PROJECT_ROOT>",
        "GROK_DELEGATE_LANES_PARENT": "<LANES_PARENT>",
        "GROK_DELEGATE_JOBS_DIR": "<JOBS_DIR>"
      }
    }
  }
}
```

Varias raíces exactas: sepárelas con `;` en `GROK_DELEGATE_ALLOWED_ROOTS`. Los
descendientes de una raíz en allowlist **no** se confían de forma implícita:
cada `project_root` debe coincidir **exactamente** con una entrada.

---

## Primera verificación

### 1. Self-test del operador

```bash
cd <REPO_PATH>
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
python -m grok_delegate --self-test
```

Espere una tabla PASS/FAIL sobre binario, versión, presencia de auth, git y
rutas JSON-RPC de tools de estado. El verde completo requiere una sesión local
válida del Grok CLI.

### 2. Desde el host MCP

Cuando el host liste las tools:

1. Confirme la versión del servidor **0.5.0** (initialize / status).
2. Llame a **`grok_agent_status`** (o la compatible `grok_delegate_status`).
3. Confirme el transporte por defecto: **stdio** (`auto` → solo stdio, sin
   cascada silenciosa a WebSocket/legacy).
4. Prefiera la primera prueba de escritura solo en un repositorio git
   temporal, no en un monorepo de producción.

### 3. Pruebas unitarias (contribuidores)

```bash
pip install -e ".[test]"
pytest tests -q
```

---

## Transportes explicados

Dos capas distintas se confunden con facilidad:

| Capa | Qué es | Quién conecta |
|---|---|---|
| **MCP ↔ host** | Siempre **stdio** JSON-RPC para este paquete | Claude / Codex / Cursor / etc. lanzan el proceso |
| **Puente ↔ agente Grok** | **Transporte backend** elegido dentro del servidor | `legacy`, `stdio` (ACP) o `websocket` (ACP) |

### Transportes backend (paquete de tarea / argumento de tool)

| Valor | Función |
|---|---|
| `legacy` | Ruta headless del Grok CLI (`grok --single` / delegate legado) |
| `stdio` | ACP v1 sobre un proceso `grok agent stdio` por tarea (**predeterminado**; `auto` es alias aquí) |
| `websocket` | ACP v1 sobre WebSocket en **loopback** hacia `grok agent serve` gestionado o del operador |
| `auto` | Solo alias de `stdio` — **sin** cascada de fallback |

MCP **no** es WebSocket hacia el host. WebSocket es solo la ruta ACP opcional
hacia un agente Grok **local** en loopback. Véase `docs/ACP-TRANSPORTS.md`.

---

## Variables de entorno

Use marcadores de posición en docs y ejemplos. Prefiera rutas absolutas.

| Variable | Obligatoria | Descripción |
|---|---|---|
| `GROK_DELEGATE_ALLOWED_ROOTS` | Sí* | Allowlist de raíces exactas de proyecto (`;` o array JSON) |
| `GROK_DELEGATE_REPO_ROOT` | Sí* | Ancla de una sola raíz si no hay `ALLOWED_ROOTS` |
| `GROK_DELEGATE_LANES_PARENT` | Recomendada | Directorio padre de worktrees git externos |
| `GROK_DELEGATE_JOBS_DIR` | Recomendada | Registros durables de jobs (+ log opcional colocalizado) |
| `GROK_DELEGATE_BIN` | No | Ruta o nombre solo de `grok` / `grok.exe` |
| `GROK_DELEGATE_SANDBOX` / `GROK_SANDBOX` | No | Perfil de sandbox (`off` desactiva) |
| `GROK_DELEGATE_CONCURRENCY` | No | 1–2 (predeterminado 1) |
| `GROK_DELEGATE_MAX_QUEUED` | No | 1–32 (predeterminado 8) |
| `GROK_DELEGATE_GIT_TIMEOUT_SECONDS` | No | Timeout de sondas git (predeterminado 60) |
| `GROK_DELEGATE_GIT_CHECKOUT_TIMEOUT_SECONDS` | No | Presupuesto de `worktree add` (predeterminado 600) |
| `GROK_DELEGATE_LOG_FILE` | No | Ruta de log (nunca escriba MCP JSON-RPC en stdout) |
| `GROK_DELEGATE_LOG_LEVEL` | No | p. ej. `INFO` |
| `GROK_DELEGATE_WS_ENDPOINT` | Avanzado opcional | Solo URL WS en loopback, p. ej. `ws://127.0.0.1:<PORT>/ws` |
| `PYTHONPATH` | Si no instalado | `<REPO_PATH>` al usar `python -m` sin instalación editable |

\* Al menos uno de `GROK_DELEGATE_ALLOWED_ROOTS` o `GROK_DELEGATE_REPO_ROOT`
debe producir un allowlist no vacío.

### Secretos — solo en el proceso, nunca en archivos de config

| Variable | Regla |
|---|---|
| `GROK_AGENT_SECRET` | **Nunca** en JSON MCP, git ni ejemplos. Solo env de proceso para un demonio WS opcional del operador; el modo managed genera un secreto efímero en memoria. |
| Tokens OAuth / claves API | **Nunca** se configuran para este servidor. Use el login del Grok CLI en la máquina. |

---

## Reglas de seguridad para la configuración

1. **Nunca** ponga `GROK_AGENT_SECRET`, claves API ni tokens OAuth en archivos
   de configuración.
2. **Nunca** confirme rutas reales de home ni raíces privadas; use
   marcadores en plantillas compartidas.
3. Mantenga los directorios de lanes y jobs **fuera** del repositorio fuente
   cuando sea posible.
4. Revise los diffs del worktree antes de fusionar; este servidor nunca hace
   push ni merge.
5. Reporte vulnerabilidades mediante GitHub Security Advisories — véase
   `SECURITY.md` en la raíz.

---

## Solución de problemas

| Síntoma | Qué comprobar |
|---|---|
| `ALLOWED_ROOTS_EMPTY` / error de setup | Defina `GROK_DELEGATE_ALLOWED_ROOTS` o `GROK_DELEGATE_REPO_ROOT` |
| Raíz del cliente rechazada | Debe coincidir **exactamente** con el allowlist (no una ruta hija) |
| Auth ausente en self-test | Ejecute el login del Grok CLI con este usuario del SO; no añada tokens al config MCP |
| `GROK_MISSING` | Instale el CLI o defina `GROK_DELEGATE_BIN` con una ruta segura de `grok` |
| Tools ausentes en el host | Reinicie el host tras cambiar la config; el comando debe estar en el PATH del host |
| El servidor “se cuelga” al lanzarlo a mano | Esperado: stdio espera JSON-RPC del host |
| Fallos de WebSocket | Solo loopback; sin secretos en config; prefiera el modo managed |
| `QUEUE_FULL` | Baje la carga o suba `GROK_DELEGATE_MAX_QUEUED` dentro de los límites |
| Jobs stale / `unknown` tras reinicio | Los registros durables pueden marcar runs huérfanos; inspeccione el worktree, no asuma éxito |
| Conector remoto de ChatGPT | Este servidor es stdio local — véase la sección ChatGPT |

Logs: defina `GROK_DELEGATE_LOG_FILE` o use `<JOBS_DIR>/grok-delegate.log`
cuando el jobs dir esté configurado. No registre secretos.

---

## Documentación relacionada

- `docs/ACP-TRANSPORTS.md` — detalles ACP stdio/WebSocket  
- `docs/SECURITY.md` — aplicación de controles y riesgo residual  
- `SECURITY.md` en la raíz — reporte y política de credenciales  
- `examples/` — plantillas JSON y shell solo con marcadores


---

## Aviso legal (producto no oficial)

> **Proyecto comunitario.** **No** es un producto oficial de **xAI**, **Grok**,
> Anthropic, OpenAI ni Codex. Sin afiliación ni respaldo. La autenticación es la
> **sesión local del Grok CLI** (`grok login`). **Nunca** ponga OAuth, API keys
> ni `GROK_AGENT_SECRET` en la configuración MCP.

---

## Economía de tokens

El agente host (Claude / Cursor) orquesta con prompts cortos; el **Grok CLI**
ejecuta el bucle largo de código en la máquina o en un VPS.

| Variable | Propósito |
|---|---|
| `GROK_DELEGATE_ECONOMY=1` | Defaults más bajos de `max_turns` / timeout / reasoning |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL=1` | Payloads compactos de poll/job |

Herramienta de sesión: **`grok_agent_economy`**.

Secuencia: `status` → `economy` → `consult`/`review` → `execute` → `poll`.

Guía: [../economy.md](../economy.md) (EN).

---

## FastMCP

| Ruta | Cómo |
|---|---|
| stdio local | FastMCP / host lanza `python -m grok_delegate.server` |
| Proxy remoto | HTTP en VPS + TLS; FastMCP local `create_proxy` con bearer |

[fastmcp.md](fastmcp.md) · [../../examples/fastmcp_proxy.py](../../examples/fastmcp_proxy.py)

---

## VPS (HTTP bearer, no OAuth)

```bash
export GROK_DELEGATE_ALLOWED_ROOTS="<PROJECT_ROOT>"
export GROK_DELEGATE_HTTP_TOKEN_FILE="<TOKEN_FILE>"
python -m grok_delegate.server --transport http --host 127.0.0.1 --port 8765
```

Bearer = **secreto del operador**, nunca OAuth de Grok.  
[vps.md](vps.md) · [../../examples/vps.systemd.service](../../examples/vps.systemd.service) ·
[../../examples/http.env.example](../../examples/http.env.example)

---

## Variables de entorno de economy / HTTP

| Variable | Descripción |
|---|---|
| `GROK_DELEGATE_ECONOMY` | Activa defaults de presupuesto |
| `GROK_DELEGATE_ECONOMY_COMPACT_POLL` | Poll compacto |
| `GROK_DELEGATE_HTTP_TOKEN` | Bearer en env (excluyente con file) |
| `GROK_DELEGATE_HTTP_TOKEN_FILE` | Ruta al bearer (`<TOKEN_FILE>`) |
| `GROK_DELEGATE_HTTP_HOST` / `PORT` | Por defecto `127.0.0.1:8765` |
