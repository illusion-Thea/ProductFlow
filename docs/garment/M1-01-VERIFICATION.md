# M1-01 替身验证操作说明

[English](M1-01-VERIFICATION.en.md)。本切片只用于独立验证实例。已实现原型任务/十位置、预览确认、请求账本、Outbox、共享额度、未知结果、显式重试/取消、本地恢复及 Q01 删除保护。真实模型、成品合成、采用/R0、正式账号/归档/留存和生产部署仍在后续范围。产品规则以 [PRD](PRD.md) 为准。

## 环境及启动

使用 `scripts/verify_m101.py`，不要使用现有 Windows 启动脚本或 `.env.dev`。`--run-id` 对应 `.local/verification/m1-01/<run-id>/manifest.json`；真实路径、端口和 marker 以该文件为准。runner 在业务模块导入前设置隔离配置、网络和原生数据库驱动栅栏。私有随机密码写入 run 内 private.json，不复制到报告或 Git。

下面的 `python` 指已核对的 Python 3.12 虚拟环境解释器，依赖来自 `backend/uv.lock`；前端复用仓库 Node 22/pnpm 10 离线缓存和 `web/pnpm-lock.yaml`。当前实现需要已有原生 PG/Redis 二进制，缺失即停止，不自动安装或联网。

```text
python -B scripts/verify_m101.py print-plan --run-id <run-id>
python -B scripts/verify_m101.py prepare --run-id <run-id>
python -B scripts/verify_m101.py preflight --run-id <run-id>
python -B scripts/verify_m101.py migrate --run-id <run-id> --stage expand
python -B scripts/verify_m101.py migrate --run-id <run-id> --stage head
python -B scripts/verify_m101.py seed --run-id <run-id>
python -B scripts/verify_m101.py prepare-web --run-id <run-id>
```

preflight 核对独立 PG 集群身份/库/角色、Redis 目录/marker/队列命名空间、存储/临时/日志目录和端口。seed 幂等，已有实验绝不清零。迁移 A=0030 单独提交枚举，B=0031 建表和旧活动隔离，0032 加不可变及归属约束，0033 追加旧隔离记录摘要，0034 将关闭等待归属到具体模型请求并保留旧关闭记录；当前迁移 head 为 `20261124_0034`。最终空库升级及 A 成功/B 失败均有独立 fixture 验证；不存在已验证的生产回退构建。

## 验证命令

```text
python -B scripts/verify_m101.py run --run-id <run-id> --mode unit -- python -m ruff check backend scripts/verify_m101.py scripts/probe_m101_isolation.py scripts/inspect_m101_execution.py scripts/verification_bootstrap
python -B scripts/verify_m101.py run --run-id <run-id> --mode unit -- python -m pytest -c backend/pyproject.toml backend/tests -q
python -B scripts/verify_m101.py run --run-id <run-id> --mode integration -- python -m pytest -c backend/pyproject.toml backend/integration_tests -q
python -B scripts/verify_m101.py run --run-id <run-id> --mode unit -- pnpm --dir web lint
python -B scripts/verify_m101.py run --run-id <run-id> --mode unit -- pnpm --dir web test:run
python -B scripts/verify_m101.py run --run-id <run-id> --mode unit -- pnpm --dir web build
python -B scripts/verify_m101.py start-app --run-id <run-id>
python -B scripts/verify_m101.py start-web --run-id <run-id>
```

集成测试自己管理双 worker/publisher/scanner。浏览器服务已启动时先 `pause-delivery --run-id <run-id>`，再跑 integration；完成后用 `resume-delivery` 恢复。runner 拒绝后台投递进程与故障用例并行。旧 run 缺最终空库 fixture 时，先 `add-fixture --run-id <run-id> --fixture-suffix fresh_final`；只追加新库，已有库拒绝重建。

## 界面操作

工作台界面当前只支持中文，不提供界面语言切换；已有英文等界面偏好会在打开时归一为中文。图片文案独立支持中文（默认）、马来语、印尼语和菲律宾语。打开中文工作台不翻译历史文案，也不触发模型请求；更换文案语言仍需显式准备预览并确认对应版本。

1. 在 manifest 的 Web 地址使用本 run 的验证密钥登录；进入 seed.json 中的商品，展开“原型验证”。创建任务并保存 SKU/内容语言。草稿可缺 SKU，确认前必须补齐。
2. 点击“准备文案预览”，查看十份替身文案；确认当前语言及版本后，单独点击十位置提交。画布“全部运行”仍只执行自由节点。
3. 图像显示为待定底图，任务不因十张底图就完成。900×1600 等 test_only 尺寸不代表冻结的正式规格。
4. 未知结果先关闭等待；原记录与额度保留。只有再次确认潜在重复收费才能创建新尝试。本地恢复的“继续/结束”不发模型请求；结束后不能复开同一尝试。
5. 冲突或输入错误保留本地草稿。取消后不再派发新工作，迟到输出归原尝试；刷新不会复活任务。关联账本资源在验证期删除返回 409，正式归档后置。

## 证据与停止

`evidence/commands.jsonl` 记录命令/退出码和原始日志；`stub-requests.jsonl` 独立记录实际替身调用。保留失败记录、数据库、原始响应、hash、浏览器截图与账本导出。费用为 stub 的 not_applicable，不能作为真实计费证据。

```text
python -B scripts/verify_m101.py stop --run-id <run-id>
```

stop 只处理 PID 与创建时间相符的本 run 进程，保留数据。迁移/启动失败时同样保留证据；不降级账本库、不恢复旧业务快照、不启动未经兼容验证的旧 worker。历史模型记录原地保留，本说明不授权 M2、真实请求或部署。
