# Runbook — 节七 云端受限真实部署验证 + 节八 真实 Provider 预检

> **适用对象**：`yzhlx/hermes-open-swe-lab` 的云端控制面服务器（Ubuntu 24.04 LTS，2 CPU / 4 GB / 60 GB）。
> **本文档仅生成，不自动执行。** 所有命令由运维人员在云端服务器上**逐条复制执行**。
> **目标仓库唯一**：`yzhlx/hermes-open-swe-lab`。**严禁** clone / 读取 / 修改 `yzhlx/hermes-learning-os`（受保护仓库，见 AGENTS.md §4）。

---

## 0. 硬约束与前置声明（每次执行前先读）

| # | 约束 | 来源 |
|---|------|------|
| 1 | 目标仅限 `yzhlx/hermes-open-swe-lab` | 用户指令 / AGENTS.md §4 |
| 2 | 严禁访问或修改 `yzhlx/hermes-learning-os` | AGENTS.md §4 |
| 3 | 不启用公网 Webhook | 用户指令 / AGENTS.md §9 |
| 4 | 不启用/修改公网 Nginx、DNS、TLS | 用户指令 |
| 5 | 控制面仅绑定 `127.0.0.1` | AGENTS.md §9 / `control_plane_app.guard_startup` |
| 6 | 用 `hermes-swe` 服务用户跑应用，**不**以 root 运行应用 | `systemd` Unit `User=hermes-swe` / `check_config.sh` |
| 7 | `/opt/hermes-open-swe-lab/.env` 必须 `hermes-swe:hermes-swe`，权限 `600` | 用户指令 / `deploy_control_plane.sh` |
| 8 | 日志/验证结果必须脱敏，禁止输出 API Key / Worker Token / PEM | AGENTS.md §7 / `redact.py` |
| 9 | Provider 凭证缺失 → 输出 `PROVIDER_LIVE_BLOCKED_BY_CREDENTIALS`，**不得伪造 PASS** | 用户指令 / `provider_preflight.py` |
| 10 | 每步提供：命令 / 预期输出 / PASS-FAIL / 停止条件 / 回滚 | 用户指令 |
| 11 | 顺序：只读预检 → 备份 SQLite → 部署验证 | 用户指令 |
| 12 | 验证后恢复 Webhook OFF、不开放公网端口 | 用户指令 |
| 13 | 不创建新功能、不修改 D3 业务代码 | 用户指令 |
| 14 | 不合并任何 PR | AGENTS.md §6 |
| 15 | 末附节七/节八验收结果模板 | 用户指令 |

**执行者身份**：具有 `sudo` 的运维账号（操作员）。**应用进程**一律以 `hermes-swe` 运行，操作员本人不得用 root 直接起应用（`guard_startup` 会拒绝，exit 2）。
**代码来源**：从 `origin` 检出已知**运行时代码提交** `932dcd7ef9d584955d316a3ddfca25c69f7dd6e3`（变量 `RUNTIME_CODE_SHA`，detached HEAD；这是最后一个运行时代码提交，不随本仓库的文档提交变化），**不 merge 任何 PR**。
**全局变量**（在云端 shell 中 export 一次）：

```bash
export APP_HOME=/opt/hermes-open-swe-lab
export SRC=/opt/src/hermes-open-swe-lab
export SERVICE_USER=hermes-swe
export RUNTIME_CODE_SHA=932dcd7ef9d584955d316a3ddfca25c69f7dd6e3   # 最后一个运行时代码提交（完整 40 位）；执行前强制相等判断（注：PR #5 的 HEAD 可能含其后的文档提交，与 RUNTIME_CODE_SHA 不同属正常，不以 PR HEAD 作为相等判据）
```

---

## Phase A — 只读环境预检（不改任何状态）

> ⚠️ 本阶段**只读取、不写入、不启动**。任何一步 FAIL → 停止，执行该步"回滚命令"（只读步骤通常=无需回滚），报告 `STATUS: ENV_PREFLIGHT_FAILED`，不进入 Phase B/C。

### A1. 确认代码来源与提交 SHA（不是受保护仓库 + SHA 必须相等）
- **执行命令**：
  ```bash
  git -C "$SRC" remote -v
  actual=$(git -C "$SRC" rev-parse HEAD)
  echo "actual_HEAD=$actual"
  echo "expect_HEAD=$RUNTIME_CODE_SHA"
  # 自动相等判断：不一致立即失败停止
  if [ "$actual" != "$RUNTIME_CODE_SHA" ]; then
    echo "STATUS: SHA_MISMATCH actual=$actual expect=$RUNTIME_CODE_SHA"
    exit 1
  fi
  echo "SHA_MATCH=OK"
  ```
