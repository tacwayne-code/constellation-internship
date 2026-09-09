# ASS 企业微信派单通知（第一版）

## 范围

本版新增 ASS 后端派单通知。新建工单及变更工程师时，在保存工单的同一事务写入 notification_outbox；普通编辑不发通知。独立线程每 2 秒处理一条待发消息，发送失败不撤销已保存工单。不补发历史工单，不发超时提醒/完成通知，不改变身份网关和客户库。

本版通过已登录派单员 API 查看记录和重试，暂未增加 H5 通知管理页面。现有小程序无需重新上传。通知为文本，仅包含工单编号、事件编号和回小程序处理的提示；不包含客户姓名、电话、地址，也不承诺企业微信内自动登录或一键跳转。

官方发送协议：https://developer.work.weixin.qq.com/document/path/90236

## Hermes 部署步骤

1. 核对仓库提交及工作区，备份 ASS SQLite（使用 SQLite backup API 或停写备份，不能在线仅复制主文件忽略 WAL）、uploads、service.env、wecom.env 和 Compose。保留旧镜像以便回滚。不要把凭据、数据库或备份提交 Git。
2. 保持 WECOM_NOTIFICATIONS_ENABLED=0。检查 /opt/constellation/secrets/wecom.env 的 CorpID、AgentID 和应用 Secret 已存在，文件 root:root 600，不输出其内容。
3. 只读核对 ASS users 与 engineers 的关联，并与管理员确认的身份映射一致后，配置 WECOM_USER_BINDINGS。键必须是 **ASS users.id** 的字符串，值是企业微信 userid；禁止只按姓名或近期登录时间推断。已确认目标为翁贻轩→WengJiaErShao、王黎明→WangLiMing，何一剑未加入暂不设置。示例格式 `{"实际用户主键":"对应企微账号"}`。当前只有工程师派单通知会使用映射，派单员映射留作后续用途。不改动身份网关绑定。
4. ASS Compose 的 env_file 保留原 service.env，并增加 /opt/constellation/secrets/wecom.env。若 environment 中存在同名键，核查覆盖关系，最终以容器内配置为准（仅汇报存在性/开关，不打印秘密）。保留本机端口、数据卷、网络、安全限制及服务器原有 bcrypt 兼容锁定。
5. 仅构建、重建 service，默认关闭通知。启动自动新增 notification_outbox 表，不修改既有表列和业务记录。健康检查、CRM/平台/身份网关回归及既有工单数量核对。部署不要求重启 Docker、Nginx 或其他业务容器。
6. 确认企微账号位于应用可见范围；身份映射没有歧义后，设置 WECOM_NOTIFICATIONS_ENABLED=1，仅重建 service。启动时不会回填历史工单，关闭期间生成的 SKIPPED 也不会重放。核验线程无重复进程/多实例异常。
7. **停止点：先汇报部署完成，不代造工单、不主动向员工发送测试消息。** 请用户王黎明手工新建一张明确标记测试的工单并指派翁贻轩，或用户明确同意后再执行定向验证。保留记录，不擅自删除。
8. 验收：工单保存正常；对应 outbox 由 PENDING→SENT；翁贻轩手机确认收到。只改描述不产生新事件；改派产生新事件，尚未发送且已经失效的旧指派事件被 CANCELLED。外部微信接口可能已发出的旧消息不能撤回，消息明确要求查看最新指派。

## 通知记录和重试

- `GET /notifications?limit=50`：ASS 原有 Cookie/Bearer 登录，仅 paidan 可用，返回最近通知状态，无密钥、收件人标识或客户正文；最大 200 条。
- `POST /notifications/{id}/retry`：仅 paidan，只有 FAILED/BLOCKED 可重新排队；记录 retry_actor_id。成功返回 PENDING，不代表发送完成。
- SENT：微信接口接受，且没有 invaliduser/unlicenseduser 等收件人错误，**不是设备收到/员工已读证明**。
- RETRY：明确可重试的服务忙/限流/取 token 网络失败，最多 5 次，指数退避；超过上限 FAILED。
- FAILED/BLOCKED：缺绑定、收件人无效、配置问题或超过重试上限，先修复再重试。已确定的收件人如果发生映射变化会阻止重试，避免错发。
- UNKNOWN：发送时连接超时/异常，或进程在发送中退出。不能确定对方是否收到，API 不允许直接重试；须先人工核验，不能通过改数据库状态盲目补发。
- CANCELLED：处理前发现工单已删除、已完成或改派，不发送旧任务。
- SKIPPED：事件创建时开关关闭，不补发。

每条事件内容固定，微信重复检查开启（1800 秒），但不能承诺跨系统严格只发一次。工单状态核查与外部发送之间存在极短并发窗口，已发消息不可撤销；业务状态始终以 ASS 为准。

第一版不是超时告警系统；Hermes 可按需通过上述接口或只读数据库检查失败记录，不要声称已经配置定时监控。不要使用包含 token 的 URL 做日志输出。

## 回滚

通知异常先设 WECOM_NOTIFICATIONS_ENABLED=0，仅重建 service，工单仍正常工作。需代码回滚则恢复旧 service 镜像和 Compose；保留新增 outbox 表及数据，不回滚整个业务数据库覆盖新工单。不直接删除失败记录。再次开启会继续处理已有 PENDING/RETRY，重新启用前应核对队列，UNKNOWN 不自动补发。

## 本地测试

在售后小程序/backend 下运行 `python -m unittest test_wecom_notifications -v`。测试使用临时 SQLite 和模拟企业微信客户端，不请求真实企微、不发真实通知。另运行现有 test_security.py 回归登录、权限及上传。
