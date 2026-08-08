import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.ai import AIEngine
from app.core.errors import AppError, ErrorCode
from app.db.models import (
    CollaborationStatus,
    ContributionRole,
    Experience,
    ExpertCollaboration,
    ExpertContribution,
    ExpertQuestionStatus,
    ExpertReply,
    ResearchTask,
    ResearchTaskStatus,
    ReviewStatus,
    SourceFreshness,
    SourcePurpose,
    SourceStatus,
    SourceType,
)
from app.db.repositories import (
    ExpertCollaborationRepository,
    ExpertContributionRepository,
    ExpertReplyRepository,
    ResearchTaskRepository,
    SourceRepository,
    UserRepository,
)
from app.integrations.protocols import FeishuAdapter
from app.schemas.v1 import ResearchContextRead
from app.services.ai_payload_adapter import build_solution_context
from app.services.ai_run_service import persist_last_ai_run
from app.services.research_service import ResearchTaskService

ACTIVE_COLLABORATION_STATUSES = {
    CollaborationStatus.DRAFT,
    CollaborationStatus.AWAITING_CONFIRMATION,
    CollaborationStatus.GROUP_CREATED,
    CollaborationStatus.SENT,
}


class ExpertCollaborationService:
    def __init__(
        self,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        user_id: uuid.UUID,
        ai_engine: AIEngine,
        feishu: FeishuAdapter,
    ) -> None:
        self.session = session
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.ai_engine = ai_engine
        self.feishu = feishu
        self.tasks = ResearchTaskRepository(session, workspace_id)
        self.collaborations = ExpertCollaborationRepository(session, workspace_id)
        self.contributions = ExpertContributionRepository(session, workspace_id)
        self.replies = ExpertReplyRepository(session, workspace_id)
        self.users = UserRepository(session, workspace_id)
        self.sources = SourceRepository(session, workspace_id)

    async def create(self, research_task_id: uuid.UUID) -> ExpertCollaboration:
        await self._lock_task(research_task_id)
        existing = await self.collaborations.get_active_for_task(research_task_id)
        if existing is not None:
            return existing
        task = await self._task_with_context(research_task_id)
        context = await ResearchTaskService(self.session, self.workspace_id, self.user_id).context(
            task.id
        )
        context = ResearchContextRead.model_validate(context).model_dump(mode="json")
        candidate_records = await self._candidate_records(task)
        routing_context = build_solution_context(task.question, task.profile_snapshot)
        routing_context.update(
            {
                "task_type": "expert_routing",
                "research_task_id": str(task.id),
                "research_context": self._ai_research_context(task),
                "candidate_records": [
                    {
                        "candidate_id": item["candidate_id"],
                        "display_name": item["display_name"],
                        "contributions": item["contributions"],
                    }
                    for item in candidate_records
                ],
                "trace_id": task.trace_id,
            }
        )
        routing = await self.ai_engine.extract_search_intent(routing_context)
        persist_last_ai_run(
            self.session,
            self.workspace_id,
            self.ai_engine,
            target_type="expert_collaboration",
            target_id=task.id,
            input_summary={"candidate_count": len(candidate_records)},
        )
        ranked = routing.get("ranked_candidate_ids", [])
        reasons = {
            item["candidate_id"]: item
            for item in routing.get("candidate_reasons", [])
            if item.get("candidate_id")
        }
        candidates = [
            {**item, "recommendation": reasons.get(item["candidate_id"])}
            for item in candidate_records
            if item["candidate_id"] in ranked
        ]
        questions = routing.get("expert_questions") or task.expert_questions
        context["pending_questions"] = questions
        collaboration = await self.collaborations.create(
            research_task_id=task.id,
            created_by_id=self.user_id,
            status=CollaborationStatus.AWAITING_CONFIRMATION,
            group_name=f"淘到宝专家协作-{task.title[:60]}",
            candidate_records=candidates,
            selected_expert_ids=[],
            questions=questions,
            context_snapshot=context,
            content_bundle=None,
            retry_count=0,
        )
        await self.session.commit()
        await self.session.refresh(collaboration)
        return collaboration

    async def get(self, collaboration_id: uuid.UUID) -> ExpertCollaboration:
        item = await self.collaborations.get(collaboration_id)
        if item is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "专家协作不存在", status_code=404)
        return item

    async def list_items(self, page: int, page_size: int) -> tuple[list[ExpertCollaboration], int]:
        return (
            await self.collaborations.list_filtered(offset=(page - 1) * page_size, limit=page_size),
            await self.collaborations.count_filtered(),
        )

    async def update_draft(
        self,
        collaboration_id: uuid.UUID,
        *,
        group_name: str,
        selected_expert_ids: list[uuid.UUID],
        questions: list[dict[str, Any]],
    ) -> ExpertCollaboration:
        item = await self.get(collaboration_id)
        if item.status not in {
            CollaborationStatus.DRAFT,
            CollaborationStatus.AWAITING_CONFIRMATION,
        }:
            raise AppError(
                ErrorCode.VALIDATION_FAILED, "已执行的专家协作不能再编辑", status_code=409
            )
        allowed = {candidate["candidate_id"] for candidate in item.candidate_records}
        selected = [str(user_id) for user_id in dict.fromkeys(selected_expert_ids)]
        if not selected or not set(selected).issubset(allowed):
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "只能选择具有真实贡献记录的候选专家",
                status_code=422,
            )
        allowed_questions = {
            question["question_id"] for question in item.context_snapshot["pending_questions"]
        }
        if any(question["question_id"] not in allowed_questions for question in questions):
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "协作问题必须来自原研究任务",
                status_code=422,
            )
        item.group_name = group_name.strip()
        item.selected_expert_ids = selected
        item.questions = questions
        item.status = CollaborationStatus.AWAITING_CONFIRMATION
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def confirm(self, collaboration_id: uuid.UUID) -> ExpertCollaboration:
        item = await self.get(collaboration_id)
        await self._lock_task(item.research_task_id)
        if item.status == CollaborationStatus.SENT:
            return item
        if item.status not in {
            CollaborationStatus.DRAFT,
            CollaborationStatus.AWAITING_CONFIRMATION,
            CollaborationStatus.FAILED,
        }:
            raise AppError(ErrorCode.VALIDATION_FAILED, "协作状态不可执行", status_code=409)
        if not item.selected_expert_ids or not item.questions:
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "请先确认专家和问题",
                status_code=422,
            )
        creator = await self.users.get(self.user_id)
        if creator is None:
            raise AppError(ErrorCode.AUTH_REQUIRED, "当前用户不存在", status_code=401)
        selected_ids = [uuid.UUID(value) for value in item.selected_expert_ids]
        experts = await self.users.list_by_ids(selected_ids)
        if len(experts) != len(selected_ids):
            raise AppError(ErrorCode.VALIDATION_FAILED, "选中的专家已失效", status_code=409)
        bundle = self._content_bundle(item)
        execution = dict((item.content_bundle or {}).get("_execution") or {})
        bundle["_execution"] = execution
        try:
            if not item.feishu_document_id:
                document = await self.feishu.create_document(
                    item.group_name, bundle["document"], creator.feishu_access_token
                )
                item.feishu_document_id = document.document_id
                item.feishu_document_url = document.url
            item.status = CollaborationStatus.GROUP_CREATED
            item.content_bundle = bundle
            await self.session.commit()
            bundle = self._content_bundle(item)
            bundle["_execution"] = execution
            if not item.feishu_group_id:
                group = await self.feishu.create_group(
                    item.group_name,
                    [expert.feishu_user_id for expert in experts],
                    creator.feishu_access_token,
                    f"{item.id}:group",
                )
                item.feishu_group_id = group.chat_id
                item.content_bundle = bundle
                await self.session.commit()
            if not execution.get("card_message_id"):
                execution["card_message_id"] = await self.feishu.send_group_message(
                    item.feishu_group_id,
                    bundle["card"],
                    creator.feishu_access_token,
                    f"{item.id}:card",
                )
                item.content_bundle = bundle
                await self.session.commit()
            if not execution.get("opening_message_id"):
                execution["opening_message_id"] = await self.feishu.send_group_message(
                    item.feishu_group_id,
                    {"text": bundle["group_opening"]["text"]},
                    creator.feishu_access_token,
                    f"{item.id}:opening",
                )
                item.content_bundle = bundle
                await self.session.commit()
        except Exception as exc:
            item.status = CollaborationStatus.FAILED
            item.error_code = ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE.value
            item.error_summary = str(exc)[:1000]
            item.retry_count += 1
            await self.session.commit()
            raise AppError(
                ErrorCode.SOURCE_TEMPORARILY_UNAVAILABLE,
                "飞书专家协作执行失败，可安全重试",
                status_code=503,
                retryable=True,
            ) from exc
        item.content_bundle = bundle
        item.status = CollaborationStatus.SENT
        item.sent_at = datetime.now(UTC)
        item.error_code = None
        item.error_summary = None
        task = await self.tasks.get(item.research_task_id)
        if task is not None:
            task.status = ResearchTaskStatus.WAITING_EXPERT
            task.stage = "waiting_expert"
        await self.session.commit()
        await self.session.refresh(item)
        return item

    async def record_reply(
        self,
        *,
        collaboration_id: uuid.UUID,
        question_id: str,
        author_id: str,
        author_name: str,
        answer_text: str,
        feishu_message_id: str,
        message_url: str,
    ) -> tuple[ExpertReply, ResearchTask]:
        existing = await self.replies.get_by_message_id(feishu_message_id)
        if existing is not None:
            task = await self.tasks.get(existing.research_task_id)
            if task is None:
                raise AppError(ErrorCode.VALIDATION_FAILED, "原研究任务不存在", status_code=404)
            return existing, task
        item = await self.get(collaboration_id)
        if item.status != CollaborationStatus.SENT:
            raise AppError(ErrorCode.VALIDATION_FAILED, "协作尚未发送", status_code=409)
        question_ids = {question["question_id"] for question in item.questions}
        if question_id not in question_ids:
            raise AppError(ErrorCode.VALIDATION_FAILED, "问题不属于该协作", status_code=422)
        if author_id not in {
            candidate["feishu_user_id"]
            for candidate in item.candidate_records
            if candidate["candidate_id"] in item.selected_expert_ids
        }:
            raise AppError(ErrorCode.VALIDATION_FAILED, "回复人不属于已确认专家", status_code=403)
        reply = await self.replies.create(
            research_task_id=item.research_task_id,
            collaboration_id=item.id,
            question_id=question_id,
            author_id=author_id,
            author_name=author_name,
            answer_text=answer_text,
            feishu_message_id=feishu_message_id,
            message_url=message_url,
            status=ExpertQuestionStatus.ANSWERED,
        )
        task = await self.tasks.get(item.research_task_id)
        if task is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "原研究任务不存在", status_code=404)
        task.status = ResearchTaskStatus.RESEARCHING
        task.stage = "expert_feedback_received"
        await self.session.commit()
        await self.session.refresh(reply)
        return reply, task

    async def adopt_reply(self, reply_id: uuid.UUID, data: dict[str, Any]) -> ExpertReply:
        reply = await self.replies.get(reply_id)
        if reply is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "专家回复不存在", status_code=404)
        if reply.adopted_experience_id is not None:
            return reply
        source = await self.sources.create(
            imported_by_id=self.user_id,
            type=SourceType.PASTED_TEXT,
            purpose=SourcePurpose.EXPERIENCE,
            title=f"专家回复：{data['name']}",
            content=reply.answer_text,
            source_url=f"expert-reply://{reply.id}",
            author=reply.author_name,
            source_updated_at=reply.created_at,
            synced_at=datetime.now(UTC),
            tags=data.get("tags", []),
            status=SourceStatus.PENDING_REVIEW,
            freshness_status=SourceFreshness.CURRENT,
        )
        experience = Experience(
            workspace_id=self.workspace_id,
            source_id=source.id,
            data={**data, "source_id": str(source.id)},
            review_status=ReviewStatus.PENDING_REVIEW,
            embedding_ready=False,
        )
        self.session.add(experience)
        await self.session.flush()
        reply.adopted_experience_id = experience.id
        reply.status = ExpertQuestionStatus.ACCEPTED
        await self.session.commit()
        await self.session.refresh(reply)
        return reply

    async def _task_with_context(self, task_id: uuid.UUID) -> ResearchTask:
        task = await self.tasks.get(task_id)
        if task is None:
            raise AppError(ErrorCode.VALIDATION_FAILED, "研究任务不存在", status_code=404)
        if (
            task.status
            in {
                ResearchTaskStatus.QUEUED,
                ResearchTaskStatus.PLANNING,
                ResearchTaskStatus.CANCELLED,
            }
            or task.evidence_snapshot is None
        ):
            raise AppError(
                ErrorCode.VALIDATION_FAILED,
                "研究任务尚未形成可协作上下文",
                status_code=409,
            )
        return task

    async def _candidate_records(self, task: ResearchTask) -> list[dict[str, Any]]:
        snapshot = task.evidence_snapshot or {}
        source_ids = {
            uuid.UUID(item["source_id"])
            for kind in ("experiences", "capabilities")
            for item in snapshot.get(kind, [])
        }
        contributions = await self.contributions.list_for_sources(list(source_ids))
        users = await self.users.list_by_ids(list({item.user_id for item in contributions}))
        users_by_id = {user.id: user for user in users}
        grouped: dict[uuid.UUID, list[ExpertContribution]] = {}
        for contribution in contributions:
            grouped.setdefault(contribution.user_id, []).append(contribution)
        return [
            {
                "candidate_id": str(user_id),
                "display_name": users_by_id[user_id].name,
                "feishu_user_id": users_by_id[user_id].feishu_user_id,
                "contributions": [
                    {
                        "contribution_id": str(item.id),
                        "source_id": str(item.source_id),
                        "title": item.title,
                        "tags": item.tags,
                        "reviewed": item.role == ContributionRole.REVIEWER,
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in records
                ],
            }
            for user_id, records in grouped.items()
            if user_id in users_by_id
        ]

    def _ai_research_context(self, task: ResearchTask) -> dict[str, Any]:
        questions = task.expert_questions or [
            {
                "question_id": f"GAP-{index}",
                "question": gap,
                "required_roles": [],
                "sensitive": False,
            }
            for index, gap in enumerate(task.knowledge_gaps, start=1)
        ]
        return {
            "research_task_id": str(task.id),
            "conversation_summary": self._summary(task.conversation_snapshot, "暂无研究对话"),
            "research_document_summary": self._summary(
                task.report or task.findings or task.audit, "研究仍在等待专家补充"
            ),
            "profile_summary": self._summary(task.profile_snapshot, "客户画像已确认"),
            "evidence_snapshot": task.evidence_snapshot,
            "knowledge_gaps": task.knowledge_gaps,
            "questions": questions,
        }

    def _content_bundle(self, item: ExpertCollaboration) -> dict[str, Any]:
        context = item.context_snapshot
        document_url = item.feishu_document_url or "pending://feishu-document"
        sections = [
            {
                "title": "研究问题",
                "items": [{"text": context["research_document"] or "研究进行中"}],
            },
            {
                "title": "客户画像",
                "items": [{"text": self._summary(context["customer_profile"], "已确认")}],
            },
            {
                "title": "历史经验",
                "items": [
                    {"text": self._summary(value, "已校验历史经验")}
                    for value in context["evidence_snapshot"].get("experiences", [])
                ],
            },
            {
                "title": "企业能力",
                "items": [
                    {"text": self._summary(value, "已校验企业能力")}
                    for value in context["evidence_snapshot"].get("capabilities", [])
                ],
            },
            {
                "title": "知识缺口",
                "items": [{"text": value} for value in context["knowledge_gaps"]],
            },
            {"title": "待确认问题", "items": [{"text": q["question"]} for q in item.questions]},
            {
                "title": "可信边界",
                "items": [{"text": "专家回复属于 pending_confirmation，不能自动成为已校验经验。"}],
            },
            {
                "title": "来源快照",
                "items": [{"text": "本次协作固定使用研究任务创建时的证据快照。"}],
            },
        ]
        opening = "\n".join(
            [
                f"研究任务：{item.research_task_id}",
                "请围绕以下待确认问题回复：",
                *[f"{q['question_id']}：{q['question']}" for q in item.questions],
                f"研究文档：{document_url}",
            ]
        )
        return {
            "document": {"sections": sections},
            "card": {
                "header": {"title": {"tag": "plain_text", "content": item.group_name}},
                "elements": [
                    {"tag": "markdown", "content": opening},
                ],
            },
            "group_opening": {"text": opening},
        }

    @staticmethod
    def _summary(value: Any, fallback: str) -> str:
        if not value:
            return fallback
        text_value = str(value)
        return text_value[:2000] or fallback

    async def _lock_task(self, task_id: uuid.UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{self.workspace_id}:expert-collaboration:{task_id}"},
        )


async def resolve_collaboration_workspace(
    session: AsyncSession, collaboration_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    statement = (
        select(ExpertCollaboration.workspace_id, ResearchTask.created_by_id)
        .join(ResearchTask, ResearchTask.id == ExpertCollaboration.research_task_id)
        .where(
            ExpertCollaboration.id == collaboration_id,
            ExpertCollaboration.is_deleted.is_(False),
            ResearchTask.is_deleted.is_(False),
        )
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise AppError(ErrorCode.VALIDATION_FAILED, "专家协作不存在", status_code=404)
    return row.workspace_id, row.created_by_id


async def resolve_collaboration_by_feishu_group(
    session: AsyncSession, group_id: str
) -> tuple[ExpertCollaboration, uuid.UUID, uuid.UUID]:
    statement = (
        select(ExpertCollaboration, ResearchTask.created_by_id)
        .join(ResearchTask, ResearchTask.id == ExpertCollaboration.research_task_id)
        .where(
            ExpertCollaboration.feishu_group_id == group_id,
            ExpertCollaboration.is_deleted.is_(False),
            ResearchTask.is_deleted.is_(False),
        )
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise AppError(ErrorCode.VALIDATION_FAILED, "飞书群未关联专家协作", status_code=404)
    collaboration, created_by_id = row
    return collaboration, collaboration.workspace_id, created_by_id
