# 服务器状态看板

访问地址为 `http://gaozefeng.tech/machines/`。页面不加入主导航，并通过 `noindex` 和 `sitemap: false` 避免主动收录。知道地址的人可以访问；页面不提供账号密码或远程控制操作。

页面只按 IP 显示已经核实的 7 台服务器，不显示机器别名或机房名称。显卡型号及数量保存在 `assets/machines/inventory.json`，来自真实机器采样；即使占用数据中断，也会保留已确认的硬件配置。展示顺序为 `10.20.6.38`、`10.20.5.8`、`10.20.5.73`、`10.20.5.100`、`10.20.5.51`、`10.20.6.135`、`10.20.1.21`，共 32 张卡。其余两台尚未接通的机器不显示在网页列表中。

## 数据流

各台 Linux 服务器上的 `agent.py` 每约 30 秒读取 GPU、CPU 和内存指标，通过 HTTPS 主动上报。网页读取云端 `/api/status`，不连接 SSH，也不持有上报凭证。

上报服务为 `https://ruc-nova-machines-status.classyjay7.chatgpt.site`，源码位于本次项目的 `status-api` 目录。Sites 项目 ID 为 `appgprj_6ad30235451881918763f042d3967530`。云端只保存每台机器最近一次经过校验的指标。

每台机器使用独立的上报 token，服务器凭证在 `/etc/machines-monitor.env`，权限为 `0600`；云端主密钥使用 Sites secret `MACHINES_INGEST_SECRET`。这些值不进入代码仓库或网页。

## 状态含义

- 占用已更新：最近 120 秒内收到过指标。
- 占用未更新：尚未收到指标，或超过 120 秒未收到新指标。过期的占用率及已用显存显示“—”，不是零占用；已识别的总显存继续显示。
- 页面只保留 IP、显卡型号与数量、逐卡占用率及显存、最近实际采样时间，不显示 CPU、温度、功耗或机房筛选。
- GPU 读取异常：查询失败时保留未知值，不能按零占用处理。整机查询失败后，会按 PCI 地址逐卡查询，以保留其他卡的正常数据。无法确认编号时显示“编号未知”，不推测编号。

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

## 待完成的域名接入

页面已发布，但实验室服务器向默认 `chatgpt.site` 主机自动上报时收到云端的 HTTP 403；经默认服务端认证方式测试仍未解决。现已申请 `machines-api.gaozefeng.tech`，等待域名管理员在 DNSPod 的 `gaozefeng.tech` 解析中添加以下三条记录。它们是公开的 DNS 验证记录，不是服务器或上报密码。

| 类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| CNAME | machines-api | custom-domains.chatgpt.site. |
| TXT | _openai-site-verification.machines-api | openai-site-verification=bQFWpL_di42LZhijSArRnHpleCOdGXJgYldhMcsDm-M |
| TXT | _cf-custom-hostname.machines-api | f8e650c4-ce6e-4ed8-a667-0d5e7269511a |

自定义域名 ID：`appgdom_6ad306ea8af48191aed57ccf5636d2b2`。添加记录后，需要核对 Sites 域名和证书状态、验证实验室服务器对新地址的访问，再更新服务器的 `MACHINES_INGEST_URL` 及网页的 `statusUrl`。确认服务器持续自动上报成功后，将 `assets/js/machines-config.js` 的 `reportingPending` 改为 `false`，移除接入尚未完成的提示。旧的上报 token 可以沿用，不需要重置服务器登录密码。

目前只完成过从管理电脑发起的真实采样写入验证，不能据此声称服务器持续上报已成功。若暂时没有后续上报，页面会在 120 秒后显示数据中断。`10.20.6.11` 与 `10.20.6.12` 仍未接入；`10.20.5.8` 的一张 3090 无法返回指标，其余三张能正常查询。
