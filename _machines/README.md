# RUC-Nova GPU 资源管理平台

固定入口：`http://gaozefeng.tech/machines/`。首页与导航不添加入口，页面设置 `noindex`、`nofollow`、`noarchive`，不加入 sitemap。本版本将 GitHub Pages 入口改为跳转页，实际平台位于 `https://43.155.209.84/machines/`。隐藏入口不代替登录保护；原域名无查询参数的新跳转内容和到正式登录页的浏览器导航均已验证。

## 2026-11-05 按账号统计实际占用

已发布账号必填和采样占用统计。每条登记及 7/30 天个人统计下方显示：已发生登记卡时、有数据覆盖的登记卡时、实际占用卡时、登记使用率、数据覆盖率。个人以既有微信归并，在同机同卡上对进程及重叠登记去重，不同服务器相加。未指定 GPU 编号时只计该账号占用的卡，且不超过申请数量；指定编号则只匹配所选卡。

- `server_username` 是每台机器上的 SSH 账号名；后端核验最近 10 分钟采到的账号清单，并保存 UID。账号存在的核验不等于证明提交者拥有账号；多人共用 Linux 账号只能统计账号整体占用。
- 旧记录保留，编辑补账号后才开始测量，不回填之前的使用情况。修改账号、卡数、编号和时间时保留绑定分段，避免把新账号的采样归到此前的记录。
- 服务器不可达时可先登记为“账号待核验”；恢复采样后自动绑定，记录 account_verified 历史和新版本，从核验时刻开始计量。在线且账号不存在时拒绝提交。
- 每约 30 秒采集 GPU 进程归属，按账号 UID 和 GPU 去重。必须有同一开机周期、间隔不超过 90 秒的两次完整采样才累计区间。以区间左端观测估算占用；短任务可能漏采。PID 已被复用、容器/MPS 无法可靠归属、显卡清单不完整时标为未知，不能当作空闲。
- 登记使用率 = 占用卡时 / 有数据覆盖的登记卡时。覆盖率 = 有数据覆盖的登记卡时 / 已发生登记卡时。覆盖为零时显示待采集；覆盖不足时不外推到整段预约。指标包含显存驻留和等待，不代表 GPU 计算效率。
- SQLite 新增 usage_accounts、usage_last、usage_intervals；不保存命令行、脚本内容或长期 PID 明细。相邻相同归属区间合并到最多 5 分钟，每小时清理超过 35 天的区间。数据库仍是 0600，同原服务运行，无新增常驻服务。

已升级采样程序：10.20.6.38、10.20.5.100、10.20.5.51、10.20.6.135、10.20.1.21。10.20.5.8 和 10.20.5.73 在内网中转及同网段 10.20.5.100 探测均无法建立 SSH 连接，因此尚未升级，采样缺失应明确展示。网络恢复后须按下述方式升级这两台，再等待采样，不能宣称已自动升级。

本次后端备份 `/var/lib/machines-management-deploy-backups/20261105T141506161770Z`，部署后 23 条登记与原有历史和备份逐项一致，integrity_check=ok。八项云端发布文件 SHA256 与本机一致，服务 active。63 项 Python 和四组 Node 检查通过。单机真实采集 60 秒的有覆盖占用 0.010772 GPU 小时，覆盖率 64.63%，覆盖部分占用率 100%；未写测试登记或运行 GPU 负载。该样本来自当时真实占用的账号，只用于验证统计链路。

发布维护现在分两部分：云端安装器要求八项文件，在原七项基础上增加 `usage_accounting.py`；内网机器需同时升级 `/opt/machines-monitor/agent.py` 和 `process_sampling.py`，先备份再原子替换，保持原 SSH 限制及主机校验。每台内网备份位于 `/opt/machines-monitor/backups/usage-<UTC时间>`。不要只发布网页或只更新云端。暂不包含自动终止任务、限制提交或飞书通知。