- **预期输出**：`origin` 指向 `github.com/yzhlx/hermes-open-swe-lab.git`；`SHA_MATCH=OK`。
- **PASS 判据**：remote URL 仅含 `hermes-open-swe-lab`，**不含** `hermes-learning-os`；且 `actual == $RUNTIME_CODE_SHA`（脚本已 `exit 1` 强制拦截不一致）。
- **失败停止条件**：
  - 出现 `hermes-learning-os` → **立即停止，禁止自动删除**；执行安全隔离（见回滚命令），绝不继续。
  - `SHA_MISMATCH` → 立即停止，复核 `RUNTIME_CODE_SHA` 或重新 `git fetch`。
- **回滚命令（安全隔离，非删除）**：发现错误仓库时**不执行 `rm -rf`**，改为隔离等待人工确认：
  ```bash
  sudo mkdir -p /opt/quarantine
  sudo mv "$SRC" "/opt/quarantine/hermes-checkout-$(date -u +%Y%m%dT%H%M%SZ)" \
    && echo "WRONG_REPO_QUARANTINED: 已隔离至 /opt/quarantine，等待人工确认后处理"
  ```

### A2. 操作系统 / 内核 / 资源
- **执行命令**：
  ```bash
  uname -a; . /etc/os-release 2>/dev/null && echo "OS=$PRETTY_NAME"
  nproc; free -h | awk '/Mem:/ {print "RAM="$2}'
  ```
- **预期输出**：`Linux ... 6.x`; `OS=Ubuntu 24.04 LTS`; `nproc` ≥ 2; 内存 ≥ 4GB。
- **PASS 判据**：Ubuntu 24.04、CPU ≥ 2、RAM ≥ 4GB。
- **失败停止条件**：非 Ubuntu 24.04 或资源不足 → 停止，报告环境不满足。
- **回滚命令**：无需回滚（只读）。

### A3. `hermes-swe` 服务用户现状（仅报告，不在本阶段创建）
- **执行命令**：
  ```bash
  id "$SERVICE_USER" 2>&1 || echo "USER_ABSENT"
  ```
- **预期输出**：存在则打印 uid/gid；不存在则 `USER_ABSENT`（Phase C 的 deploy 会创建）。
- **PASS 判据**：存在或 `USER_ABSENT` 均可（Phase C 会补齐）；但若为 root（uid 0）属异常。
- **失败停止条件**：`$SERVICE_USER` 解析为 uid 0 → 停止（配置错误）。
- **回滚命令**：无需回滚（只读）。

### A4. 安装目录与磁盘（APP_HOME 不存在时检查 /opt）
- **执行命令**：
  ```bash
  ls -ld "$APP_HOME" 2>&1 || echo "APP_HOME_ABSENT"
  # APP_HOME 可能尚未创建（首次部署）：不存在则检查其父 /opt
  if [ -d "$APP_HOME" ]; then
    df -h "$APP_HOME" | tail -1
  else
    echo "APP_HOME_ABSENT -> check /opt"
    df -h /opt | tail -1
  fi
  ```
- **预期输出**：目录存在或 `APP_HOME_ABSENT`；磁盘可用 ≥ 2GB（取 `$APP_HOME` 或 `/opt`）。
- **PASS 判据**：目录可写（或不存在待创建）；剩余空间 ≥ 2GB。
- **失败停止条件**：磁盘剩余 < 2GB → 停止。
- **回滚命令**：无需回滚（只读）。

### A5. 端口与 Hermes 服务占用检查（仅阻塞条件：8080 被占用 或 Hermes 异常 active）
- **执行命令**：
  ```bash
  # 仅检查 Hermes 目标端口 8080 是否被占用（任何监听者都算冲突，需先解决）
  sudo ss -tlnp 2>/dev/null | grep -E ':8080\b' || echo "PORT_8080_FREE"
  # 仅检查 Hermes 服务是否处于非预期 active（异常运行）
  systemctl is-active hermes-swe-control-plane.service 2>/dev/null || echo "HERMES_SERVICE_INACTIVE"
  # 既有 80/443/Nginx 公网服务仅记录，不做阻塞判据（其前后快照见 A5b）
  sudo ss -tlnp 2>/dev/null | grep -E ':80 |:443 ' || echo "NO_PUBLIC_PORTS_RECORDED"
  systemctl is-active nginx 2>/dev/null || echo "NGINX_STATE_RECORDED"
  ```
- **预期输出**：`PORT_8080_FREE`；`HERMES_SERVICE_INACTIVE`（或 `unknown`）；既有 80/443/nginx 状态仅记录（可能为 active，正常）。
- **PASS 判据**：8080 空闲（无 `:8080` 监听）；Hermes 服务非 active。
- **失败停止条件（仅以下两项阻塞部署）**：
  - `:8080` 已被占用 → 停止，先排查并释放占用进程（不得 kill 无关 PID）；
  - Hermes 服务处于 `active`（异常运行）→ 停止，先 `sudo systemctl stop hermes-swe-control-plane.service` 再评估。
  - **既有 80/443/Nginx 公网服务仅记录、不阻塞、不停止**：其存在不是预检失败条件（E2 差异比对会确认 Hermes 未新增公网监听）。
- **回滚命令**：无需回滚（只读）；若误启动，见 Phase F。

