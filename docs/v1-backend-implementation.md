# V1 后端增量实现说明

本文件同步后端 PRD 10、11、12 的落地接口。冻结的根目录 `openapi.yaml` 与
`app/contracts/ai.py` 五个方法签名均未修改；V1 HTTP 增量见
`docs/openapi-v1-incremental.yaml`。

## Deep Research（BE-11）

- `POST/GET /api/v1/research-tasks` 创建与分页查询长任务。
- 任务按 analysis、planning、evidence_synthesis、route_comparison、fact_audit、
  awaiting_expert、final_report 持久化步骤、输入输出和失败边界。
- `/context` 是研究页一键发起与专家协作页选择任务的唯一上下文来源。
- 失败任务显式重试；已完成和已取消任务不会被后台任务重复执行。
- 每次任务固定客户画像、对话和检索证据快照，后续资产变化不改写历史结果。

## 专家识别与反馈（BE-12）

- 候选只由 `expert_contributions` 中的真实作者、贡献者和审核人产生。
- AI 通过冻结的 `extract_search_intent(context)` 完成排序，不允许发明候选或贡献。
- 同一研究任务的进行中协作由事务锁和数据库部分唯一索引双重约束。
- 专家回复绑定任务、协作、问题、作者和飞书消息；消息 ID 全局去重。
- 员工采纳回复只创建 `pending_review` 经验，向量未就绪，不能在线召回。

## 专家协作执行（BE-10）

- 创建协作后先进入 `awaiting_confirmation`，员工确认专家与问题后才调用飞书。
- 执行顺序为创建研究文档、创建群、发送卡片和开场问题；失败保留状态并可安全重试。
- 回复到达后原任务从 `waiting_expert` 恢复为 `researching`，随后继续原流水线。
- Live 飞书事件必须校验 `APP_FEISHU_VERIFICATION_TOKEN`。

## 可信基础能力

- 经验与能力分别提取、审核、向量化和检索。
- 在线检索同时要求资产 verified、Embedding 就绪、来源 completed、来源 current、
  审核版本与来源版本一致。
- 飞书原文刷新会创建新来源版本；旧来源和旧资产保留追溯，但立即标记
  `source_updated` 并暂停召回。
- AI 调用保存模型、Prompt、Schema、Embedding 版本、耗时、阶段和目标记录。
- `/api/v1/feishu/resources` 只列当前授权用户可访问的文档或妙记；选中后继续复用
  冻结的 `/sources/import-link`，妙记链接由同一适配器读取转写正文。