以下为首次上线时的历史说明，时长规则以 11 月 1 日修改为准，占用统计以本节为准。

截至北京时间 2026-10-31，HTTPS 平台已经部署并验证登录、真实采样及服务重启后登记持久化；绑定物理网卡且绕开代理的 HTTPS 读取正常。53 项 Python 测试、4 组 Node 测试、两个独立浏览器以及 360×800 视口操作通过。功能代码已推送至 master（`db6a5183f4fe2668042898ed1c9c46569e40b622`），原域名入口已验证；七项发布文件的本机与线上 SHA256 一致。用户随后已在实体手机确认登录正常且能看到申请按钮；这不等同于实体手机完整登记流程验收。未重启整台虚拟机。具体证据和范围见 [ACCEPTANCE.md](ACCEPTANCE.md)。共享访问密码通过课题组内部渠道提供，公开文档和源码不保存明文密码。

## 2026-11-01 时长限制与测试数据清理

本次规则优先于下文首次上线时的长期预约说明。新登记最多 7 天；修改时间和续期时，结束时间最多到当前时间与开始时间中较晚者起 7 天。续期快捷时长按该基准计算，不能提前叠加多个 7 天。历史长登记保留，可以修改备注、结束和取消，但不能绕过上限继续延期。前后端同时校验，自定义时间同样受限，预览和提交均校验。

用户点名的三组测试数据按姓名和微信号精确匹配；删除前备份，事务内清理对应登记、变更历史及包含其 ID 的幂等响应。其他真实登记和历史保留。备份仅在服务器私有目录保存。

## 2026-10-31 服务器列表布局调整

本轮将每台服务器改为独立卡片：顶部明确显示 IP、GPU 型号和数量，同一边框内依次放置监控与该服务器的使用登记。登记标题再次显示 IP，申请按钮始终关联当前服务器，减少跨服务器阅读时的混淆。

登记摘要同时显示有效条数、去重人数，以及“使用中 / 待开始”条数。有效登记包含当前使用和未来预约；人数按该服务器有效记录的微信号进行 NFKC 规范化、去除首尾空白并转小写后去重。同一人多条登记分别计条数，只计一人；这些是登记统计，不代表实时 GPU 计算人数。

本轮本地验证已通过：4 组 Node 回归、JavaScript 语法、bundle 构建与差异检查；360px 浏览器确认 7 张独立卡片、每个登记标题与 IP 对应、页面 scrollWidth 为 360。虚构数据的 3 条登记显示 2 人，申请表单正确关联 `10.20.6.38`；输入跨过 5 秒监控刷新后，焦点和内容保持。H100 入口正确关联 `10.20.1.21`，数量可选 1–8 张。

本轮布局已部署，备份目录为 `/var/lib/machines-management-deploy-backups/20261031T022329551953Z`。安装器返回 deployed=true，匿名接口返回 401；七项发布文件与本机 SHA256 全部一致，服务 active。正式浏览器会话继续有效，7 台监控均已更新；390px 正式页的 clientWidth / scrollWidth 均为 390，1280px 桌面清晰区分 `10.20.6.135` 整张卡片与下一台 H100 标题。截图保存在仓库外 `machines-dashboard/outputs/server-cards-mobile.jpg`、`machines-dashboard/outputs/server-cards-desktop.jpg`。

此次部署保留全部 3 条登记 ID 和部署前 5 条历史事件。验收期间用户正常操作新增了 2 条结束历史，当前共 7 条事件，每条登记均与其最新事件 after_data 一致；因此不宣称登记内容完全未变。本轮验收没有写入或编辑真实登记。

## 功能与数据口径

保留原有 7 台服务器、32 张 GPU 的监控，在每台服务器下增加共享使用登记：