### A5b. 部署前快照既有端口/Nginx 状态（用于部署后差异比对）
- **执行命令**：
  ```bash
  sudo ss -tlnp 2>/dev/null | grep -E ':80 |:443 |:8080' > /tmp/before_ports.txt || true
  systemctl is-active nginx 2>/dev/null > /tmp/before_nginx.txt || true
  echo "snapshot saved: before_ports.txt / before_nginx.txt"
  cat /tmp/before_ports.txt
  ```
- **预期输出**：保存部署前 80/443/8080 监听清单与 nginx 活跃状态（可能为空）。
- **PASS 判据**：快照成功保存（只读，不影响运行态）。
- **失败停止条件**：无需（只读）。
- **回滚命令**：无需回滚（只读）；快照文件可随时 `rm -f /tmp/before_*.txt`。

### A6. `.env` 现状（凭证是否存在，但不读取内容）
- **执行命令**：
  ```bash
  ls -l "$APP_HOME/.env" 2>&1 || echo "ENV_ABSENT"
  ```
- **预期输出**：存在则显示权限/属主（如 `-rw------- hermes-swe hermes-swe`）；否则 `ENV_ABSENT`。
- **PASS 判据**：存在=权限 `600` 且 `hermes-swe:hermes-swe`；不存在=记录"凭证缺失→节八将 BLOCKED"。**绝不 cat/.env**。
- **失败停止条件**：`.env` 存在但权限 > 600 或属主非 hermes-swe → 在 Phase C 修正，本阶段仅记录。
- **回滚命令**：无需回滚（只读）；**禁止打印 .env 内容**。

---

## Phase B — 备份 SQLite（不改运行态，仅做快照）

> 目的：在部署前对**已有**事件库做完整性快照。若库不存在则跳过。

### B1. 备份既有事件库（首次部署：库/venv/用户可能均不存在）
- **说明**：备份脚本取自部署源 `$SRC`（部署前即存在），不依赖 `$APP_HOME` 是否已 populated；
  `events.db` 不存在时脚本打印 `BACKUP SKIP` 并 exit 0；真正失败（完整性错误）exit 1。
  **严禁用 `|| echo` 吞掉错误退出码。**
- **执行命令**：
  ```bash
  # 选 python：venv 可能尚未创建，默认回退系统 python3
  PYBIN=python3
  [ -x "$APP_HOME/venv/bin/python" ] && PYBIN="$APP_HOME/venv/bin/python"
  # 选运行身份：hermes-swe 可能尚未创建（首次部署），不存在则本次以操作员运行（仅做快照，非长驻应用）
  RUN_AS="sudo -u $SERVICE_USER"
  id "$SERVICE_USER" >/dev/null 2>&1 || RUN_AS=""
  $RUN_AS "$PYBIN" "$SRC/scripts/backup_sqlite.py" \
    --src "$APP_HOME/runtime/events.db" \
    --dst-dir "$APP_HOME/backups"
  rc=$?
  echo "BACKUP_EXIT=$rc"
  ```
- **预期输出**：
  - 库存在 → `BACKUP OK /opt/hermes-open-swe-lab/backups/events-<UTC>.db`，`BACKUP_EXIT=0`；
  - 库不存在（首次）→ `BACKUP SKIP: source db not found`，`BACKUP_EXIT=0`；
  - 真正失败 → `BACKUP INTEGRITY FAILURE: ...`，`BACKUP_EXIT=1`。
- **PASS 判据**：`BACKUP_EXIT=0`（OK 或 SKIP 均通过）。
- **失败停止条件**：`BACKUP_EXIT=1`（完整性失败）→ 停止，保留非零退出码，报告 `STATUS: SQLITE_BACKUP_FAILED`，**不进入 Phase C**。
- **回滚命令**：备份本身是前滚保护；如需撤销本次备份：`rm -f "$APP_HOME/backups/events-<UTC>.db"`（仅当确认未用于恢复）。

---

## Phase C — 部署验证（受限、仅 loopback、Webhook OFF）

> 本阶段真正写盘并启动服务，但**仅绑定 127.0.0.1、不启公网、不启 Webhook、不触碰既有服务**。任意硬门 FAIL → 停服务 → 回滚。

### C1. 运行幂等部署脚本（先跑，HERMES_AUTOSTART=0，由脚本创建 hermes-swe）[纠正旧版顺序]
> ⚠️ **正确首次部署顺序**：先部署（本步）→ 再放置 .env（C2）→ 再校验 .env 权限（C3）→ 最后启动（C4）。
> 部署脚本会幂等创建 `hermes-swe` 系统用户、拷贝源码、运行 `check_config.sh`，随后将服务置为 DISABLED。
> **`check_config` 不依赖 Provider `.env`**：它仅校验 `HERMES_*` 非密配置与 loopback 绑定（见 `scripts/check_config.sh`），不读取 `OPEN_SWE_OPENAI_*`，因此部署可在 .env 放置之前安全执行。
- **执行命令**：
  ```bash
  export HERMES_APP_HOME="$APP_HOME"
  export HERMES_DEPLOY_SRC="$SRC"
  export HERMES_SERVICE_USER="$SERVICE_USER"
  export HERMES_AUTOSTART=0          # 关键：不自动启动
  sudo -E bash "$SRC/scripts/deploy_control_plane.sh"
  ```
