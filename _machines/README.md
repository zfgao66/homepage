# GPU 使用情况

访问地址：`http://gaozefeng.tech/machines/`。首页及导航不放入口；设置了 `noindex`、`sitemap: false`。知道地址的人可以查看，页面没有登录凭证或远程操作功能。

只按 IP 显示用户指定的 7 台服务器：

| IP | 显卡 | 数量 |
| --- | --- | --- |
| 10.20.6.38 | RTX 3090 | 4 |
| 10.20.5.8 | RTX 3090 | 4 |
| 10.20.5.73 | RTX 4090 | 4 |
| 10.20.5.100 | RTX 4090 | 4 |
| 10.20.5.51 | RTX 4090 | 4 |
| 10.20.6.135 | RTX 3090 | 4 |
| 10.20.1.21 | H100 | 8 |

共 32 张卡。型号和数量已通过真实采样核实，保存在 `assets/machines/inventory.json`；占用中断时仍显示硬件配置。`10.20.5.8` 有一张卡无法返回指标，保留“编号未知 / 读取异常”，不推测编号、不按零占用处理。`10.20.6.11` 和 `10.20.6.12` 尚未连接，不在网页列表或本次采集中。

顶部按同一批快照汇总 GPU 总数、空余、有占用及异常 / 未更新数量。空余按 GPU 占用率低于 10% 且显存已用低于 1024 MiB 估算，不保证无人使用；使用前应查看逐卡指标。缺少指标、异常编号、重复编号及超过 5 分钟的采样均不计为空余。获取过采样后，三种状态的数量之和等于库存 GPU 总数；没有本机缓存且首次连接失败时动态数量显示“—”，不能据此判断 32 张 GPU 都异常。汇总在浏览器内计算，不增加服务器采集。

每个 IP 下同时显示 CPU 使用率、内存已用 / 总量、本地硬盘各分区的可用 / 总量。配置栏显示 CPU 型号、物理核心数、线程数，多路机器还显示 CPU 颗数。剩余空间不足 10% 的分区单独提示，不用数据盘空余量掩盖系统盘紧张的情况。

磁盘指标只读取本地已挂载文件系统的容量，不扫描文件目录、不统计虚拟内存盘或网络挂载；同一设备的绑定挂载不重复展示。排除 `/boot` 等启动分区。可用空间使用普通用户能够写入的 `f_bavail`，不把管理员保留空间计算为可用。

## 正在运行的数据链路

1. `10.20.6.38` 上的 `machines-relay.service` 主动建立到公网服务器 `43.155.209.84` 的 SSH 加密连接。
2. 连接只在公网服务器本机的 `127.0.0.1:22638` 监听，指向内网中转机的 SSH。没有向公网开放内网 SSH 端口。
3. 公网服务器的 `machines-collector.service` 每约 30 秒，通过该连接及受限 SSH 转发，读取上述 7 台机器的真实 GPU 指标。
4. 同一个采集进程在内存中保留各机器的最新快照，以只读接口监听 `127.0.0.1:8273`；已有 nginx 将 `https://43.155.209.84/machines-api/status` 转发到该接口。接口每次请求重新计算数据年龄，停止采集不会把旧数据误认为空闲。
5. 原有 HTTPS 上报继续更新备用云端状态服务；备用上传失败不延迟本机快照的采集，也不触发采集退避。
6. 公网服务器同时提供 `https://43.155.209.84/machines/`。同一个采集进程将最新快照内嵌在首次 HTML 响应中，样式和脚本也包含在同一份页面内；初次显示不再依赖额外的跨域 Fetch 或本机缓存。后续只请求同源 `/machines-api/status`，每 2 分钟更新。
7. GitHub Pages 的 `/machines/` 只读取 `https://43.155.209.84/machines-api/status`，不再通过 `chatgpt.site` 备用接口或隐藏 iframe 获取数据。页面保留 8 秒请求上限、本机最新缓存及每 2 分钟刷新。请求失败时保留已获取的快照并显示无法连接，不把 HTTP 错误、无效数据等失败一律称为超时。网页读取不会触发内网重新采集。