| IP | 显卡 | 数量 |
| --- | --- | --- |
| 10.20.6.38 | RTX 3090 | 4 |
| 10.20.5.8 | RTX 3090 | 4 |
| 10.20.5.73 | RTX 4090 | 4 |
| 10.20.5.100 | RTX 4090 | 4 |
| 10.20.5.51 | RTX 4090 | 4 |
| 10.20.6.135 | RTX 3090 | 4 |
| 10.20.1.21 | H100 | 8 |

库存以 `assets/machines/inventory.json` 为准。`10.20.6.11`、`10.20.6.12` 不在当前采集范围。读取失败的显卡仍占库存，不能按空闲卡计算。

- 监控保留逐卡 GPU 利用率、显存、CPU、内存、本地磁盘、异常状态与采样时间。磁盘只读取已挂载本地文件系统容量，不扫描目录；绑定挂载去重，排除虚拟、网络及启动文件系统，可用空间使用普通用户可写的 `f_bavail`。
- 实时空余按 GPU 利用率低于 10%、已用显存低于 1024 MiB 估算。缺失、错误、重复编号和超过 5 分钟的采样不计为空余。未取得数据时显示“—”；采样时间用北京时间显示。
- 登记支持 8/24/48/72 小时、7/15/30 天和自定义结束时间。超过 7 天的连续计划标为长期，30 天登记不会因 GPU 空闲、采样中断或经过 72 小时而失效。
- 姓名、微信、IP、GPU 数量、开始与结束时间必填；GPU 编号、用途、使用方式、长备注可选。指定编号时数量必须匹配，且不能重复或超出库存。全部时间统一按北京时间输入和显示。
- 所有人共用访问密码，无独立账号或审批。登录成员可查看、编辑、续期、提前结束登记，或取消尚未开始的预约；变更历史保留。到期只改变状态，不删除记录。
- 时间区间按“含开始、不含结束”处理。指定 GPU 重叠或同时申请总量超过库存时显示联系人及冲突说明；必须明确确认后才能继续，双方记录都保留。提交时在数据库事务内重新检查，不能依赖先前预览结果。
- 当前有效申请卡数按正在使用的登记相加，协调中的重叠申请可能使数量超过库存；长期数含有效的当前/未来登记；即将到期指正在使用且未来 24 小时内结束。
- 7/30 天统计按微信号归并。登记 GPU 卡时按统计窗口内已发生的登记时间计算；同人在同机重叠时按“指定 GPU 并集”和“最大同时申请张数”中较大值去重，不同服务器相加。提前结束、取消和到期续期空档不多计。申请次数按窗口内创建记录计，包含后来取消的申请；长期登记时长按长期区间的时间并集计算。界面展示服务端中文统计说明。

登记是公开协调计划，不能据此认定 GPU 正在满负荷计算，也不会锁卡、执行 SSH 管理命令、杀进程、操作 Slurm 或发送微信。已有受限 SSH 连接仅继续承担采样职责。

## 架构与认证边界

```text
GitHub Pages /machines/（仅跳转）
        ↓ HTTPS
43.155.209.84 /machines/（密码界面及登录后的平台）
        ↓ 同源 /machines-api/*，浏览器 Cookie + CSRF
nginx → 127.0.0.1:8273/api/*
        ├─ 内存：每台机器最新一条监控快照
        └─ SQLite：共享登记、历史、幂等响应、登录会话
```

`status_api.py` 与采集器位于同一进程，不新增常驻后端服务。浏览器不再跨域读取旧备用站点，也不通过 iframe、JSONP 或 GitHub 内容获取私有数据。

`management_auth.py` 使用带随机盐的 PBKDF2-SHA256 密码哈希。哈希仅在服务器私有配置中；登录请求由后端核验，不能通过前端弹窗绕过。会话凭证采用 `__Host-machines_session` Cookie，设置 `Secure`、`HttpOnly`、`SameSite=Lax`、`Path=/`；无持久化 Cookie 有效期，服务端额外限制 24 小时。SQLite 只保存会话令牌的 SHA256 指纹。退出即撤销服务端会话，复制的旧 Cookie 同样失效。

