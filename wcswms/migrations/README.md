# 原库存迁移包

`wms-stock-20260930-140500.zip` 是 2026-09-30 14:05（北京时间）的 WMS 1.6.0 一致性快照。此目录提供原始校验包，下载不会自动导入库存或启动设备。

## 数据范围

| 内容 | 数量 |
| --- | ---: |
| 在库空箱 | 45 |
| 在库物料数量 | 0 |
| 库位 | 180 |
| 库存记录（含历史） | 65 |
| 物料档案 | 5 |
| 历史任务 | 116（完成 106、取消 10） |
| 历史单据 | 1（已完成） |
| PLC 指令记录 | 112 |

原箱号、库位状态、物料档案、分配规则、任务编号和去重记录均保留。当前分配规则为先右后左、先下后上、列号由小到大，层优先。

包内不包含控制密钥、SSH 密钥或 GitHub 凭据。业务数据和条码随此迁移包公开在仓库中。

## Linux 下载与校验

以下命令仅下载、校验和解压，不修改正在运行的 WMS 数据：

```sh
mkdir -p /home/sameng/wms-migration-20260930
cd /home/sameng/wms-migration-20260930
curl -fL -o wms-stock-20260930-140500.zip https://raw.githubusercontent.com/tacwayne-code/constellation-internship/main/wcswms/migrations/wms-stock-20260930-140500.zip
curl -fL -o SHA256SUMS https://raw.githubusercontent.com/tacwayne-code/constellation-internship/main/wcswms/migrations/SHA256SUMS
sha256sum -c SHA256SUMS
python3 -m zipfile -e wms-stock-20260930-140500.zip snapshot
```

校验应显示 `wms-stock-20260930-140500.zip: OK`。包内 `manifest.json` 另有每个文件的 SHA-256，`restore-verification.json` 记录隔离恢复验证结果。

## 恢复到 /home/sameng/wcswms

1. 确认原后台在快照之后没有新作业；若有变化，重新制作快照。切换前停止原后台和目标后台，确认没有运行中的任务或出入口交接。
2. 完整备份目标主机的 `data/` 目录（包含 SQLite 的 `-wal`、`-shm` 文件）及 `config/`。目标若已有业务数据，不直接覆盖或合并。
3. 在服务停止后，对目标现有 SQLite 数据库完成 WAL 检查点并关闭连接。确认检查点成功后，再处理旧数据库的日志文件，不能让旧 WAL 与恢复的数据库混用。
4. 将下面两份数据库一起恢复。不能只迁移库存库而遗漏指令去重记录：

   | 解压后的文件 | 目标文件 |
   | --- | --- |
   | `snapshot/data/wms/warehouse.sqlite3` | `/home/sameng/wcswms/data/wms/warehouse.sqlite3` |
   | `snapshot/data/physical-commands.sqlite3` | `/home/sameng/wcswms/data/physical-commands.sqlite3` |

5. 保留目标的 `config/wms.json` 和控制密钥。包内 `reference/source-wms.json` 仅供参数核对，其监听地址属于旧主机，不能覆盖目标配置。目标网页应继续在 `192.168.1.100:8770` 提供服务。
6. 确认原后台已停止，再以目标原有方式启动 WMS 1.6.0。核对 180 个库位、45 个在库空箱、5 个物料档案和 116 条历史任务，并抽查条码、库位和分配规则。

推送和下载迁移包不代表完成上述恢复步骤。PLC 连通性需在目标主机另行核对；恢复过程无需向 PLC 下发任务。
