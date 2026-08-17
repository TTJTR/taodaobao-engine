# 会话与方案运行模块PRD

## 1.模块信息

- 编号：BE-07
- 目标版本：V0.5核心。
- 目标：支持一个客户多个方案会话、连续追问和可追溯的方案运行。

## 2.范围

包含会话、消息、轮次、快速方案异步运行、轮询、检索快照、结果和失败。不包含Deep Research长任务。

## 3.依赖

|方向|模块|依赖内容|
|---|---|---|
|上游|客户画像|customer_profile_id、确认画像|
|上游|向量检索|RetrievalSnapshot|
|上游|AI运行编排|`generate_solution(context, retrieval_snapshot)`|
|下游|Web前端|会话、消息、运行状态、方案|
|下游|结果同步/专家协作执行|可共享的方案结果|

## 4.冻结契约

- `/sessions`、`/sessions/{session_id}`、`/sessions/{session_id}/turns`
- `/solution-runs/{run_id}`
- `CreateTurnRequest.mode`当前固定为`quick`。
- AI：`extract_search_intent`和`generate_solution`。

## 5.运行流程

```text
创建会话→提交用户消息→创建SolutionRun(pending)
→加载画像/历史消息→检索→保存快照→生成→Schema/引用检查
→保存助手消息和Solution(completed)
```

## 6.功能要求

|编号|要求|验收|
|---|---|---|
|BE07-01|会话必须绑定一个客户画像|不可跨客户切换历史会话|
|BE07-02|每轮消息sequence单调递增|重复提交不产生乱序|
|BE07-03|运行异步返回run_id并可轮询|前端显示真实等待/成功/失败|
|BE07-04|后续追问加载本会话上下文和最近快照|不读取其他客户会话|
|BE07-05|方案完整输出八个区块|字段与`Solution`一致|
|BE07-06|每个事实型条目带boundary和来源ID|点击可回到资产/来源|
|BE07-07|无证据时输出待确认和建议问题|不生成虚假案例|
|BE07-08|历史结果保存独立快照|之后打回资产不改历史答案|

## 7.异常与降级

- 画像不存在/跨workspace：拒绝创建会话。
- 运行失败：保存error_code和retryable，用户消息不丢失。
- 重复轮次请求：按幂等键返回同一run_id。

## 8.交接边界

本模块负责运行生命周期和持久化；检索质量、模型文案、飞书发送分别由其他模块负责。