匿名访问 `/machines/` 返回登录页，不内嵌监控快照或会话 CSRF；登录后才内嵌最新快照以减少首屏请求。监控、登记、历史、概览和统计接口都需要有效会话。所有写接口（包括冲突预览、退出）校验同源 Origin 和会话绑定 CSRF，登录也校验 Origin。不开放跨域读写。密码校验有请求速率和并发上限；请求体最大 32 KiB，字段另有限长、类型和数值范围校验。

接口响应使用 `Cache-Control: no-store`；平台 CSP 限制 `connect-src 'self'` 和 `frame-ancestors 'none'`。用户字段通过文本节点显示，HTML 内嵌 JSON 转义脚本边界。错误响应不返回密码、配置或堆栈。退出/会话失效会清空页面私有内容与最新快照缓存，取消迟到请求。

### 服务端接口

外部路径前缀为 `/machines-api/`，nginx 转至本机 `/api/`：

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/login` | 密码校验、设置会话 Cookie |
| GET | `/session` | 会话及 CSRF |
| POST | `/logout` | 撤销当前会话 |
| GET | `/status` | 最新监控快照及真实数据年龄 |
| GET | `/registrations?history=1` | 登记列表；省略 history 仅列有效记录，可按 machine_id 过滤 |
| POST | `/registrations/preview` | 验证表单并预览冲突，编辑可带 exclude_id |
| POST | `/registrations` | 创建登记 |
| PATCH | `/registrations/<id>` | 编辑登记 |
| POST | `/registrations/<id>/extend`、`/end`、`/cancel` | 续期、提前结束、取消未来预约 |
| GET | `/registrations/<id>/history` | 不可变更的操作历史 |
| GET | `/overview`、`/statistics?days=7` 或 `days=30` | 登记汇总与窗口统计 |

写入使用 UUID `Idempotency-Key`，同一键重试返回原结果；改变内容不能复用旧键。编辑和状态操作须带 `expected_version`，过期版本返回 409。冲突需要 `conflict_ack: true` 明确确认。SQLite 使用事务、WAL 和忙等待，持久化数据是多客户端的唯一事实来源。

## 保持不变的采集链路

1. 内网 `10.20.6.38` 的 `machines-relay.service` 主动建立到公网服务器的 SSH 加密回连。
2. 公网只在本机 `127.0.0.1:22638` 监听回连端口，指向内网中转机 SSH；没有向公网开放内网 SSH。
3. `machines-collector.service` 约每 30 秒通过受限 SSH 转发并发采集这 7 台机器。每台机器只保留最新快照，单机故障独立退避，不阻塞其他机器。
4. 本机 `127.0.0.1:8273` 提供认证后的监控与管理接口。网页初次登录读取最新数据，监控每 2 分钟刷新，登记通常每 30 秒刷新；打开登记表单时不自动重绘登记，监控渲染不替换表单和展开内容。
5. `install_management.py` 在部署时将私有配置的 `endpoint` 置为空串，停止向旧备用云端服务上传，本次线上配置已核查为关闭。采集仍正常运行；旧 token 字段暂保留以兼容机器清单，不应再启用旧上传作为网页回退。2026-10-31，旧 Sites 备用服务的访问策略已改为仅所有者（revision 3）。停止上传不等于删除旧站点已经持有的历史快照。

监控最新快照在进程内，服务重启后等待下一轮采样恢复；登记、变更历史、会话在磁盘 SQLite 中，服务重启不丢失。浏览器可覆盖缓存一份最新监控快照，不存监控时间序列；缓存年龄随离线时间累加，不会把旧数据算成空闲。登记不会因关闭网页或退出管理电脑而停止保存。

采集日志只记录状态变化和定期健康摘要；nginx 管理路径关闭访问日志。新功能不为每次轮询保存历史遥测，但会持久保存登记及其变更历史。公网服务 `MemoryMax=256M` 和中转服务 `MemoryMax=64M` 是限制，不是预留内存。首次上线验收时，采集服务实际内存为 38,608,896 字节，约 36.8 MiB；没有新增常驻后端进程。当时仅有一条明确标注的已取消验收记录、0 条有效申请；后续已有用户登记，本轮部署保留 3 条登记 ID 和全部历史，详见上方布局验收记录。

## 文件、私有配置与权限

| 位置 | 用途 |
| --- | --- |
| `/opt/machines-collector/` | 已部署 Python 代码、采样 agent 与自包含 dashboard.html |
| `/etc/machines-collector/config.json` | 0600 私有采集/管理配置，通过 systemd LoadCredential 读取 |
| `/var/lib/machines-collector/registrations.sqlite3` | 持久 SQLite，0600，归采集服务用户；同目录可能有 WAL/SHM |
| `/var/lib/machines-collector/` | 服务 StateDirectory，0700；也包含既有受限 SSH 私钥 |
| `/etc/nginx/conf.d/machines-status-https.conf` | 独立 HTTPS 平台路由 |
| `/etc/machines-monitor-tls/` | 0700，IP 证书、ACME 账号私钥和续期配置 |
| `/var/lib/machines-management-deploy-backups/` | 0700，部署前代码/配置/nginx 及 SQLite 一致性备份 |

私有配置中的 `serve_status.management` 包含 `database`、`password_hash`、`origin`，origin 为 `https://43.155.209.84`；缺失配置时后端拒绝以公开模式启动。密码通过交互式输入生成哈希或安全的私有载荷提供，不放命令参数、公开仓库、构建文件或操作日志。变更共享密码时还应明确撤销已有会话；更换哈希本身不会自动删除会话表。