- **预期输出**：`[deploy] creating service user hermes-swe` → `[deploy] copying source ...` → `check_config: OK` → `service left DISABLED (default)` → `deploy complete -> /opt/hermes-open-swe-lab`。
- **PASS 判据**：`hermes-swe` 用户已存在（或已存在）；`check_config: OK`；`service left DISABLED`；脚本 exit 0。
- **失败停止条件**：`check_config` 失败（fail-closed）或脚本非零退出 → 停止，查看 stderr，进入 Phase F。
- **回滚命令**：`sudo bash "$SRC/scripts/rollback_control_plane.sh"`（见 Phase F）。

### C2. 放置 / 修正 .env（部署后，运维经安全通道写入）[不打印真实值]
> ⚠️ **绝不**在 Runbook / 聊天 / 日志中写入真实密钥，只用占位符。`.env` 在部署完成后、服务启动前，由运维通过安全通道写入 `$APP_HOME/.env`。
- **执行命令**（运维在服务器本地执行，内容用占位符示意）：
  ```bash
  # 真实值经安全通道写入，禁止打印：
  #   OPEN_SWE_OPENAI_BASE_URL=<RELWAY_BASE_URL>
  #   OPEN_SWE_OPENAI_API_KEY=<RELWAY_API_KEY>
  #   OPEN_SWE_OPENAI_MODEL=<RELWAY_MODEL>
  # 写入后仅确认存在，绝不 cat
  if [ -f "$APP_HOME/.env" ]; then
    echo "ENV_PRESENT"
  else
    echo "ENV_ABSENT: 未完成 .env 写入（节八将 BLOCKED）"
  fi
  ```
- **预期输出**：`ENV_PRESENT`（运维已写入）；或 `ENV_ABSENT`（跳过，节八将 BLOCKED）。
- **PASS 判据**：`.env` 存在（内容经安全通道）；不存在=记录"凭证缺失→节八将 BLOCKED"。
- **失败停止条件**：无（本步仅放置；权限校验在 C3）。
- **回滚命令**：无需（仅写入；若误写，用安全通道重写，**禁止**在会话中回显）。

### C3. 校验并修正 .env 权限与属主（部署后、启动前）[纠正旧版顺序]
> ⚠️ 此步现在在**部署之后、启动之前**（旧版放在部署前是错误的）。确保 `.env` 为 `600`、`hermes-swe:hermes-swe`。
- **执行命令**：
  ```bash
  if [ -f "$APP_HOME/.env" ]; then
    sudo chown "$SERVICE_USER":"$SERVICE_USER" "$APP_HOME/.env"
    sudo chmod 600 "$APP_HOME/.env"
    stat -c '%a %U:%G' "$APP_HOME/.env"   # 仅看权限/属主，禁止 cat
  else
    echo "ENV_ABSENT: 跳过 .env 修复（节八将 BLOCKED）"
  fi
  ```
- **预期输出**：`-rw------- 1 hermes-swe hermes-swe ... .env`（存在时）；或 `ENV_ABSENT`（跳过）。
- **PASS 判据**：存在=权限 `600` 且 `hermes-swe:hermes-swe`；不存在=记录"凭证缺失→节八将 BLOCKED"。
- **失败停止条件**：存在但权限 ≠ 600 或属主错误且无法修正 → 停止。
- **回滚命令**：无需回滚（仅修正权限）；若误写错误内容，用安全通道重写，**禁止**在会话中回显。

### C4. 启动控制面（以 hermes-swe 运行，systemd 保证非 root）
- **执行命令**：
  ```bash
  sudo systemctl daemon-reload
  sudo systemctl start hermes-swe-control-plane.service
  sleep 2
  systemctl is-active hermes-swe-control-plane.service
  ```
- **预期输出**：`active`。
- **PASS 判据**：`active`；且进程有效用户为 `hermes-swe`（见 C6）。
- **失败停止条件**：`failed` 或启动即退（guard_startup 拒 root/非loopback）→ `journalctl -u hermes-swe-control-plane -n 50` 排查，停止并回滚。
- **回滚命令**：`sudo systemctl stop hermes-swe-control-plane.service`（见 Phase F）。

### C5. 验证健康检查（仅 loopback）
- **执行命令**：
  ```bash
  curl -s -o /tmp/h.json -w "HTTP %{http_code}\n" http://127.0.0.1:8080/healthz && cat /tmp/h.json; echo
  curl -s -o /tmp/r.json -w "HTTP %{http_code}\n" http://127.0.0.1:8080/readyz  && cat /tmp/r.json; echo
  ```