网页通过 Jekyll 内嵌同一份库存 JSON，首屏硬件清单无需另发请求。浏览器 `localStorage` 的 `machines.latest.v1` 仅保存一份最新快照，约十几 KB，每次成功覆盖；没有历史序列。缓存恢复会累加离线时间，超过 5 分钟的占用显示“—”。首次访问或浏览器禁用存储时仍需一次网络读取。

网页关闭、管理电脑退出后，采集仍由两台服务器上的系统服务运行；两个服务都设置了开机自启和异常重连。

备用云端服务为 `https://ruc-nova-machines-status.classyjay7.chatgpt.site`，Sites 项目 ID 为 `appgprj_6ad30235451881918763f042d3967530`，源码在本次工作目录的 `status-api` 项目中。采集器目前仍向该服务上报，每台机器仅保留最新采样；网页已不再依赖这条连接。只读直连接口支持受限来源的 GET / HEAD 跨域预检，独立服务器页面则使用同源接口。

腾讯云轻量服务器的防火墙必须允许公网 TCP 443。2026-10-17 已确认原规则仅放行 22、80、8080 和 ICMP，导致 HTTPS 请求在到达服务器前被丢弃；电脑通过代理读取备用接口成功掩盖了这项配置缺失。经用户确认，已增加来源 `0.0.0.0/0`、TCP 443、允许、备注 `GPU status HTTPS` 的规则。绑定物理网络接口并绕开代理后，接口返回 200，约 0.15 秒取得 7 台服务器、32 张 GPU 的新鲜快照，独立页面也返回 200。后续验收必须包含无代理访问，不能用备用线路成功代替主接口验证。

独立服务器页面保留原有 `frame-ancestors` 限制及 `connect-src 'self'`，没有写接口。响应不缓存、不写访问日志，只有约 80 KB 的当前页面。采集器只多保留约 70 KB 的静态模板，不新增常驻进程，也不保存历史网页。旧的嵌入脚本保留用于兼容尚未更新的网页缓存，新版页面不加载它。页面入口仍不出现在首页或导航。

公网服务器和内网机器不保存历史指标或采样文件。公网采集器的内存中只有每台机器最新一条快照，云端按机器 IP 覆盖上一条记录；本机浏览器也仅覆盖一份缓存。采集日志只记录首次状态、故障或恢复等状态变化，以及每小时一条健康摘要；不为每次成功采样或接口读取写日志。日志交给系统现有的滚动日志机制管理。

