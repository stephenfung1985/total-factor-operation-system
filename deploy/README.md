# 部署手册：Mac mini（M1）对外提供在线服务

目标：服务跑在 Mac mini 上，公司内网直接访问；外地人员通过内网穿透（SakuraFrp）从互联网访问。没有域名也没有公网 IP 的情况下可用。

```
内网用户 ──────────────HTTP──────────────┐
外地用户 ──HTTPS──> SakuraFrp 服务器 ──> frpc ──┴─> uvicorn :8000 ──> SQLite（~/tfos-data/tfos.db）
```

## 一、对外服务模式（`TFOS_PRODUCTION=1`）

`deploy/install.sh` 生成的配置默认开启。启动时系统会自检，不合格就拒绝启动：

| 项 | 行为 |
| --- | --- |
| 调试账号 `test`（密码 `test`，可切换成管理员） | 不再创建；库里已有的自动停用并清除其会话 |
| `TFOS_DEMO_AUTH=1`（请求头可冒充任何账号） | 设置了就拒绝启动 |
| 初始密码 `123456` | 不再使用；必须由 `TFOS_INITIAL_PASSWORD` 提供（至少 8 位，含字母和数字，不能是 `123456`），否则拒绝启动 |

初始密码只在**建立账号时**生效，且各账号首次登录仍强制修改。**建议部署后立刻自己先登录 admin 改密码**，再把其他账号的初始密码通知给本人，避免初始密码长期有效。

注意：全新库会写入示例数据（`seed.py`）。正式使用前请确认是否保留，不需要的话由系统管理员处理，或另行提供空库初始化。

## 二、Mac mini 系统设置（手工，一次性）

1. **接网线**。WiFi 会断线、换 IP。只能用 WiFi 的话，在路由器里按 MAC 绑定固定 IP。
2. 系统设置 → 能源：开启「唤醒以供网络访问」，并设置不自动进入睡眠；勾选「断电后自动启动」。
3. 系统设置 → 用户与群组：设置**自动登录**（本方案用 LaunchAgent，登录桌面后服务才启动）。自动登录要求关闭 FileVault 磁盘加密，这是取舍：关闭加密则机器被盗后数据可读，请把机器放在受控的地方。
4. 系统设置 → 网络 → 防火墙：开启。允许传入连接给 python 的提示选「允许」。
5. 安装依赖：`brew install python node`（Python 3.11+，Node 20+）。

## 三、安装

```bash
git clone https://github.com/stephenfung1985/total-factor-operation-system.git
cd total-factor-operation-system
deploy/install.sh        # 第一次会生成 ~/tfos-data/env 后退出
nano ~/tfos-data/env     # 改 TFOS_INITIAL_PASSWORD
deploy/install.sh        # 再运行一次：建 venv、构建前端、注册开机自启与每日备份
```

完成后屏幕会打印局域网地址，如 `http://192.168.1.20:8000/`。内网同事直接访问即可。

- 数据都在 `~/tfos-data/`：`tfos.db`（数据库）、`env`（配置，权限 600）、`logs/`、`backups/`。仓库目录里没有数据，更新代码不会影响它。
- 服务由 launchd 管理：崩溃自动重启，开机自启。查看日志：`tail -f ~/tfos-data/logs/server.log`。
- 只开 1 个 worker，SQLite 是单文件库，不要改成多进程。

## 四、让外地人员从互联网访问（SakuraFrp）

1. 注册 <https://www.natfrp.com> 并完成实名认证（国内服务商的要求）。
2. 在「隧道列表」新建隧道：类型选 **HTTPS**（或 HTTP），本地地址 `127.0.0.1`，本地端口 `8000`，节点选离你们最近的。创建后会得到一个访问地址（形如 `xxxx.natfrp.cloud`，具体以后台显示为准）。
3. 在「软件下载」里下载 **frpc（macOS arm64）**，放到 `~/tfos-data/frpc`，`chmod +x ~/tfos-data/frpc`。
4. 在后台找到你的**访问密钥**和**隧道 ID**，执行：
   ```bash
   deploy/install.sh frpc "访问密钥:隧道ID"
   ```
   frpc 会作为开机自启服务运行（配置文件权限 600，密钥不会进仓库）。日志：`~/tfos-data/logs/frpc.log`。
5. 把第 2 步的地址发给外地同事。

穿透服务商的套餐、流量、HTTPS 证书和访问控制选项以其后台为准，我无法替你核实；cpolar、花生壳的流程类似，把第 4 步换成它们的客户端即可。

## 五、对外暴露后的安全清单

- [ ] admin 已改成强密码，初始密码已通知到本人并确认都改过
- [ ] 账号管理里只保留真实在用的账号；离职人员及时停用
- [ ] **不要**在路由器上同时做端口映射，外部入口只留穿透这一条
- [ ] 穿透后台如有访问控制（来源限制 / 访问密码），建议开启
- [ ] 应用自带：连续输错 5 次锁定该账号 15 分钟，会话 12 小时过期。注意这也意味着有人可以故意输错来锁别人的账号，发生时由管理员在账号管理里重置
- [ ] 穿透之后，应用看到的来源 IP 是穿透节点的 IP，无法按用户 IP 限速，要靠账号锁定和服务商的控制

## 六、日常运维

| 事项 | 操作 |
| --- | --- |
| 备份 | 每天 02:30 自动执行，保留最近 30 份在 `~/tfos-data/backups`。**请把备份目录指到外接硬盘或网盘同步目录**（在 `~/tfos-data/env` 里加 `TFOS_BACKUP_DIR=/Volumes/xxx/tfos-backups`，改后重新运行 `deploy/install.sh`）。Mac mini 只有一块盘，盘坏了数据就没了 |
| 手动备份 | `deploy/backup.sh` |
| 恢复 | `deploy/restore.sh ~/tfos-data/backups/tfos-xxxx.db`（先停服务，旧库保留为 `.before-restore.*`） |
| 更新版本 | `git pull && deploy/install.sh`（会先自动备份，再重建前端并重启） |
| 重启服务 | `launchctl kickstart -k gui/$(id -u)/com.tfos.server` |
| 停止服务 | `launchctl bootout gui/$(id -u)/com.tfos.server` |
| 重置 admin 密码 | 在其他管理员账号的账号管理里重置；都忘了就停服务后换回备份库 |

建议每季度做一次**恢复演练**：把一份备份恢复到另一台机器或另一个目录，确认能起来、数据对。
