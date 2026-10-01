# DeerFlow

DeerFlow is an agent harness with a FastAPI Gateway, a Next.js chat interface,
skills, MCP tools, subagents, memory, and sandboxed execution. This repository
contains the DeerFlow 2.x application and its Python harness package.

- [Local setup on Windows](#local-setup-on-windows)
- [Local development in WSL2](#local-development-in-wsl2)
- [Development commands](#development-commands)
- [Troubleshooting](#troubleshooting)
- [Architecture and capabilities](#architecture-and-capabilities)
- [Documentation](#documentation)

## Local setup on Windows

Use **PowerShell and Docker Desktop** for the team's default local environment.
The app runs on your machine in Linux containers with backend and frontend hot
reload. Node.js, Python, nginx, and Redis run inside the containers; you do not
need to install them or GNU Make on Windows for this path.

### 1. Install prerequisites and open the repository

Install [Git for Windows](https://git-scm.com/install/windows), including Git
Bash, and the company's approved [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/).
Enable Docker's WSL2 backend and use **Linux containers**. Start Docker Desktop
before continuing. The dev stack requires Docker Compose **2.24 or newer**.

Allow roughly 25 GB of free disk space for images and dependencies. A practical
starting point is 4 CPU cores and 8 GB RAM; 8 cores and 16 GB RAM provide more
headroom for builds and concurrent agent work. Hosted model APIs do not require
a local GPU.

If you have already cloned the repository, open PowerShell in that checkout.
Otherwise:

```powershell
git clone https://github.com/bytedance/deer-flow.git
Set-Location deer-flow
```

Check the prerequisites:

```powershell
git --version
docker compose version
docker info
```

Run the remaining PowerShell commands from the **repository root**, where
`Makefile`, `backend`, and `frontend` are located.

### 2. Create and configure local files

Copy missing templates. These commands preserve existing files:

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

Configure at least one model before starting. Get the endpoint, model ID, and
credentials from your team's model platform owner. The model must support tool
calling. In `config.yaml`, replace the top-level `models:` section with your
approved provider configuration. For an OpenAI-compatible Chat Completions
endpoint, the shape is:

```yaml
models:
  - name: team-model
    display_name: Team model
    use: langchain_openai:ChatOpenAI
    model: REPLACE_WITH_TEAM_MODEL_ID
    base_url: $MODEL_BASE_URL
    api_key: $MODEL_API_KEY
```

Replace the model ID and add these variables to the **root `.env`** file:

```dotenv
MODEL_BASE_URL=https://REPLACE_WITH_TEAM_MODEL_ENDPOINT/v1
MODEL_API_KEY=REPLACE_WITH_YOUR_KEY
```

The placeholders above are not working credentials. Keep the rest of
`config.yaml`; leave the default `LocalSandboxProvider` and
`allow_host_bash: false` for the initial setup. With Docker, that provider
executes in the Gateway container and has access to its mounted files. See the
[configuration guide](backend/docs/CONFIGURATION.md#sandbox) for isolated shell
execution and other sandbox providers.

Keep `frontend/.env` at its defaults to use the same-origin proxy. Optional
search tools and integrations may need additional credentials. Other model
providers, including Azure and provider-specific APIs, use their own settings;
see [model configuration](backend/docs/CONFIGURATION.md#models).

`config.yaml`, `extensions_config.json`, and `.env` files are gitignored. Store
credentials there rather than in committed source files.

### 3. Start the app

```powershell
.\scripts\run-with-git-bash.cmd ./scripts/docker.sh start
```

This is the same launcher used by `make docker-start`. It locates Git Bash,
checks Compose compatibility, sets the checkout path, selects services for the
configured sandbox, and builds and starts the dev containers. The first build
can take several minutes. Kubernetes is only needed if you explicitly configure
the provisioner sandbox.

Open **http://localhost:2026** when services are ready. Complete the account
setup or sign-in shown by the application, then select your configured model
and send a short chat message.

Check the Gateway separately:

```powershell
Invoke-RestMethod http://localhost:2026/health
```

A successful health response confirms the Gateway is reachable. The chat check
also verifies model credentials and connectivity.

### 4. Logs, restart, and stop

```powershell
# Follow Gateway logs; Ctrl+C stops following logs.
.\scripts\run-with-git-bash.cmd ./scripts/docker.sh logs --gateway

# Apply changes to root .env, or rebuild after dependency changes.
.\scripts\run-with-git-bash.cmd ./scripts/docker.sh start

# Stop the development stack.
.\scripts\run-with-git-bash.cmd ./scripts/docker.sh stop
```

Source changes in the mounted backend and frontend directories reload
automatically. Changes to container environment variables need the start
command again so Compose can recreate the affected service. Restart the
Gateway after changing settings documented as requiring a restart.

## Local development in WSL2

Use this path to run the application services directly in Linux on a Windows
machine. Docker is optional with the default local sandbox. These commands run
in **Ubuntu inside WSL2**, except the initial Windows command.

1. If WSL2 is not installed, run the following in an administrator PowerShell
   terminal, then follow the reboot and Ubuntu account prompts. See
   [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install).

   ```powershell
   wsl --install -d Ubuntu
   ```

2. Open Ubuntu. Install Git, Make, nginx, curl, and the process inspection tool:

   ```bash
   sudo apt update
   sudo apt install -y git make nginx curl lsof python3 python3-venv
   ```

   Install **Node.js 22 or newer** using the
   [official Node.js instructions](https://nodejs.org/en/download) for Linux.
   Install uv using its [official installer](https://docs.astral.sh/uv/getting-started/installation/),
   then open a new Ubuntu terminal so its PATH changes apply:

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

   Install the repository's pinned pnpm version and Python runtime:

   ```bash
   npm install --global pnpm@10.26.2
   uv python install 3.12
   ```

3. Clone into the Linux filesystem, such as `~/src/deer-flow`. Keep this checkout
   separate from the Windows Docker checkout and its dependencies.

   ```bash
   mkdir -p ~/src
   cd ~/src
   git clone https://github.com/bytedance/deer-flow.git
   cd deer-flow
   make check
   make install
   make setup
   ```

   The setup wizard creates `config.yaml` and writes provider credentials to the
   root `.env`. Choose your approved model provider and skip optional services
   you do not need. For manual configuration, use `make config` instead of
   `make setup`, then configure the model and `.env` as described above.

4. Start and verify the app:

   ```bash
   make doctor
   make dev
   ```

   Open **http://localhost:2026** in your Windows browser. Logs are in
   `logs/gateway.log`, `logs/frontend.log`, and `logs/nginx.log`. To stop, run
   `make stop` from the same checkout in another Ubuntu terminal.

## Development commands

For Docker development, run checks inside the running containers from
PowerShell:

```powershell
docker exec -w /app/backend deer-flow-gateway uv run --no-sync pytest -m "not live" --ignore=tests/blocking_io tests/ -q
docker exec -w /app/backend deer-flow-gateway uv run --no-sync ruff check .
docker exec -w /app/backend deer-flow-gateway uv run --no-sync ruff format --check .
docker exec -w /app/frontend deer-flow-frontend pnpm check
docker exec -w /app/frontend deer-flow-frontend pnpm test
```

For WSL2 development:

| Location | Command | Purpose |
| --- | --- | --- |
| Repository root | `make dev` / `make stop` | Start / stop local services |
| Repository root | `make doctor` | Diagnose configuration and dependencies |
| Repository root | `make support-bundle` | Create redacted troubleshooting artifacts |
| `backend/` | `make test` | Run offline tests, excluding blocking I/O tests |
| `backend/` | `make test-blocking-io` | Run the separate blocking I/O suite |
| `backend/` | `make lint` / `make format` | Check / fix Python style |
| `frontend/` | `pnpm check` | Lint and type check |
| `frontend/` | `pnpm test` | Run unit tests |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development workflow and
[RELEASING.md](RELEASING.md) for version and release rules.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Docker daemon cannot be reached | Start Docker Desktop and check `docker info`. Use Linux containers. |
| `env_file ... must be a string` | Upgrade to Compose 2.24 or newer. |
| Git Bash cannot be found | Install Git for Windows with Bash included; reopen PowerShell and check that `git` is on PATH. |
| Missing config or no models | Ensure `config.yaml` is at the repository root and has an active model entry; the template alone does not configure one. |
| Model request fails | Check the model ID, endpoint, root `.env` variable names, and Gateway logs. Rerun the Docker start command after `.env` changes. |
| A model or MCP service runs on the Windows host | Containers reach the host through `host.docker.internal`; `localhost` inside the Gateway refers to that container. |
| Port 2026 is already occupied | Stop the conflicting service. For Docker, set `$env:PORT = "2027"` in PowerShell, start again, and use `http://localhost:2027`. |
| Corporate network blocks image or package downloads | Configure Docker Desktop's approved proxy and use your team's package registries; the build supports `UV_INDEX_URL` and `NPM_REGISTRY` environment variables. |
| WSL command is missing | Install the dependency inside Ubuntu; Windows installations do not replace Linux dependencies. |

Use the Windows launcher for sandbox-aware startup. Direct Compose commands
need an explicit `DEER_FLOW_ROOT` and the correct sandbox overlays; see
[CONTRIBUTING.md](CONTRIBUTING.md#option-1-docker-development-recommended).

## Architecture and capabilities

| Component | Role |
| --- | --- |
| nginx, port 2026 | Browser entry point; proxies frontend and API requests |
| Next.js frontend, port 3000 | Chat and workspace UI |
| FastAPI Gateway, port 8001 | REST APIs and embedded LangGraph agent runtime |
| Redis, Docker dev stack | Stream delivery across Gateway workers |
| Optional provisioner, port 8002 | Kubernetes sandbox lifecycle |

The harness supports skills, built-in and MCP tools, native subagents, ACP agent
processes, memory, file workspaces, streaming, and optional scheduled tasks.
Internal and external contributions follow the same integration mechanisms;
ownership and execution protocol are separate choices. See the
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
  skills, MCP tools, agents, subagents, Python tools, and extensions. Open the
  HTML file in a browser.
- [Configuration guide](backend/docs/CONFIGURATION.md): providers, tools,
  sandboxes, skills, and environment variables.
- [Runtime and integration reference](docs/runtime-reference.md): detailed
  feature behavior, tracing, memory, projects, scheduling, and integration notes.
- [Enterprise design](docs/enterprise-fit.html): proposed capability registry,
  Typesense discovery, telecom data catalog, and skill promotion workflow.
- [Backend guide](backend/AGENTS.md) and [frontend guide](frontend/AGENTS.md):
  module boundaries, conventions, and commands.
- [Changelog](CHANGELOG.md): release history.

## License

[MIT](LICENSE). Existing copyright notices are retained in the license.