- **预期输出**：`HTTP 200` + `{"status":"ok","service":"hermes-swe-control-plane"}`；`HTTP 200` + `{"ready":true}`。
- **PASS 判据**：两个端点均 HTTP 200 且 body 如上（无 secret 字段）。
- **失败停止条件**：非 200 或超时 → 停止，查 `journalctl`，进入 Phase F。
- **回滚命令**：`sudo systemctl stop hermes-swe-control-plane.service`。

### C6. 验证进程用户非 root + 仅绑定 127.0.0.1（Hermes 新增监听）
- **执行命令**：
  ```bash
  pid=$(pgrep -f "deploy.cloud.control_plane_app" | head -1); echo "PID=$pid"
  [ -n "$pid" ] && ps -o user= -p "$pid"
  sudo ss -tlnp 2>/dev/null | grep ':8080' || echo "NO_8080_LISTEN"
  ```
- **预期输出**：`PID=...`；`user` 列为 `hermes-swe`（**不是** root）；监听行显示 `127.0.0.1:8080`（**不是** `0.0.0.0` 或公网 IP）。
- **PASS 判据**：用户 ≠ root；绑定 == `127.0.0.1:8080`（仅 Hermes 本次新增的 loopback 监听）。
- **失败停止条件**：用户为 root 或绑定 `0.0.0.0`/公网 → 立即停止（安全违规），进入 Phase F。
- **回滚命令**：`sudo systemctl stop hermes-swe-control-plane.service`。

### C7. 复核 .env 权限（若 C3 已放置 .env）
- **执行命令**：`stat -c '%a %U:%G' "$APP_HOME/.env" 2>/dev/null || echo "ENV_ABSENT"`
- **预期输出**：`600 hermes-swe:hermes-swe`（或 `ENV_ABSENT`）。
- **PASS 判据**：`600 hermes-swe:hermes-swe` 或 `ENV_ABSENT`。
- **失败停止条件**：权限放宽 → 重新 C3 修正。
- **回滚命令**：重新 `chmod 600`。

### C8. 验证 Webhook OFF / 无新增公网端口 / 不触碰既有 Nginx
- **执行命令**：
  ```bash
  # 仅核对 Hermes 本次新增的 127.0.0.1:8080；不处理、不停止任何其他服务
  sudo ss -tlnp 2>/dev/null | grep ':8080' || echo "NO_8080_LISTEN"
  systemctl is-active nginx 2>/dev/null || echo "NGINX_STATE_RECORDED"
  ```
- **预期输出**：`127.0.0.1:8080` 由 hermes-swe 监听（loopback）；nginx 状态仅记录（无论 active/inactive 均不改动）。
- **PASS 判据**：无 `0.0.0.0:*`/公网监听；nginx 未被本 Runbook 停止或改动（见 E2 差异比对）。
- **失败停止条件**：出现非 127.0.0.1 的公网监听 → 立即停 Hermes 服务并排查（违规），进入 Phase F。**注意：禁止停止既有 nginx。**
- **回滚命令**：`sudo systemctl stop hermes-swe-control-plane.service`（**仅** Hermes；绝不 `systemctl stop nginx`）。

---

## Phase D — 真实 Provider 预检（节八）

> 仅在 `.env` 三变量齐全时执行真实调用；缺失 → 输出 `PROVIDER_LIVE_BLOCKED_BY_CREDENTIALS` 并停止（**不伪造 PASS**）。

### D1. 凭证齐全性检查（不读取值）
- **执行命令**：
  ```bash
  # 显式传入 APP_HOME（sudo 默认不保留操作员环境变量，避免 $APP_HOME 在子 shell 中为空）
  sudo -u "$SERVICE_USER" env APP_HOME="$APP_HOME" bash -c '
    set -a; [ -f "$APP_HOME/.env" ] && . "$APP_HOME/.env"; set +a
    for v in OPEN_SWE_OPENAI_BASE_URL OPEN_SWE_OPENAI_API_KEY OPEN_SWE_OPENAI_MODEL; do
      if [ -z "${!v:-}" ]; then echo "MISSING:$v"; fi
    done
    echo "CHECK_DONE"
  '
  ```
- **预期输出**：仅 `CHECK_DONE`（三变量均非空）；或 `MISSING:OPEN_SWE_OPENAI_*`。
- **PASS 判据**：无任何 `MISSING:*` → 进入 D2。
- **失败停止条件（即 BLOCKED）**：存在 `MISSING:*` → **打印 `PROVIDER_LIVE_BLOCKED_BY_CREDENTIALS`**，停止节八，不执行 D2，不报告 PASS。
- **回滚命令**：无需回滚（只读）。

