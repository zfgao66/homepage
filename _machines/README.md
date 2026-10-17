# 服务器状态看板

访问地址为 `http://gaozefeng.tech/machines/`。页面不加入主导航，并通过 `noindex` 和 `sitemap: false` 避免主动收录。知道地址的人可以访问；页面不提供账号密码或远程控制操作。

## 数据流

各台 Linux 服务器上的 `agent.py` 每约 30 秒读取 GPU、CPU 和内存指标，通过 HTTPS 主动上报。网页读取云端 `/api/status`，不连接 SSH，也不持有上报凭证。

上报服务为 `https://ruc-nova-machines-status.classyjay7.chatgpt.site`，源码位于本次项目的 `status-api` 目录。Sites 项目 ID 为 `appgprj_6ad30235451881918763f042d3967530`。云端只保存每台机器最近一次经过校验的指标。

每台机器使用独立的上报 token，服务器凭证在 `/etc/machines-monitor.env`，权限为 `0600`；云端主密钥使用 Sites secret `MACHINES_INGEST_SECRET`。这些值不进入代码仓库或网页。

## 状态含义

- 未接入：云端还没有收到该机器的指标。
- 在线：最近 120 秒内收到过指标。
- 数据中断：超过 120 秒未收到新指标，旧指标不计入低占用 GPU。
- 低占用：GPU 利用率不高于 10%，显存占用不高于 20%；不表示机器已被预订或保证可用。
- GPU 指标不可读：查询失败时保留未知值，不能按零占用处理。整机查询失败后，会按 PCI 地址逐卡查询，以保留其他卡的正常数据。

## 服务器上的服务

代码：`/opt/machines-monitor/agent.py`。服务：`machines-agent.service`。采集程序使用无登录权限、无 sudo 权限的 `machines-monitor` 系统账号。

```sh
sudo systemctl status machines-agent.service
sudo journalctl -u machines-agent.service --no-pager -n 30
```

需要停止上报时：

```sh
sudo systemctl disable --now machines-agent.service
```

## 本地验证与维护

```sh
python3 -m unittest discover -s _machines -p 'test_*.py'
node --check assets/js/machines.js
python3 _machines/manage.py sample --output /tmp/machines-samples.json
```

维护程序在交互模式中隐藏密码输入；自动化模式仅从管道接收临时配置。运行时凭证不应通过命令行参数或仓库文件传递。

新增机器时，同步更新 `assets/machines/inventory.json` 和云端 `lib/inventory.ts`，为新机器配置独立上报凭证，再安装采集服务。
