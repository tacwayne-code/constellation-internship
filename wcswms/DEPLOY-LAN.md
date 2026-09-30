# Windows 局域网部署

部署对象：Windows 10/11 64 位后台主机；其他电脑、安卓平板或 PDA 通过浏览器访问。后台主机需要 Python 3.12 64 位，安装依赖时需要联网。仓库已包含网页产物，日常部署不需要 Node.js。

## 1. 下载系统

克隆或下载本仓库后，进入根目录下的 `wcswms/`。建议放在本地普通数据目录，例如 `D:\apps\constellation-internship\wcswms`。不要复制旧主机 `.venv`，新主机重新创建 Python 环境。

## 2. 配置两条网络

- 公司局域网，例如 `192.168.1.x`：电脑和平板通过该地址访问 WMS。
- PLC 有线网络，例如本机 `192.168.0.11/24`、PLC `192.168.0.100`：后台通过网线或交换机访问 PLC。

示例地址须替换为新主机实际地址，不能与现有设备冲突。通常公司网络保留默认网关，专用 PLC 网卡不另设默认网关；PLC 端口为 TCP 102。需要跨网段或 VLAN 时由网络管理员提供对应路由。

先在 PowerShell 查看：

```powershell
Get-NetIPAddress -AddressFamily IPv4
Test-NetConnection 192.168.0.100 -Port 102
```

客户端只需能访问后台的公司局域网地址，不需要直接连接 PLC 网段。

## 3. 安装与初始化

双击 **`安装WMS.cmd`**，按提示输入“后台主机公司局域网 IP”和“PLC IP”。或者在 `wcswms` 文件夹打开 PowerShell 执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-wms.ps1 -ListenHost 192.168.1.45 -PlcHost 192.168.0.100
```

上面的 `192.168.1.45` 必须换成新主机实际公司网络地址。脚本创建 `.venv`、安装锁定依赖、生成 `config/wms.json` 和只读入口的 `config/plc.json`。重复安装保留已有配置，不覆盖库存或密钥；脚本不会连接 PLC 或启动任务。若找不到 Python，可增加 `-PythonExe 'C:\实际路径\python.exe'`。

公司访问地址保存在 `config/wms.json` 的 `listen_host`，网页端口保存在 `web_port`（默认 8770）。PLC 地址为 `host`，端口 102、rack 0、slot 1。安装后如需修改网页监听地址，停止本套 WMS 后修改配置并重新启动；运行时修改 PLC IP 则直接用设备页“修改 IP”。

初始仅提供左仓 1 层 1 列单伸库位，需在页面核对；其余库位按实际设备建立。入库和出库码头默认均为 1。

## 4. 允许公司局域网访问

如果其他电脑访问不到，而后台本机能访问，在**管理员 PowerShell**执行一次以下命令（地址换成新主机公司网络 IP）：

```powershell
New-NetFirewallRule -DisplayName 'WCSWMS LAN 8770' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8770 -LocalAddress 192.168.1.45 -RemoteAddress LocalSubnet -Profile Any
```

该规则限定为这个公司网卡地址及其本地子网。跨 VLAN 的客户端需要按实际来源网段单独配置。更换 IP 或端口后检查原规则，避免重复建规则。不要关闭整个 Windows 防火墙。

## 5. 启动和打开

双击 **`启动仓储WMS.cmd`**，保持该窗口运行；Ctrl+C 停止本套后台。

浏览器打开：`http://新主机公司局域网IP:8770/`。例如仍使用示例地址时为 `http://192.168.1.45:8770/`。系统需单进程运行，不要同时启动多份 WMS 操作同一 PLC。

首次启动自动生成新控制密钥，文件是 `data/physical-control-key.txt`。在每台操作电脑/PDA/平板的右上角“解锁操作”输入此密钥。密钥不在仓库里，各新安装独立生成。

设备页显示实时状态后核对 CPU、远程许可、报警、空闲和握手条件。PLC 离线时网页库存和设置仍可打开。保存 IP 不等于已连接，须看到采样恢复“实时”。

USB/蓝牙扫码枪使用键盘输入并自动回车；电脑扫码前切换英文输入法。手机/PDA 的同一页面也可用键盘式扫码。

## 6. 接续原主机的库存和任务

**软件代码不包含现场数据。** 迁移前完成正在执行的搬运、人工交接和返库；先停止旧主机 WMS 与使用同一通信日志的其他后台，避免两台后台同时控制设备。

私下备份并传输以下文件，不上传到公共 Git 仓库：

- `data/wms/`：库存、任务、库位、单据和分配设置。
- `data/physical-commands.sqlite3` 及同名 `-wal` / `-shm`（若仍存在）：下发去重记录。
- `data/physical-control-key.txt`：如果需要继续沿用原密钥。
- `config/wms.json`：作为原参数参考，新主机应改为自己的公司网络地址。

在新后台停止的状态下恢复数据。不要只复制正在使用的 `.sqlite3` 而遗漏 WAL；需要在线备份时使用 SQLite backup API。`data/wms/server.lock` 是运行锁，新进程会重新取得；复制文件不会复制锁的持有状态。

新旧主机已有各自业务数据时不能直接合并覆盖，应先分别完整备份并核对。首次启动后比对库位数、料箱、数量和未结束任务，再安排现场试运行。旧后台保持停止。

## 7. 更新与故障检查

后续拉取代码前先备份，完成现场作业并停止后台，再 `git pull --ff-only`，安装依赖后重新启动。生产 `config/wms.json`、`config/plc.json` 与 `data/` 不受 Git 更新覆盖。浏览器 Ctrl+F5，平板重新加载。

`http://后台IP:8770/api/health` 应返回版本 `1.6.0`。网页能打开而 PLC 离线时，检查 PLC 地址、网卡 IP、TCP 102 与 CPU/S7 权限；另一台电脑网页打不开时检查公司 IP、8770 端口、防火墙和客户端是否处于可达网络。

后台已包含真实 PLC 通信，无需单独启动 WCS 服务。仿真、只读三维和 Siemens 工程软件属于独立可选入口。