### D2. 运行 Provider 预检（P1–P6，输出经 redact）— 禁止临时未固定 `pip install openai`
- **前置依赖检查（锁定依赖优先）**：
  ```bash
  # 0) 优先用 venv 中已安装的 openai（如既有受控安装）
  PYBIN="$APP_HOME/venv/bin/python"
  [ -x "$PYBIN" ] || PYBIN=python3
  if "$PYBIN" -c "import openai" 2>/dev/null; then
    echo "OPENAI_SDK=PRESENT"
  else
    echo "OPENAI_SDK=ABSENT"
    # 1) 仅允许使用“项目锁定依赖”；无锁定依赖则停止，绝不在服务器临时装未固定版本
    LOCK=""
    for f in requirements.lock.txt requirements.txt pip-tools.lock pip.lock poetry.lock; do
      [ -f "$SRC/$f" ] && LOCK="$SRC/$f" && break
    done
    if [ -n "$LOCK" ]; then
      echo "LOCKED_DEP_FOUND=$LOCK （按锁定版本安装，不浮动）"
      sudo -u "$SERVICE_USER" "$PYBIN" -m pip install -r "$LOCK"   # 必须含 == 固定版本
    else
      echo "STATUS: HARNESS_DEPENDENCY_MISSING — 无项目锁定依赖，停止节八"
      exit 3
    fi
  fi
  ```
  > 说明：`pyproject.toml` 仅声明 `relay = ["openai>=1.30"]`（浮动下界，**非锁定**），仓库无 lock 文件 → 实际将触发 `HARNESS_DEPENDENCY_MISSING` 并停止，**绝不**执行 `pip install openai`。
- **执行命令**（依赖就绪后以 hermes-swe 运行，结果落日志目录，禁止回显密钥）：
  ```bash
  # 显式传入 APP_HOME（sudo 默认不保留操作员环境变量，避免 $APP_HOME 在子 shell 中为空）；禁止回显密钥
  sudo -u "$SERVICE_USER" env APP_HOME="$APP_HOME" bash -c '
    set -a; [ -f "$APP_HOME/.env" ] && . "$APP_HOME/.env"; set +a
    cd "$APP_HOME"
    "$APP_HOME/venv/bin/python" scripts/provider_preflight.py \
      --json --out "$APP_HOME/logs/provider_preflight.json"
    echo "PREFLIGHT_EXIT=$?"
  '
  ```
- **预期输出**：逐行 `[PASS] P1..P6 ...`；末行 `Result: PASS`；或 `STATUS: PROVIDER_INCOMPATIBLE`（exit 1）；或 `PROVIDER_PREFLIGHT: NOT_TESTED`（exit 2）。
- **PASS 判据**：exit 0 且 `Result: PASS`；JSON 中 `status=PASS`。所有错误文本已 redact（无 `sk-` / `Bearer` / PEM）。
- **失败停止条件**：
  - exit 1（`PROVIDER_INCOMPATIBLE`）→ 记录 FAIL，不合并/不部署到生产；
  - exit 3 / `HARNESS_DEPENDENCY_MISSING` → 节八停止，记录 NOT_TESTED，**不得伪造 PASS**。
- **回滚命令**：无需回滚（只读调用，不改状态）。

### D3. 预检结果判定（退出码映射）
| exit | 含义 | 处置 |
|------|------|------|
| 0 | PASS（P1–P6 全过） | 节八 PASS |
| 1 | `PROVIDER_INCOMPATIBLE`（P2/P3 失败） | 节八 FAIL，不得用于生产 |
| 2 | `NOT_TESTED`（无 relay 配置） | 等同凭证缺失类，记 NOT_TESTED |
| 3 | harness error / `HARNESS_DEPENDENCY_MISSING`（缺 openai SDK 且无锁定依赖） | 停止节八，记 NOT_TESTED，**不得**临时 `pip install openai` |

---

## Phase E — 恢复 Webhook OFF / 不开放公网端口

> 部署与预检验证完毕后，**主动收口**：停服务、保持禁用、确认无公网监听。

### E1. 停止并禁用自启（仅本次 Hermes 服务）
- **执行命令**：
  ```bash
  sudo systemctl stop hermes-swe-control-plane.service
  sudo systemctl disable hermes-swe-control-plane.service 2>/dev/null
  systemctl is-active hermes-swe-control-plane.service 2>/dev/null || echo "SERVICE_STOPPED"
  ```
- **预期输出**：`SERVICE_STOPPED`。
- **PASS 判据**：服务非 active；Webhook/公网端口均未开启（本就未开）。
- **失败停止条件**：无法停止 → `systemctl status` 排查。
- **回滚命令**：无需（这是收口动作）。

### E2. 部署前后端口差异复核（用 comm -13 提取真实新增；E1 已停 Hermes，新增必须为空）
- **执行命令**：
  ```bash
  sudo ss -tlnp 2>/dev/null | grep -E ':80 |:443 |:8080' > /tmp/after_ports.txt || true
  echo "=== 部署前（既有）==="; cat /tmp/before_ports.txt
  echo "=== 部署后（当前）==="; cat /tmp/after_ports.txt
  # comm -13 = 出现在 after 但不在 before 中的行 = 本次部署"真实新增"的监听
  sort /tmp/before_ports.txt > /tmp/before_ports.sorted.txt 2>/dev/null || : > /tmp/before_ports.sorted.txt
  sort /tmp/after_ports.txt  > /tmp/after_ports.sorted.txt  2>/dev/null || : > /tmp/after_ports.sorted.txt
  comm -13 /tmp/before_ports.sorted.txt /tmp/after_ports.sorted.txt > /tmp/new_listeners.txt
  echo "=== 真实新增监听（E1 已停 Hermes，必须为空）==="; cat /tmp/new_listeners.txt
  # 兜底：确认 8080 已无任何残留监听（含 127.0.0.1:8080）
  sudo ss -tlnp 2>/dev/null | grep -E ':8080\b' && echo "WARN: 8080 STILL LISTENING" || echo "PORT_8080_CLEAR"
  ```
