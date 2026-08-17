# 客户画像模块PRD

## 1.模块信息

- 编号：BE-04
- 目标版本：V0.5核心。
- 目标：一个客户维护一份可复用长期画像，供快速方案和Deep Research共同读取。

## 2.范围

包含画像创建、列表、详情、人工更新、多来源生成、确认和复用。不包含自动把每轮会话写回长期画像。

## 3.依赖

|方向|模块|依赖内容|
|---|---|---|
|上游|资料与任务|source_ids、补充文本|
|上游|AI运行编排|`extract_profile(raw_text, source_ids)`结果|
|下游|会话与方案|已选画像和信息缺口|
|下游|Deep Research|同一画像上下文|

## 4.冻结契约

- `/customer-profiles`
- `/customer-profiles/{profile_id}`
- `/customer-profiles/{profile_id}/generate`
- `/customer-profiles/{profile_id}/confirm`
- AI：`extract_profile(raw_text: str, source_ids: list[str])`

字段严格使用`CustomerProfileData`：industry、background、current_problem、goals、constraints、existing_systems、information_gaps。

## 5.状态与规则

```text
创建空画像→pending_confirmation
生成/人工编辑→pending_confirmation
人工确认→confirmed
新增来源→生成新草稿→再次确认
```

同一workspace内同一客户名称需要重复提示，但不自动合并。未确认画像可查看和编辑，默认不能作为正式方案事实。

## 6.功能要求

|编号|要求|验收|
|---|---|---|
|BE04-01|创建客户壳并绑定多个来源|来源关系可追溯|
|BE04-02|合并来源正文与补充文本发起异步生成|返回Job并可轮询|
|BE04-03|AI未识别字段保留空值，缺口写入information_gaps|不出现臆造行业/系统|
|BE04-04|人工可修改全部画像字段|再次保存仍待确认|
|BE04-05|确认动作记录操作者和时间|多个会话读取同一已确认版本|
|BE04-06|会话信息不会自动覆盖画像|追问后长期画像不变化|

## 7.异常与降级

- 无来源且无补充文本：不允许生成。
- 部分来源失效：使用可用来源并把缺失来源写入提示。
- AI生成失败：保留用户已输入字段，不清空画像。

## 8.交接边界

本模块对下游输出结构化画像、状态和source_ids。画像如何从文本推断由AI服务负责；是否可作为事实由确认状态决定。
