-- Demo data only: complete the prepared research task after the clearly labelled mock reply.
UPDATE research_tasks
SET status = 'completed',
    stage = 'completed',
    progress = 100,
    report = jsonb_build_object(
        'title', '东岳智行多工厂质量知识闭环与推广路线',
        'executive_summary', '建议以两条产线开展90天旁路试点，以统一质量履历和人工复核闭环验证价值，再根据证据完整度、查询时延和异常闭环效果决定是否扩大到其他工厂。',
        'recommended_route', jsonb_build_array(
            '0–15天：冻结数据口径、NTP对时、序列号和设备编码规则',
            '16–45天：只读接入MES、QMS与边缘设备，建立VIN—工位—设备—时间质量履历',
            '46–75天：上线质量事件和人工复核闭环，验证脱敏特征路线',
            '76–90天：完成指标核验、风险复盘与多工厂推广决策'
        ),
        'trust_boundary', '历史指标只作为同类项目依据，不直接承诺当前客户效果；专家补充内容为演示Mock资料，仍属待确认信息。',
        'data_status', 'demo_mock_supplement'
    ),
    completed_at = now(),
    error_code = NULL,
    error_summary = NULL,
    updated_at = now()
WHERE id = '8f72b927-65a7-4a6f-9cdd-76981fddd4ca'
  AND EXISTS (
      SELECT 1 FROM expert_replies
      WHERE research_task_id = research_tasks.id
        AND feishu_message_id = 'mock-demo-reply-20260816-001'
  );

-- Keep failed solution runs for audit, but remove their duplicate messages from
-- the prepared recording conversation.  This makes session restoration select
-- the original complete, evidence-backed quick solution instead of a later
-- safe-degradation placeholder.
UPDATE messages
SET is_deleted = TRUE,
    updated_at = NOW()
WHERE session_id = '4aafb330-c788-41da-a4da-706e1ba112fd'
  AND sequence BETWEEN 3 AND 6
  AND is_deleted = FALSE;