- **预期输出**：`new_listeners.txt` 为空（无新增监听）；`PORT_8080_CLEAR`（8080 无残留，含 127.0.0.1:8080）。
- **PASS 判据**：`new_listeners.txt` 为空（无 `0.0.0.0`/公网、也无 `127.0.0.1:8080` 新增）；既有 80/443/其他服务（含 nginx）状态未被本 Runbook 改变。
- **失败停止条件**：
  - `new_listeners.txt` 非空（出现任何新增监听）→ 立即排查；若含 `0.0.0.0`/公网 IP → `sudo systemctl stop hermes-swe-control-plane.service` 并报告 `STATUS: SECURITY_BOUNDARY_VIOLATION`。
  - `8080 STILL LISTENING`（8080 残留，含 127.0.0.1:8080）→ 说明 E1 未真正停服，重新执行 E1 后再复核。
  - **禁止 `kill` 不属于本次验证的 PID，禁止停止既有 nginx。**
- **回滚命令**：仅 `sudo systemctl stop hermes-swe-control-plane.service`（Hermes 本服务）；其他服务一律不动。

---

## Phase F — 回滚（异常时使用）

### F1. 服务/代码回滚
- **执行命令**（回滚到上一部署 SHA，不自动启动）：
  ```bash
  export HERMES_APP_HOME="$APP_HOME" HERMES_DEPLOY_SRC="$SRC" HERMES_SERVICE_USER="$SERVICE_USER" HERMES_AUTOSTART=0
  sudo -E bash "$SRC/scripts/rollback_control_plane.sh"        # 用 DEPLOY_HISTORY 次新 SHA
  # 或显式： sudo -E bash "$SRC/scripts/rollback_control_plane.sh" <TARGET_SHA>
  ```
- **预期输出**：`[rollback] target = <SHA>` → `deploy complete -> /opt/hermes-open-swe-lab`。
- **PASS 判据**：回滚脚本 exit 0；`$APP_HOME/DEPLOYED_SHA` 更新为目标 SHA。
- **失败停止条件**：目标 SHA 非法或 checkout 失败 → 脚本自身 fail-closed 退出，报告 `STATUS: ROLLBACK_FAILED`。
- **回滚命令**：回滚即恢复动作；若仍失败，手动 `git -C "$SRC" checkout "$RUNTIME_CODE_SHA"` 后重跑 deploy。

### F2. SQLite 恢复（当需回退数据）
- **执行命令**（先停服务）：
  ```bash
  sudo systemctl stop hermes-swe-control-plane.service
  sudo -u "$SERVICE_USER" "$APP_HOME/venv/bin/python" "$APP_HOME/scripts/restore_sqlite.py" \
    --backup "$APP_HOME/backups/events-<UTC>.db" \
    --target "$APP_HOME/runtime/events.db" --force
  ```
- **预期输出**：`RESTORE OK /opt/hermes-open-swe-lab/runtime/events.db <- ...`。
- **PASS 判据**：exit 0 且 `RESTORE OK`。
- **失败停止条件**：`RESTORE FAIL`（备份损坏/不存在）→ 停止，保留现状，报告。
- **回滚命令**：恢复为最新备份即目标；无进一步回滚。

---

## 15. 节七 / 节八 验收结果模板

复制以下模板填写（所有含密钥/Token/PEM 的字段一律写 `***REDACTED***` 或留空）：

