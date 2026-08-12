# V2 方案演练 AI 增强实现与交接说明

## 1. 小白版：这次到底做了什么

V1 是 AI 帮销售准备方案，V2 是 AI 陪销售练习怎么回答客户。

一次演练像一次模拟面试：

1. 系统把创建演练时的客户画像、方案、研究报告、公开情报和响应矩阵拍成一张“资料照片”，以后原资料变化也不会偷偷改变本次演练；
2. AI 根据角色、难度和关注点扮演客户；
3. AI 每轮先看之前问过什么，再提出一个新的客户异议；
4. 销售回答后，AI 从事实、证据、能力边界、风险、客户关注点、表达和下一步等方面评分；
5. 规则守门员同时检查“保证、百分之百、零风险”等危险承诺，AI 不能把这些问题洗掉；
6. 演练结束后生成带轮次、分数和扣分理由的报告；
7. 报告整理出可供展示稿参考的异议、推荐回答、风险和知识缺口，但默认不自动使用，必须由员工选择。

模型超时、没开通、返回坏 JSON 或返回不合格内容时，系统会自动改用原来的规则继续提问和评分，不会把演练卡死。

## 2. 代码位置

- `app/ai/rehearsal.py`：单工作流 AI 陪练、输出 Schema、规则守门和展示稿候选交接包；
- `app/services/rehearsal_service.py`：把 AI 陪练接入创建、开始、逐轮回答和报告流程；
- `app/api/deps.py`：按 `APP_AI_MODE` 选择百炼或规则降级；
- `app/api/v1/routes/rehearsals.py`：给现有演练接口注入同一个 AI 工作流；
- `tests/ai/test_rehearsal_workflow.py`：角色/难度、历史轮次、坏输出、超时、可信评分、不可变资料和展示稿边界测试；
- `.env.example`：演练模型超时和提示词长度配置。

没有修改 `app/contracts/ai.py`，五个已冻结的 `AIEngine` 方法签名保持不变；没有使用 Multi-Agent；没有改数据库、演练 URL 或原有请求字段。

## 3. 模式和配置

### 规则/演示模式

```env
APP_AI_MODE=mock
APP_REHEARSAL_AI_TIMEOUT_SECONDS=20
APP_REHEARSAL_AI_MAX_PROMPT_CHARACTERS=36000
```

此时不调用百炼，使用确定性规则。报告的 `generation.mode` 会明确显示 `rule_fallback`，不会伪装为真实模型。

### 百炼真实模式

```env
APP_AI_MODE=live
DASHSCOPE_API_KEY=只写在本地.env中
CHAT_MODEL=qwen-plus
BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
APP_REHEARSAL_AI_TIMEOUT_SECONDS=20
```

真实 API Key 不能提交到 GitHub。

## 4. 输出说明

### 客户角色

保存在演练的不可变 `context_snapshot.rehearsal_persona` 中，含业务目标、关注点、沟通风格、证据期待、上下文引用和待确认问题。

### 每轮评价

每轮 `evaluation` 包含总分和六项分数、扣分理由和上下文引用、优点、推荐回答结构、知识缺口、下一步动作，以及本轮使用模型还是规则降级。

规则守门结果与模型结果合并时采用更保守的分数；模型不能抹掉绝对承诺等规则命中的风险。

### 总结报告

报告含逐轮得分和扣分理由、上下文版本、高频问题、推荐回答、风险、知识缺口、待确认问题和下一步动作。

`presentation_handoff` 只是“候选篮子”。`select_presentation_handoff()` 只允许员工从五类内容中选择，生成的交接包明确：

- `fact_ledger_writes` 始终为空；
- `auto_applied` 始终为 `false`；
- 这些内容只能改善展示叙事和备答，不能证明企业能力；
- 展示稿事实仍须经过 FactLedger、EvidenceGuard 和 Trust Gate。

## 5. 已完成测试

```text
ruff：通过
目标文件语法编译：通过
新增 AI 演练测试：5 passed
```

测试覆盖不同角色和难度、多轮历史、坏 Schema 和超时降级、危险承诺、报告追溯、上下文不可变及展示稿可信边界。

本机新目录未安装完整项目依赖，无法执行全量数据库测试；全量测试需在队友正常 V2 开发环境或 CI 中执行。基线现有 `app/services/pptx_parser.py` 还会使当前 Python 的全目录 `compileall` 报语法错误，该文件不是本次修改。

## 6. 后续联调

1. 从最新 `origin/v2_backbone` 创建 `feat/v2-rehearsal-presentation`，把这些文件放入该分支；
2. 在有完整依赖和 PostgreSQL 的环境运行交接报告要求的全量检查；
3. 用脱敏资料分别跑 `APP_AI_MODE=mock` 和 `live`；
4. 观察角色、问题、逐轮评价和报告中的 `generation`；
5. 对展示稿衔接增量提案拍板后，再增加员工选择接口，不先改冻结契约。