`setup_status_https.py` 只为默认 HTTP 站点增加 ACME 验证路径，保留已有应用路由，并新增独立的 443 只读状态入口。IP 地址证书使用 [Certbot 支持的短期证书](https://letsencrypt.org/2026/03/11/shorter-certs-certbot/)。证书、账号私钥及续期配置在根用户专有的 `/etc/machines-monitor-tls`；`/opt/machines-certbot` 为独立环境，不修改应用的 Python 依赖。`machines-status-cert-renew.timer` 每 6 小时检查续期，成功后校验并重载 nginx；Certbot 平时不常驻运行，自己的日志只留一个轮换备份。

内网的采样命令短时运行后退出；保留少量 SSH 连接以免每 30 秒重复建立连接。中转机另有一个加密回连进程。公网服务的 `MemoryMax=256M`、中转服务的 `MemoryMax=64M` 是上限，不是预留内存。不要删除采样命令、密钥或关闭这两个服务，否则自动更新会中断。

该方案不需要修改 DNS。公网服务器直连原公共跳板机 `202.112.116.48:52622` 的尝试超时，最终使用上述内网主动回连。公共跳板机上的本次临时采集密钥已经移除。此前准备但未启用的 `machines-api.gaozefeng.tech` 自定义域名已撤销。

## 账号、密钥与权限

- 公网采集进程以无登录权限、无 sudo 权限的 `machines-collector` 用户运行。
- 公网私钥位于 `/var/lib/machines-collector/id_ed25519`，只保存在该机器上。内网机器的对应公钥被强制限制为执行 `/opt/machines-monitor/agent.py --once --machine-id <IP>`。
- 中转机的该公钥另外只允许连接清单中 7 个 IP 的 SSH 端口。其余目标不在允许列表中。
- 内网连接进程以无登录权限、无 sudo 权限的 `machines-relay` 用户运行，私钥只在该内网机的 `/var/lib/machines-relay/id_ed25519`。
- 公网的 `machines-tunnel` 系统账号无登录权限、无 sudo 权限，对应密钥只允许使用固定的本机回连端口，不能执行命令。
- 每台机器的独立上报 token 位于公网 `/etc/machines-collector/config.json`，权限 `0600`；通过 systemd `LoadCredential` 提供给进程。云端主密钥由 Sites secret `MACHINES_INGEST_SECRET` 保管。
- 登录密码、私钥、token 不进入仓库或网页。运行时 SSH 主机密钥严格核对，HTTPS 证书正常校验。

## 状态含义

- 占用已更新：采样年龄不超过 5 分钟。网页统一使用接口给出的 `age_seconds` 加本地经过时间判断。
- 上次数据：当前先显示本浏览器保存的最近一份快照，网络更新尚未完成；采样时间保留，过期占用不会算空闲。
- 占用未更新：尚无数据，或采样年龄超过 5 分钟。过期占用率和已用显存显示“—”，不是空闲；已确认的总显存仍显示。
- 最近采样：指标实际生成时间，北京时间，不使用网页刷新时间代替。
- CPU、已用内存和磁盘可用量也在数据过期时显示“—”；CPU 配置、总内存与分区容量保留最近一次已知值。
- 读取异常：某卡无法查询。整机查询失败时逐卡查询，保留其他卡正常数据。

采集器拒绝机器 IP 不符、过期或未来时间异常的采样。单台失败不阻止其他机器更新；失败重试逐步延长，最长约 5 分钟。

## 运维

公网服务器上：

```sh
sudo systemctl status machines-collector.service
sudo journalctl -u machines-collector.service --no-pager -n 30
sudo systemctl restart machines-collector.service
sudo systemctl status machines-status-cert-renew.timer
```

内网中转机 `10.20.6.38` 上：

```sh
sudo systemctl status machines-relay.service
sudo journalctl -u machines-relay.service --no-pager -n 30
sudo systemctl restart machines-relay.service
```

暂停本功能时，停用采集器和中转服务；直连接口会不可用，已有数据超过五分钟后在网页显示占用未更新。不要停用 nginx 等其他应用共用的服务。

旧的 `machines-agent.service` 已在 7 台机器上停止并禁用。保留 `/opt/machines-monitor/agent.py` 作为受限 SSH 采样命令，不再由内网机器直接访问云端。

本仓库的 `collector_setup.py`、`relay_setup.py`、`authorize_collector.py` 为可重复执行的安装维护程序，通过 `ssh_admin.py` 从管道接收配置。保留已有其他用户授权，不修改全局 SSH 规则。新增机器需同步更新采集器及受限转发的 IP 清单、网页库存、云端 `lib/inventory.ts`，并配置对应采样公钥和独立上报凭证。

## 本地验证

```sh
python3 -m unittest discover -s _machines -p 'test_*.py'
node --check assets/js/machines.js
node _machines/test_dashboard.cjs
node _machines/test_frame.cjs
python3 _machines/build_dashboard.py --output /tmp/dashboard.html
```

更改界面后，用 `build_dashboard.py` 重新生成 bundle，通过现有受保护维护连接更新 `/opt/machines-collector/dashboard.html`，然后重启 `machines-collector.service`。共享界面在 `_includes/machines-content.html`，库存仍以 `assets/machines/inventory.json` 为准；构建不包含密码、密钥、上报 token 或配置文件。`status_api.py` 启动时只读取该固定文件，不提供任意文件读取。`setup_status_https.py` 包含两个独立的页面路径，保留其他应用路由。

测试覆盖未知 GPU 指标、单卡查询失败、物理核心与线程计数、绑定挂载去重、普通用户可用空间、单个分区读取失败、跨机器误报、过期采样、单机连接失败和重复日志抑制、故障及恢复记录。接口另验证了扩展指标的校验与旧采样的兼容。上线验证检查服务器日志、云端连续多轮采样时间及正式网页的实际展示。
