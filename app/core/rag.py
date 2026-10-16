from app.core.confidence import compute_evidence_confidence, retrieved_snapshot
from app.core.retrieval import arrange_evidence

REFUSAL = "抱歉，现有知识库中没有足够依据回答这个问题。请联系人工客服核实，以平台售后规则为准。"


class RagPipeline:
    def __init__(self, retrieval, repository, self_check, threshold=0.5, evidence_threshold=None):
        self.retrieval, self.repository = retrieval, repository
        self.self_check, self.threshold = self_check, threshold
        self.evidence_threshold = evidence_threshold

    async def assess(self, query, hits, *, policy_only=False):
        confidence = compute_evidence_confidence(hits)
        # Refund workflow retains its policy gate. Knowledge queries use the
        # calibrated four-signal gate before calling semantic self-check.
        if not policy_only and self.evidence_threshold is not None:
            if confidence.score < self.evidence_threshold:
                return (
                    "retrieval_low_conf",
                    f"置信度 {confidence.score:.6f}，信号 {confidence.signals}",
                )
        elif not hits or max(hit["score"] for hit in hits) < self.threshold:
            return "retrieval_low_conf", "未命中或最高重排分低于阈值"
        check = self.self_check.check_policy if policy_only else self.self_check
        useful, reason = await check(query, hits)
        return (None, reason) if useful else ("self_check", reason)

    async def answer_evidence(self, query, conversation_id, raw_question=None):
        hits = await self.retrieval.search_knowledge(query, 10, strategy="hybrid_rerank")
        source, reason = await self.assess(raw_question or query, hits)
        if source:
            await self.repository.low_confidence(
                conversation_id,
                raw_question or query,
                source,
                reason,
                retrieved_chunks=retrieved_snapshot(hits) or None,
            )
            return {
                "hits": [],
                "citations": [],
                "refuse": True,
                "message": REFUSAL,
                "reason": reason,
                "found": False,
                "retrieved_snapshot": retrieved_snapshot(hits),
            }
        citations = arrange_evidence(hits)
        return {
            "hits": [{"question": h["question"], "answer": h["answer"]} for h in citations],
            "citations": citations,
            "refuse": False,
            "found": True,
            "retrieved_snapshot": retrieved_snapshot(hits),
            "message": "证据足够，请只据带编号证据回答，事实句末使用 [n] 引用。",
        }
