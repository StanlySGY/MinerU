# 实施计划

1. 在 OpsStore 增加任务快照及页级明细的级联清理；在 OpsRuntime 增加批次孤儿/超时看门狗和定时产物 GC。
2. 增加任务缓存清理、单条缓存删除 API，并在控制台任务页提供操作入口。
3. 保留批次任务的来源元数据，删除批次时同步删除其任务快照。
4. 将 Router upstream 接入环境变量和配置白名单，现场配置只重建 Router；不把跨主机 Docker 权限交给 Ops Agent。
5. 将 multi Compose 改为可按角色和节点编号部署，补齐现场四个 API 地址、API 端口和加速卡叠加配置。
6. 通过 Python 单测、Compose 展开、shell/JS 语法和 diff 检查验收。