公网采集账户 `machines-collector`、内网回连账户 `machines-relay`、公网隧道账户 `machines-tunnel` 均为专用系统账户，无登录 shell、无 sudo。对应公钥只允许既定采样命令、固定本机回连端口及清单内的 SSH 目标。继续严格核对 SSH 主机密钥和 HTTPS 证书。不要删除采样程序或密钥，也不要停用这些采集服务来清理所谓历史数据。

仓库忽略数据库及 WAL/SHM、运行目录和私有本地配置。仍需检查待提交文件，避免在新命名文件或发布包中误带真实申请记录、私钥、Cookie、密码哈希或配置。备份包含私有数据，同样不能上传 GitHub。

## 构建、发布与证书维护

在仓库根目录运行完整测试与构建：

```sh
python3 -m unittest discover -s _machines -p 'test_*.py' -v
node --check assets/js/machines.js
node --check assets/js/machines-management.js
node _machines/test_dashboard.cjs
node _machines/test_frame.cjs
node _machines/test_management.cjs
node _machines/test_management_races.cjs
python3 _machines/build_dashboard.py --output /tmp/machines-dashboard.html
```

Python 测试覆盖采样、采集、鉴权、HTTP、SQLite 和并发；Node 测试覆盖原监控、北京时间/长期边界、登录门控、退出清理和异步表单竞态。测试只使用临时目录及虚构人员。测试通过后仍需完成 [ACCEPTANCE.md](ACCEPTANCE.md) 的实际浏览器和线上场景，尤其是 360px 操作、复制微信、冲突确认、服务重启和无代理访问。运行中的真实登记不能作为破坏性测试数据。

`build_dashboard.py` 将共享 include、CSS、监控 JS、管理 JS 和库存合并成一个 HTML，快照和会话保留服务端替换标记；构建不包含密码、数据库或真实登记。GitHub `machines.html` 只是跳转入口；后台 bundle 和 Python 代码须单独部署，推送 GitHub 不会更新云服务器。

既有服务器升级顺序：

