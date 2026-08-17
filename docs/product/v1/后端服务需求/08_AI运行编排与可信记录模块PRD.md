# AI运行编排与可信记录模块PRD

## 1.模块信息

- 编号：BE-08
- 目标版本：V0.5核心，V1扩展长任务。
- 目标：以冻结的五个AI方法连接业务模块，统一超时、重试、Schema检查、版本和运行日志。

## 2.范围

包含AI调用适配、上下文组装、返回校验、运行记录、降级、Prompt/模型版本记录。不负责具体Prompt内容。

## 3.依赖

|方向|模块|依赖内容|
|---|---|---|
|上游|资料、画像、资产、会话、Deep Research|raw_text、source_id、context、snapshot|
|上游|AI服务|冻结五个方法的实现|
|下游|业务模块|校验后的结构化结果或统一错误|
|下游|部署与评测|request_id、耗时、版本和错误|

## 4.冻结契约

仅调用`后端_开发/ai.py`：

```text
extract_profile(raw_text, source_ids)
extract_experience(raw_text, source_id)
extract_capabilities(raw_text, source_id)
extract_search_intent(context)
generate_solution(context, retrieval_snapshot)
```

不能新增参数或方法。V1模式通过`context`的可选、版本化字段表达，仍保持签名不变。

## 5.功能要求

|编号|要求|验收|
|---|---|---|
|BE08-01|每个调用设置超时和有限重试|超时不会永久占用任务|
|BE08-02|返回值经过Pydantic或等价Schema校验|缺字段结果不能落为正式资产|
|BE08-03|记录request_id、方法、模型、Prompt、Schema和耗时版本|一次答案可复盘|
|BE08-04|敏感原文不写普通日志|日志只保留ID和脱敏摘要|
|BE08-05|Mock与真实AI实现同一Protocol|切换不影响业务层|
|BE08-06|Schema失败允许一次修复重试|仍失败则返回`AI_OUTPUT_INVALID`|
|BE08-07|V1 Deep Research的多步调用共享trace_id|可看到每一阶段|

## 6.异常与降级

- 模型不可用：返回统一可重试错误；演示环境可显式启用Mock。
- 输出越界或伪造来源：拒绝写入，记录审计失败。
- 上下文过长：按来源分块并保留截断记录，不静默丢失。

## 7.交接边界

本模块是业务服务与AI实现间唯一适配层。业务模块不得直接调用模型SDK；AI服务不得直接写业务数据库。
