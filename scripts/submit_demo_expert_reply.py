"""Submit one clearly-labelled mock evidence reply through the real Feishu event endpoint."""

import json
import os
import urllib.request
from urllib.error import HTTPError


def main() -> None:
    payload = {
        "collaboration_id": "7fed89bb-9696-491f-a39d-5d4a9df1319e",
        "question_id": "EQ1",
        "author_id": "ou_f8a578bca1f21598ce818c2b74dba59f",
        "author_name": "季添瑞（演示 Mock 补充资料）",
        "answer_text": (
            "【演示 Mock 专家补充资料】"
            "1）PACK 质量履历包含设备 ID、设备程序版本、采集时间戳的组合索引，"
            "可按序列号回溯；2）QMS 质量事件支持 VIN、工位、设备编号与毫秒级时间范围"
            "联合查询，但跨系统时间必须先完成 NTP 对时；3）视觉质检采用厂内边缘节点"
            "提取结构化特征，外部模型只接收脱敏后的缺陷类别、置信度和坐标摘要，"
            "禁止上传原始生产图片。以上仅用于比赛演示，边界为 pending_confirmation，"
            "不自动沉淀为已校验经验。"
        ),
        "feishu_message_id": "mock-demo-reply-20260816-001",
        "message_url": (
            "https://feishu.cn/docx/KvNHdOAnpoGb03xA9wQcIqCvnTg#mock-demo-evidence"
        ),
        "verification_token": os.environ["APP_FEISHU_VERIFICATION_TOKEN"],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/expert-collaborations/events/replies",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            print(response.read().decode("utf-8"))
    except HTTPError as exc:
        print(exc.read().decode("utf-8"))
        raise


if __name__ == "__main__":
    main()