1. 确认当前服务、证书、持久目录及备份可用，记录旧代码版本。不要重新执行历史 `collector_setup.py` 覆盖配置。
2. 测试并构建 bundle。发布清单必须恰好包含 `collector.py`、`status_api.py`、`management_auth.py`、`reservations.py`、`usage_accounting.py`、`dashboard.html`、`install_management.py`、`setup_status_https.py` 八项；两个运维脚本一并更新，确保共用 nginx 路由生成函数。已部署的 `agent.py` 和 SSH 配置继续使用。
3. 在仓库外、权限 0600 的私有载荷中提供 `password_hash` 与 `files` 映射。各文件内容为 base64 字符串，整个 JSON 再编码为一行 base64；通过现有受保护维护连接的 stdin 交给 `install_management.py`。不把明文密码或载荷打印到终端日志。服务器本地的等价调用是 `sudo python3 install_management.py < /private/staging/release-payload.b64`。
4. 安装器校验文件清单和 Python 语法，先备份现有代码、配置、nginx 以及存在的 SQLite，再短暂停止采集服务、更新文件与私有配置、启动服务，检查匿名状态接口为 401，最后校验并重载 nginx。部署回执只包含结果、备份路径和数据库路径。
5. 从无代理网络验证 GitHub 入口跳转、正确/错误密码、匿名 401、两独立会话共享记录、刷新/退出、最新采样和服务重启。核对 nginx、采集器、证书续期状态及资源用量。浏览器自动化和物理手机实测要分别记录。
6. 提交并发布 GitHub 入口和源码，记录提交 SHA 与实际服务器部署版本；清理私有临时发布载荷，按现有运维策略保留受限备份。不要仅凭安装器返回成功就宣称全部业务验收完成。

`install_management.py` 更新既有运行环境，不负责首次创建系统账户、SSH 回连或证书。`collector_setup.py` 是旧公开上传架构的初始安装工具，目前仍会写旧 endpoint 和不含 management 的配置；不能用于重装新平台。新增服务器也需要同步更新后端 HOSTS、库存、采集清单及受限 SSH 转发，不能仅修改网页列表。

`setup_status_https.py` 复用 `install_management.py.nginx_config()`，两文件必须放在同一目录后执行。它仅在既有默认 HTTP 站点加入 ACME 验证 include，保留其他应用路由；独立 HTTPS 配置把整个 `/machines-api/` 前缀转发到受保护后端，不能恢复成旧的单一公开 status 路由。它不配置管理密码，也不证明后端已通过登录验收。

证书使用独立 `/opt/machines-certbot` 环境，不改其他应用的 Python 依赖；证书与账号保存在 `/etc/machines-monitor-tls`。本次检查采集服务及续期 timer 均为 enabled，timer active，当前证书有效期为 2026-10-27 至 2026-11-03。`machines-status-cert-renew.timer` 每 6 小时检查续期，成功后先 `nginx -t` 再 reload，不常驻 Certbot 进程。不要删除 HTTP `/.well-known/acme-challenge/` 路由或关闭续期 timer。

本轮卡片布局部署的备份目录为 `/var/lib/machines-management-deploy-backups/20261031T022329551953Z`；此前平台发布备份 `/var/lib/machines-management-deploy-backups/20261030T180948903217Z` 包含 SQLite 一致性备份。恢复方法见下节。GitHub Pages 工作流 `41104571745` 已确认 `completed / success`，对应上述功能提交，更新时间为 `2026-10-30T18:14:13Z`；实际新版入口内容和跳转也已验证。

无需新增 DNS 记录。公网 TCP 443 必须在腾讯云防火墙和主机网络规则中放行；回环 8273、22638 不向公网开放。验收须关闭代理，不能以代理下成功代替手机/普通网络直连测试。本次物理 en0 直连检查绕开代理；原域名检查另通过真实 Pages IP 的 `--resolve` 绕过本机 Clash 假 DNS，具体证据见验收记录。

## 备份、恢复与回滚

