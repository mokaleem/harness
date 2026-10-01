# DeerFlow

DeerFlow is an agent harness with a FastAPI Gateway, a Next.js chat interface,
skills, MCP tools, subagents, memory, and sandbox providers. This repository
contains the DeerFlow 2.x application and its Python harness package.

Local development runs directly on **Windows through PowerShell** or on
**macOS through Terminal**. Docker, Podman, WSL, and GNU Make are not required.
The Gateway, frontend, and nginx run as native processes on your workstation.

- [Local setup on Windows](#local-setup-on-windows)
- [Local setup on macOS](#local-setup-on-macos)
- [Configure your model and sandbox](#configure-your-model-and-sandbox)
- [Development commands](#development-commands)
- [Troubleshooting](#troubleshooting)
- [Architecture and capabilities](#architecture-and-capabilities)
- [Documentation](#documentation)

## Local setup on Windows

### 1. Install prerequisites and open the repository

Install through your company's approved software distribution:

- [Git for Windows](https://git-scm.com/install/windows).
- [Node.js](https://nodejs.org/en/download), version **22 or newer**.
- [Python](https://www.python.org/downloads/windows/), version **3.12**.
- [uv](https://docs.astral.sh/uv/getting-started/installation/), the Python
  dependency manager. A standalone Windows executable is available if
  installation scripts are restricted.
- [Native nginx for Windows](https://nginx.org/en/docs/windows.html). Extract
  the approved Windows ZIP and add its directory containing `nginx.exe` to
  your user PATH. Use the actual version directory, such as
  `C:\Tools\nginx-VERSION`; there is no container or Windows service to install.

Reopen PowerShell after PATH changes. Install the pinned pnpm version using
`npm.cmd`, which avoids PowerShell's script execution policy for npm shims:

```powershell
npm.cmd install --global pnpm@10.26.2
git --version
node --version
python --version
uv --version
pnpm.cmd --version
nginx.exe -v
```

If already cloned, open PowerShell in your checkout. Otherwise:

```powershell
git clone https://github.com/mokaleem/harness.git
Set-Location harness
```

Run setup commands from the **repository root**. The examples below assume
your existing checkout is `D:\poc\deer-flow`; substitute your own path.

### 2. Create and configure local files

Copy missing templates; these commands preserve existing configuration:

```powershell
if (!(Test-Path config.yaml)) {
    Copy-Item config.example.yaml config.yaml
}
if (!(Test-Path extensions_config.json)) {
    Copy-Item extensions_config.example.json extensions_config.json
}
if (!(Test-Path .env)) {
    Copy-Item .env.example .env
}
if (!(Test-Path frontend/.env)) {
    Copy-Item frontend/.env.example frontend/.env
}
```

Follow [model and sandbox configuration](#configure-your-model-and-sandbox)
before starting. The example configuration needs a working model entry.

### 3. Install dependencies

```powershell
Push-Location backend
uv sync --locked --all-packages --python 3.12
Pop-Location
python .\scripts\pnpm.py install --frozen-lockfile
```

uv creates `backend/.venv`; you do not need to activate it. The pnpm runner
uses the Windows command shim and runs in `frontend/` automatically. Check each
command succeeds before continuing. Optional integrations may need additional
extras; see [development commands](#development-commands).

### 4. Start the native services

Use three PowerShell terminals so logs remain visible and each service can be
restarted independently. Start the Gateway and frontend before nginx.

**Terminal 1 — Gateway:**

```powershell
Set-Location D:\poc\deer-flow
$env:DEER_FLOW_PROJECT_ROOT = (Get-Location).Path
$env:DEER_FLOW_HOME = Join-Path $env:DEER_FLOW_PROJECT_ROOT "backend/.deer-flow"
$env:GATEWAY_WORKERS = "1"
New-Item -ItemType Directory -Force -Path $env:DEER_FLOW_HOME | Out-Null
Set-Location backend
uv run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 --env-file ../.env
```

`--env-file` loads the root `.env`, including your model credentials. This
command runs one Gateway process. Restart it after backend code, dependencies,
configuration, or credential changes.

**Terminal 2 — frontend:**

```powershell
Set-Location D:\poc\deer-flow
$env:PORT = "3000"
$env:DEER_FLOW_INTERNAL_GATEWAY_BASE_URL = "http://127.0.0.1:8001"
$env:DEER_FLOW_TRUSTED_ORIGINS = "http://localhost:2026,http://localhost:3000"
python .\scripts\pnpm.py run dev -- --hostname 127.0.0.1
```

The frontend uses hot reload. Wait for its ready message.

**Terminal 3 — nginx and verification:**

```powershell
Set-Location D:\poc\deer-flow
New-Item -ItemType Directory -Force -Path logs,temp/client_body_temp,temp/proxy_temp,temp/fastcgi_temp,temp/uwsgi_temp,temp/scgi_temp | Out-Null
$nginxPrefix = (Get-Location).Path.Replace('\', '/') + '/'
nginx.exe -t -p $nginxPrefix -c docker/nginx/nginx.local.conf
```

Only after nginx reports that its configuration test succeeded:

```powershell
nginx.exe -p $nginxPrefix -c docker/nginx/nginx.local.conf
Invoke-RestMethod http://localhost:2026/health
Invoke-RestMethod http://localhost:2026/health/ready
```

nginx runs in the background on Windows. The prefix makes its logs and PID file
belong to this checkout, and uses the forward-slash paths required by nginx.
The file under `docker/nginx/` is also the existing **native local** proxy
configuration; using it does not run Docker.

Open **http://localhost:2026**, complete account setup or sign-in, select your
configured model, and send a short chat message. `/health` checks reachability;
`/health/ready` checks runtime readiness. The chat also checks model connectivity.

### 5. Stop and restart

Press **Ctrl+C** in the Gateway and frontend terminals. From the third terminal,
stop nginx gracefully using the same prefix:

```powershell
nginx.exe -p $nginxPrefix -c docker/nginx/nginx.local.conf -s quit
```

To start again, repeat the three service commands. To follow proxy errors:

```powershell
Get-Content .\logs\nginx-error.log -Tail 50 -Wait
```

## Local setup on macOS

### 1. Install prerequisites and open the repository

Use company-approved native installations of Git, Node.js **22 or newer**,
Python **3.12**, uv, pnpm **10.26.2**, and nginx. Download
[Node.js](https://nodejs.org/en/download),
[Python for macOS](https://www.python.org/downloads/macos/), and
[Git](https://git-scm.com/install/mac) through your approved distribution.
With an approved Homebrew installation,
[uv](https://docs.astral.sh/uv/getting-started/installation/) and
[nginx](https://formulae.brew.sh/formula/nginx) can be installed with:

```bash
brew install uv nginx
npm install --global pnpm@10.26.2
git --version
node --version
python3 --version
uv --version
pnpm --version
nginx -v
```

Install Node.js and Python before these commands. Do not start Homebrew's
global nginx service; the app uses its own configuration and process.

If already cloned, open Terminal in that checkout. Otherwise:

```bash
git clone https://github.com/mokaleem/harness.git
cd harness
```

### 2. Configure and install dependencies

From the repository root, copy missing templates:

```bash
test -e config.yaml || cp config.example.yaml config.yaml
test -e extensions_config.json || cp extensions_config.example.json extensions_config.json
test -e .env || cp .env.example .env
test -e frontend/.env || cp frontend/.env.example frontend/.env
```

Follow [model and sandbox configuration](#configure-your-model-and-sandbox),
then install dependencies:

```bash
cd backend
uv sync --locked --all-packages --python 3.12
cd ..
python3 scripts/pnpm.py install --frozen-lockfile
```

### 3. Start and verify the native services

Open three terminals in the **same repository root**.

**Terminal 1 — Gateway:**

```bash
export DEER_FLOW_PROJECT_ROOT="$PWD"
export DEER_FLOW_HOME="$PWD/backend/.deer-flow"
export GATEWAY_WORKERS=1
mkdir -p "$DEER_FLOW_HOME"
cd backend
uv run --no-sync uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001 --env-file ../.env
```

**Terminal 2 — frontend:**

```bash
export PORT=3000
export DEER_FLOW_INTERNAL_GATEWAY_BASE_URL=http://127.0.0.1:8001
export DEER_FLOW_TRUSTED_ORIGINS=http://localhost:2026,http://localhost:3000
python3 scripts/pnpm.py run dev -- --hostname 127.0.0.1
```

**Terminal 3 — nginx:**

```bash
mkdir -p logs temp/client_body_temp temp/proxy_temp temp/fastcgi_temp temp/uwsgi_temp temp/scgi_temp
nginx -t -p "$PWD/" -c docker/nginx/nginx.local.conf
```

After its configuration test succeeds, keep nginx running in this terminal:

```bash
nginx -p "$PWD/" -c docker/nginx/nginx.local.conf -g 'daemon off;'
```

Open **http://localhost:2026**. In another terminal, verify:

```bash
curl --fail http://localhost:2026/health
curl --fail http://localhost:2026/health/ready
```

Sign in and send a short chat to verify the configured model. Stop all three
foreground processes with **Ctrl+C** in their respective terminals. Restart
the Gateway after backend or root `.env` changes.

## Configure your model and sandbox

Get the endpoint, model ID, and credentials from your team's model platform
owner. The model must support tool calling. In `config.yaml`, replace the
top-level `models:` section with your approved provider configuration. For an
OpenAI-compatible Chat Completions endpoint:

```yaml
models:
  - name: team-model
    display_name: Team model
    use: langchain_openai:ChatOpenAI
    model: REPLACE_WITH_TEAM_MODEL_ID
    base_url: $MODEL_BASE_URL
    api_key: $MODEL_API_KEY
```

Replace the model ID and add these variables to the **root `.env`**:

```dotenv
MODEL_BASE_URL=https://REPLACE_WITH_TEAM_MODEL_ENDPOINT/v1
MODEL_API_KEY=REPLACE_WITH_YOUR_KEY
```

These placeholders are not working credentials. Azure and other providers
have their own settings; see [model configuration](backend/docs/CONFIGURATION.md#models).

Keep the rest of `config.yaml`, including its local SQLite database and
in-process stream defaults. This single-process setup needs no Redis or
PostgreSQL server. Leave `NEXT_PUBLIC_BACKEND_BASE_URL` and
`NEXT_PUBLIC_LANGGRAPH_BASE_URL` unset in `frontend/.env` to use nginx's
same-origin routes. Enable optional search tools only with their required
credentials.

Use the native local sandbox configuration:

```yaml
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: false
```

Keep the template's other sandbox settings. This provider handles workspace
files on the host; it is **not a security isolation boundary**. With
`allow_host_bash: false`, tools and skills that require shell execution cannot
run. For a trusted single-user environment, your team can explicitly enable
`allow_host_bash: true` if workstation policy permits agent command execution.
Commands then run with your account's privileges; native Windows can use its
PowerShell/cmd fallback. Skills written for Unix shell commands may still need
Windows-compatible scripts. For isolated execution without a local container
runtime, configure a company-approved remote sandbox provider; see
[sandbox configuration](backend/docs/CONFIGURATION.md#sandbox).

`config.yaml`, `extensions_config.json`, and `.env` files are gitignored.
Keep credentials in those files or the approved secret system.

## Development commands

Run backend commands from `backend/`; frontend commands below use the runner
from the repository root (`python3` instead of `python` on macOS):

| Location | Command | Purpose |
| --- | --- | --- |
| `backend/` | `uv run --no-sync pytest -m "not live" --ignore=tests/blocking_io tests/ -q` | Offline backend tests |
| `backend/` | `uv run --no-sync pytest tests/blocking_io -q` | Blocking I/O tests |
| `backend/` | `uv run --no-sync ruff check .` | Check Python style |
| `backend/` | `uv run --no-sync ruff format .` | Format Python |
| Repository root | `python scripts/pnpm.py check` | Frontend lint and type check |
| Repository root | `python scripts/pnpm.py test` | Frontend unit tests |

If enabling optional browser, PostgreSQL, or other integration features, add
the corresponding extras when syncing, for example
`uv sync --locked --all-packages --python 3.12 --extra browser` from `backend/`.
Keep all required extras in subsequent sync commands. Browser automation also
needs Chromium installed with `uv run --no-sync playwright install chromium`
from `backend/`; see the [configuration guide](backend/docs/CONFIGURATION.md).
Restart affected services after dependency changes.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the wider development workflow and
[RELEASING.md](RELEASING.md) for version and release rules.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `npm.ps1` or `pnpm.ps1` is blocked | Use `npm.cmd` / `pnpm.cmd`, or the documented Python pnpm runner. No execution-policy change is needed. |
| Command not found | Reopen your terminal after installing; verify Python, Node, uv, pnpm, and nginx are on PATH. |
| Python launches the Microsoft Store | Install approved Python 3.12 and fix PATH/app execution aliases so `python --version` runs it. |
| Missing config or no models | Ensure root `config.yaml` has a working model entry and the root `.env` has matching credentials. |
| Model request fails | Check the model ID, endpoint, variable names, and Gateway terminal logs; restart the Gateway after root `.env` edits. |
| Package downloads fail on the corporate network | Use approved registries, proxy settings, and the enterprise CA. Set `UV_INDEX_URL` for uv and configure npm's registry; do not disable TLS verification. |
| nginx configuration test fails | Check port availability, PATH, writable `logs/` and `temp/`, and the checkout prefix. If IPv6 is disabled, adjust the `[::]:2026` listener in your local proxy configuration. |
| Browser shows 502 | Ensure the Gateway is listening on 8001 and the frontend on 3000; check their terminals and `logs/nginx-error.log`. |
| Port 2026 is occupied | Stop the conflicting process. Changing root `.env` `PORT` does not change native nginx; its listener is in `docker/nginx/nginx.local.conf`. |
| A skill cannot execute a command | Check `sandbox.allow_host_bash`, workstation policy, required CLI dependencies, and whether its scripts support your operating system. |

## Architecture and capabilities

| Component | Role |
| --- | --- |
| Native nginx, port 2026 | Browser entry point; proxies frontend, API, SSE, and WebSocket requests |
| Next.js frontend, port 3000 | Chat and workspace UI |
| FastAPI Gateway, port 8001 | REST APIs and embedded LangGraph agent runtime |
| Local SQLite and files | Developer runtime state; no external database needed |
| Optional remote sandbox | Isolated execution when configured |

The harness supports skills, built-in and MCP tools, native subagents, ACP
agent processes, memory, file workspaces, streaming, and optional scheduled
tasks. Internal and external contributions follow the same integration
mechanisms; ownership and execution protocol are separate choices. See the
[capability inventory](docs/capabilities.html) for support boundaries.

The Python harness lives in `backend/packages/harness`; the Gateway lives in
`backend/app`; the public extension contract lives in
`backend/packages/extension-api`. Applications can embed `DeerFlowClient`; see
[the client reference](docs/runtime-reference.md#embedded-python-client).

These instructions set up a developer environment. Shared deployments need
appropriate authentication, authorization, and sandbox configuration; see
[SECURITY.md](SECURITY.md) and [SSO setup](backend/docs/SSO.md).

## Documentation

- [Offline extension handbook](docs/index.html): adding internal and external
  skills, MCP tools, agents, subagents, Python tools, and extensions.
- [Configuration guide](backend/docs/CONFIGURATION.md): providers, tools,
  sandboxes, skills, and environment variables.
- [Runtime and integration reference](docs/runtime-reference.md): detailed
  runtime behavior and optional deployment/integration notes.
- [Enterprise design](docs/enterprise-fit.html): proposed capability registry,
  Typesense discovery, telecom data catalog, and skill promotion workflow.
- [Backend guide](backend/AGENTS.md) and [frontend guide](frontend/AGENTS.md):
  module boundaries, conventions, and commands.
- [Changelog](CHANGELOG.md): release history.

## License

[MIT](LICENSE). Existing copyright notices are retained in [LICENSE](LICENSE).