```markdown
# MVP-0 节七/节八 验收结果

## 元信息
- 执行日期：YYYY-MM-DD
- 执行人：<运维账号，非 root>
- 目标仓库：yzhlx/hermes-open-swe-lab（✅ 非 hermes-learning-os）
- 运行时代码提交 SHA (RUNTIME_CODE_SHA)：932dcd7ef9d584955d316a3ddfca25c69f7dd6e3（git rev-parse 复核：<一致/不一致>）
- PR #5 HEAD（执行时读取）：<`gh pr view 5 --json headRefOid -q .headRefOid`；可与 RUNTIME_CODE_SHA 不同，因 PR HEAD 可能含其后的文档提交，属正常>
- 云服务器：Ubuntu 24.04 / 2CPU / 4GB

## 节七 云端受限真实部署验证
| 项 | 结果 | 证据 |
|----|------|------|
| A1 代码来源+SHA相等 | PASS/FAIL | remote 仅含 hermes-open-swe-lab; SHA_MATCH=OK（对照 RUNTIME_CODE_SHA） |
| A2 OS/资源 | PASS/FAIL | Ubuntu 24.04, nproc, RAM |
| A5 端口/Hermes 占用 | PASS/FAIL | PORT_8080_FREE / HERMES_SERVICE_INACTIVE（既有 80/443/nginx 仅记录） |
| A4 安装目录/磁盘 | PASS/FAIL | df 剩余 ≥2GB（APP_HOME 或 /opt） |
| A5b 部署前快照 | PASS/FAIL | before_ports.txt / before_nginx.txt 已存 |
| B1 SQLite 备份 | PASS/SKIP | BACKUP OK / BACKUP SKIP（EXIT=0） |
| C1 部署脚本(先跑,check_config) | PASS/FAIL | check_config: OK; service left DISABLED |
| C2 放置/修正 .env(部署后) | PASS/SKIP | ENV_PRESENT（安全通道） |
| C3 .env 权限校验(部署后,启动前) | PASS/SKIP | chmod 600, chown hermes-swe |
| C4 服务启动(hermes-swe) | PASS/FAIL | systemctl active |
| C5 /healthz + /readyz | PASS/FAIL | HTTP 200 两次 |
| C6 非 root + 仅 127.0.0.1 | PASS/FAIL | user=hermes-swe, bind=127.0.0.1:8080 |
| C7 复核 .env 权限 | PASS/ABSENT | stat 600 hermes-swe:hermes-swe |
| C8 Webhook OFF/无公网 | PASS/FAIL | 仅 127.0.0.1 曾出现但 E1 已停; nginx 未改动 |
| E 收口(停服/新增监听为空) | PASS/FAIL | SERVICE_STOPPED; new_listeners.txt 为空 |

**节七结论**：PASS / FAIL / NOT_TESTED
**节七 STATUS**：（无安全违规则为 OK；出现 0.0.0.0 监听或 root 运行 → STATUS: SECURITY_BOUNDARY_VIOLATION）

## 节八 真实 Provider 预检
- .env 三变量齐全：是 / 否
- 若否 → 输出：`PROVIDER_LIVE_BLOCKED_BY_CREDENTIALS`（不得伪造 PASS）
- 预检退出码：0(PASS) / 1(INCOMPATIBLE) / 2(NOT_TESTED) / 3(HARNESS_DEPENDENCY_MISSING)
- 依赖锁定：有锁定依赖且安装成功 / 无锁定依赖→HARNESS_DEPENDENCY_MISSING 停止

### 节八A — 真实预检（scripts/provider_preflight.py 定义，逐项一致不得改名）
- P1 basic response：PASS/FAIL
- P2 single tool call：PASS/FAIL
- P3 consecutive tool calls：PASS/FAIL
- P4 streaming：PASS/FAIL
- P5 long context：PASS/FAIL
- P6 error handling + secret redaction：PASS/FAIL

### 节八B — 离线验证（tests/test_provider_live.py 定义，逐项一致不得改名）
- P1 endpoint & auth：PASS/FAIL
- P2 specified model callable：PASS/FAIL
- P3 minimal non-streaming chat (content+finish_reason+usage)：PASS/FAIL
- P4 streaming incremental output：PASS/FAIL
- P5 tool-call structure compatible (non-stream + stream)：PASS/FAIL
- P6 fixed Open SWE agent minimal no-repo task：PASS/FAIL

- 输出是否脱敏（无 sk-/Bearer/PEM）：是 / 否

**节八结论**：PASS / FAIL / NOT_TESTED / PROVIDER_LIVE_BLOCKED_BY_CREDENTIALS / HARNESS_DEPENDENCY_MISSING

## 总体
- 是否合并任何 PR：否（✅ 符合约束 #14）
- 是否修改 D3 业务代码：否（✅ 符合约束 #13）
- 是否开放公网端口/Webhook：否（✅ 符合约束 #3/#4/#12）
- 是否触碰既有 nginx/其他服务：否（✅ 符合约束 #2/#4）
- 待用户操作：<例如：提供真实 .env 后重跑节八 / 复核合并顺序>
```

---

## 附：常见停止信号速查
- `STATUS: ENV_PREFLIGHT_FAILED` — Phase A 失败，未改动。
- `STATUS: SQLITE_BACKUP_FAILED` — Phase B 完整性失败（退出码 1）。
- `STATUS: SHA_MISMATCH` — 检出 SHA 与 RUNTIME_CODE_SHA 不一致，已自动停止。
- `WRONG_REPO_QUARANTINED` — 误拉到非目标仓库，已隔离至 /opt/quarantine，等待人工确认（未删除）。
- `STATUS: SECURITY_BOUNDARY_VIOLATION` — 出现 root 运行 / 非 loopback 绑定 / 公网端口新增 / 触碰 hermes-learning-os → 立即停服并报告。
- `PROVIDER_LIVE_BLOCKED_BY_CREDENTIALS` — 节八凭证缺失，不得伪造 PASS。
- `HARNESS_DEPENDENCY_MISSING` — 节八缺少锁定依赖（openai），停止，不得临时 `pip install openai`。
- `STATUS: ROLLBACK_FAILED` — Phase F 回滚失败，保留现场待人工。