SQLite 使用 WAL。运行中不要只复制主 `.sqlite3` 文件；应使用 SQLite backup API 生成一致性备份。下面命令在公网服务器创建私有人工备份，不输出数据库内容：

```sh
sudo python3 - <<'PY'
import datetime
import os
import pathlib
import shutil
import sqlite3
os.umask(0o077)
root = pathlib.Path('/var/lib/machines-management-deploy-backups')
root.mkdir(mode=0o700, exist_ok=True)
folder = root / ('manual-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
folder.mkdir(mode=0o700)
with sqlite3.connect('file:/var/lib/machines-collector/registrations.sqlite3?mode=ro', uri=True) as source:
    with sqlite3.connect(folder / 'registrations.sqlite3') as target:
        source.backup(target)
shutil.copy2('/etc/machines-collector/config.json', folder / 'config.json')
os.chmod(folder / 'config.json', 0o600)
print(folder)
PY
```

每次正式部署还自动保存七个发布文件、私有配置和独立 nginx 文件；`manifest.json` 将原路径映射到备份编号。SQLite 备份包含会话、历史和幂等记录，需要与其他私有配置同等级保护。备份保留按现有运维策略处理，不自动删除登记历史。

安装器失败时会恢复原代码、配置和 nginx，重启服务并重新校验 nginx；它不会用旧数据库覆盖新增登记。首次管理平台升级前的旧版本没有认证，因此回退到旧公开代码会改变访问边界，应先限制该平台入口并准备兼容的修复版本。

确需恢复损坏或误操作的数据库时：

1. 选择经确认的 SQLite 备份，先保全当前数据库及 `-wal`、`-shm`，并记录恢复时间点会丢失哪些后续操作。普通代码回滚无需恢复数据库。
2. 停止 `machines-collector.service`，确认不再有数据库连接。将当前 DB/WAL/SHM 一起移到私有保全目录；不能让旧 WAL 配合恢复后的主文件继续使用。
3. 从一致性备份复制主数据库到 `/var/lib/machines-collector/registrations.sqlite3`，设置属主 `machines-collector:machines-collector`、权限 0600，父目录 0700。备份本身保持不动。恢复私有配置时保留与该代码兼容的 management 设置及正确权限。
4. 对恢复副本执行 `PRAGMA integrity_check`，应返回 `ok`。恢复会带回备份时仍有效的会话和幂等记录；需要强制重新登录时，在服务停止期间清空 `auth_sessions`，不要删除登记表。
5. 启动采集服务，验证匿名 401、登录、记录/历史/统计和新采样。恢复操作与实际重启范围写入验收记录；不要把服务重启称作整机重启。

## 日常运维

公网服务器：

```sh
sudo systemctl status machines-collector.service
sudo journalctl -u machines-collector.service --no-pager -n 30
sudo systemctl is-enabled machines-collector.service
sudo systemctl status machines-status-cert-renew.timer
sudo systemctl list-timers machines-status-cert-renew.timer
sudo nginx -t
```

内网中转机 `10.20.6.38`：

```sh
sudo systemctl status machines-relay.service
sudo journalctl -u machines-relay.service --no-pager -n 30
sudo systemctl is-enabled machines-relay.service
```

需要重载应用时只重启 `machines-collector.service`；无需重启整台服务器。暂停本功能可停止采集器/回连服务，但不要停用其他应用共用的 nginx。停止采集后旧监控随时间失效，持久登记仍在数据库中。

旧 `machines-agent.service` 不承担当前采集任务；各内网机器保留 `/opt/machines-monitor/agent.py` 作为受限 SSH 单次采样命令。采集器拒绝机器 IP 不符、过期或异常未来时间的采样；单台失败独立退避，最长约 5 分钟。GPU/CPU/内存/磁盘数据过期显示“—”，已知硬件配置可保留，绝不能把“没有取得数据”显示成“无人使用”。
