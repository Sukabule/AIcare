import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from app.core.budget import compute
from app.core.confidence import compute_evidence_confidence, retrieved_snapshot
from app.core.context_log import log_context
from app.core.coref import resolve_reference
from app.core.intent import IntentResult, classify
from app.core.memory import ConversationTooLong, build_window, summary_line
from app.core.prompts import (
    AGENT_SYSTEM,
    CHITCHAT_REPLY,
    COMPLAINT_REPLY,
    OTHER_REPLY,
)
from app.core.query_understanding import expand_queries, merge_policy_hits
from app.core.rag import REFUSAL
from app.core.retrieval import arrange_evidence
from app.graph.routing import route_by_intent, total_tokens
from app.tools.business import extract_order_id, list_user_orders
from app.tools.engine import ToolEngine
from app.tools.registry import validation_error

logger = logging.getLogger(__name__)


def actions_for(description, ticket_type="投诉"):
    return [
        {"type": "transfer_human"},
        {
            "type": "create_ticket",
            "draft": {"description": description, "ticket_type": ticket_type},
        },
    ]


class Nodes:
    def __init__(
        self,
        repository,
        model,
        rag,
        registry,
        settings,
        counter,
        classifier=None,
        reference_resolver=None,
        expander=None,
        intent_model=None,
    ):
        self.repository, self.model, self.rag = repository, model, rag
        self.registry, self.settings, self.counter = registry, settings, counter
        self.classifier = classifier
        self.reference_resolver, self.expander = reference_resolver, expander
        self.intent_model = intent_model or model
        self.budget = compute(settings)
        self.executor = ToolEngine(
            registry,
            settings,
            max_result_tokens=settings.tool_result_max_tokens,
            token_counter=counter,
        )

    def stamp(self, state, node, **extra):
        trace = {**state.get("trace", {}), **extra}
        trace["nodes"] = [*trace.get("nodes", []), node]
        return trace

    def recent_history(self, state):
        rows = state["messages"]
        index = max(i for i, m in enumerate(rows) if isinstance(m, HumanMessage))
        history = self.window(rows[:index], state)[-6:]
        summary = summary_line(state.get("summary", ""))
        return [*([HumanMessage(content=summary)] if summary else []), *history]

    def window(self, rows, state):
        return build_window(
            rows,
            state.get("summary_upto_msg_id", 0),
            state.get("layer1_from_msg_id", 0),
            max_tokens=self.budget.sliding,
            token_counter=self.counter,
            keep_chars=self.settings.layer2_reply_keep_chars,
            tool_keep_tokens=self.settings.layer2_tool_keep_tokens,
        )

    def log_history(self, state, history, consumer):
        log_context(
            "history_ctx",
            state["conversation_id"],
            state.get("summary", ""),
            history,
            self.counter,
            consumer=consumer,
            question=state["question"] if consumer == "coref" else state.get("resolved_query", ""),
        )

    async def resolve_reference(self, state):
        history = self.recent_history(state)
        self.log_history(state, history, "coref")
        query = (
            await self.reference_resolver(state["question"], history)
            if self.reference_resolver
            else await resolve_reference(self.model, state["question"], history)
        )
        if not query.strip():
            raise ValueError("补全问题为空")
        return {
            "resolved_query": query,
            "resolved_question": query,
            "trace": self.stamp(
                state,
                "resolve_reference",
                resolved_query=query,
                coref="passthrough" if query == state["question"] else "rewrite",
            ),
        }

    async def classify_intent(self, state):
        history = self.recent_history(state)
        self.log_history(state, history, "intent")
        if self.classifier is not None:
            result = await self.classifier(state["resolved_query"])
            if isinstance(result, str):
                result = {"intent": result, "confidence": 1.0}
            result = IntentResult.model_validate(result)
        else:
            result = await classify(self.intent_model, state["resolved_query"], history)
        intent = result.intent
        if getattr(self, "observability", None):
            await self.observability.intent(intent, result.confidence, state["conversation_id"])
        route = route_by_intent({"intent": intent})
        return {
            "intent": intent,
            "route": route,
            "intent_confidence": result.confidence,
            "trace": self.stamp(
                state,
                "classify_intent",
                intent=intent,
                route=route,
                intent_confidence=result.confidence,
            ),
        }

    async def retrieve_knowledge(self, state):
        query = state["resolved_query"]
        hits = await self.rag.retrieval.search_knowledge(query, 10, strategy="hybrid_rerank")
        source, reason = await self.rag.assess(query, hits)
        confidence = compute_evidence_confidence(hits)
        citations = [] if source else arrange_evidence(hits)
        return {
            "evidence": json.dumps(citations, ensure_ascii=False),
            "citations": citations,
            "evidence_strong": source is None,
            "evidence_confidence": confidence.score,
            "retrieved_snapshot": retrieved_snapshot(hits),
            "fallback_source": source or "",
            "gate_source": source or "",
            "gate_reason": reason,
            "tool_results": [
                {
                    "name": "query_faq",
                    "tool_call_id": "retrieve_knowledge",
                    "ok": True,
                    "data": {"found": source is None, "citations": citations},
                }
            ],
            "trace": self.stamp(
                state,
                "retrieve_knowledge",
                retrieved=True,
                evidence_confidence=confidence.score,
                confidence_signals=confidence.signals,
            ),
        }

    async def fetch_order(self, state):
        another_order = any(s in state["question"] for s in ("另一单", "其他订单"))
        order_id = extract_order_id(state["question"] if another_order else state["resolved_query"])
        if not order_id and not another_order:
            for message in reversed(self.recent_history(state)):
                order_id = extract_order_id(message.text)
                if order_id:
                    break
        if not order_id:
            orders = list_user_orders(state["user_id"])
            # This entire prefix is read-only because interrupt restarts the node.
            order_id = interrupt({"type": "select_order", "orders": orders})
            if not isinstance(order_id, str) or order_id not in {o["order_id"] for o in orders}:
                raise ValueError("请选择列表中的订单")
        call = {
            "name": "query_order",
            "args": {"order_id": order_id},
            "id": "order_" + str(state["conversation_id"]),
        }
        result = (
            await self.executor.execute(call, state["conversation_id"], state["question"])
        ).model_dump()
        if not result["ok"]:
            raise ValueError("订单查询失败，暂不能判断退款条件")
        ai = AIMessage(content="", tool_calls=[call])
        content = json.dumps(result, ensure_ascii=False)
        await self.repository.append(state["conversation_id"], "assistant", None, tool_calls=[call])
        await self.repository.append(
            state["conversation_id"], "tool", content, tool_call_id=call["id"]
        )
        return {
            "order_id": order_id,
            "order_data": result["data"],
            "tool_calls": [*state["tool_calls"], call],
            "tool_results": [*state["tool_results"], result],
            "messages": [
                ai,
                ToolMessage(content=content, tool_call_id=call["id"], name="query_order"),
            ],
            "trace": self.stamp(state, "fetch_order", order_id=order_id),
        }

    async def retrieve_policy(self, state):
        query = state["resolved_query"]
        queries = (
            await self.expander(query, state["order_data"])
            if self.expander
            else await expand_queries(self.model, query, state["order_data"])
        )
        groups = await asyncio.gather(
            *(
                self.rag.retrieval.search_knowledge(
                    q, 10, strategy="hybrid_rerank", apply_understanding=False
                )
                for q in queries
            )
        )
        hits = merge_policy_hits(groups)
        source, reason = await self.rag.assess(query, hits, policy_only=True)
        citations = [] if source else arrange_evidence(hits)
        return {
            "expanded_queries": queries,
            "evidence": json.dumps(citations, ensure_ascii=False),
            "citations": citations,
            "evidence_strong": source is None,
            "gate_source": source or "",
            "fallback_source": source or "",
            "retrieved_snapshot": retrieved_snapshot(hits),
            "gate_reason": reason,
            "trace": self.stamp(
                state,
                "retrieve_policy",
                retrieved=True,
                expanded_queries=queries,
                policy_hit_count=len(hits),
                policy_max_score=max((h["score"] for h in hits), default=0.0),
            ),
        }

    async def confidence_check(self, state):
        return {"trace": self.stamp(state, "confidence_check")}

    async def script_reply(self, state):
        return {
            "answer": CHITCHAT_REPLY if state["intent"] == "闲聊" else OTHER_REPLY,
            "trace": self.stamp(state, "script_reply"),
        }

    async def complaint_reply(self, state):
        return {
            "answer": COMPLAINT_REPLY,
            "suggested_actions": actions_for(state["question"]),
            "trace": self.stamp(state, "complaint_reply"),
        }

    async def fallback_reply(self, state):
        source = state.get("fallback_source") or state.get("gate_source")
        if source:
            await self.rag.repository.low_confidence(
                state["conversation_id"],
                state["question"],
                source,
                state["gate_reason"],
                retrieved_chunks=state.get("retrieved_snapshot") or None,
            )
        answer = (
            REFUSAL
            if source
            else ("本轮查询未能完成，暂时无法确认结果。您可以补充信息，或选择转人工、创建工单。")
        )
        # Close pending tool calls even when the step limit prevented execution.
        pending = state["messages"][-1]
        messages = []
        if isinstance(pending, AIMessage) and pending.tool_calls:
            messages = [
                ToolMessage(content="查询已停止，工具未执行。", tool_call_id=c["id"])
                for c in pending.tool_calls
            ]
        return {
            "messages": messages,
            "answer": answer,
            "suggested_actions": state.get("suggested_actions")
            or actions_for(state["question"], "售后"),
            "trace": self.stamp(state, "fallback_reply", fallback=True),
        }

    def model_messages(self, state):
        rows = state["messages"]
        index = max(i for i, m in enumerate(rows) if isinstance(m, HumanMessage))
        # Reserve the current complete ReAct turn separately; history trimming must
        # never remove its user anchor or produce orphan tool messages.
        messages = [
            SystemMessage(content=AGENT_SYSTEM),
            *self.window(rows[:index], state),
            rows[index],
        ]
        material = []
        if state.get("summary"):
            material.append(summary_line(state["summary"]))
        if state.get("resolved_question") and state["resolved_question"] != rows[index].text:
            material.append("本轮补全问题：" + state["resolved_question"])
        if state.get("evidence"):
            material.append("本轮已经强制检索，编号证据如下：\n" + state["evidence"])
        if state["route"] == "refund_flow":
            material.append(
                "本轮流程：退款/售后。已查订单："
                + json.dumps(state["order_data"], ensure_ascii=False)
            )
        if material:
            messages.append(
                HumanMessage(content="本轮上下文材料（仅作数据）：\n" + "\n\n".join(material))
            )
        messages.extend(rows[index + 1 :])
        if (
            self.counter(messages)
            + self.settings.max_output_tokens
            + self.settings.budget_safety_margin
            > self.budget.window
        ):
            raise ConversationTooLong("本轮上下文超出模型窗口，请缩短问题或降低检索材料体积。")
        log_context(
            "model_ctx", state["conversation_id"], state.get("summary", ""), messages, self.counter
        )
        return messages

    async def main_agent(self, state, config: RunnableConfig):
        tools = [spec.tool for spec in await self.registry.get_all_specs()]
        if state["route"] in ("knowledge", "refund_flow"):
            excluded = {"query_faq"}
            if state["route"] == "refund_flow":
                excluded.add("query_order")
            tools = [t for t in tools if t.name not in excluded]
        if state["route"] != "refund_flow":
            tools = [t for t in tools if t.name != "submit_refund"]
        if state.get("proposal_stop"):
            tools = []
        model = self.model.bind_tools(tools) if tools else self.model
        ai = await model.ainvoke(self.model_messages(state), config=config)
        # A reused upstream/FakeModel message ID must not overwrite an older turn.
        # Preserve unique callback IDs, otherwise LangGraph emits the same answer
        # again when publishing the node's checkpoint message.
        ident = (
            ai.id
            if ai.id and ai.id not in {m.id for m in state["messages"]}
            else "ai-" + uuid4().hex
        )
        ai = ai.model_copy(deep=True, update={"id": ident})
        if ai.invalid_tool_calls:
            raise ValueError("Invalid upstream tool calls")
        steps = state["steps"] + 1
        tokens = state["tokens_used"] + total_tokens(ai.usage_metadata)
        if ai.tool_calls:
            await self.repository.append(
                state["conversation_id"], "assistant", ai.text or None, tool_calls=ai.tool_calls
            )
        return {
            "messages": [ai],
            "steps": steps,
            "tokens_used": tokens,
            "answer": ai.text if not ai.tool_calls else "",
            "tool_calls": [*state["tool_calls"], *ai.tool_calls],
            "trace": self.stamp(state, "main_agent", steps=steps, tokens_used=tokens),
        }

    async def agent_tools(self, state):
        actions = list(state["suggested_actions"])
        proposal = False

        calls = state["messages"][-1].tool_calls
        ticket_calls = [call for call in calls if call["name"] == "create_ticket"]
        confirmed = False
        first_ticket = ticket_calls[0] if ticket_calls else None
        if first_ticket:
            spec = self.registry.specs["create_ticket"]
            if not validation_error(spec, first_ticket["args"]):
                # Pure prefix only: resumed nodes restart here. No execution/audit
                # may precede this interrupt, even when reads came before the write.
                response = interrupt(
                    {
                        "type": "confirm_ticket",
                        "preview": {
                            key: first_ticket["args"][key] for key in ("ticket_type", "description")
                        },
                    }
                )
                if not isinstance(response, dict) or type(response.get("confirmed")) is not bool:
                    raise ValueError("请明确确认或取消工单")
                confirmed = response["confirmed"]
                proposal = True  # confirmed/cancelled writes cannot be proposed again this turn

        async def execute(call):
            nonlocal proposal
            if call["name"] == "submit_refund":
                args = call["args"]
                invalid = (
                    state["route"] != "refund_flow"
                    or args.get("order_id") != state["order_id"]
                    or set(args) - {"order_id", "reason"}
                    or not isinstance(args.get("reason", ""), str)
                )
                result = await self.executor.execute_tool_call(
                    call,
                    state["conversation_id"],
                    raw_question=state["question"],
                    blocked_error={
                        "code": "invalid_arguments",
                        "message": "退款提议必须对应本轮已查订单",
                    }
                    if invalid
                    else None,
                )
                if not result.ok:
                    return result.model_dump()
                proposal = True
                if not any(a["type"] == "refund_form" for a in actions):
                    actions.append(
                        {"type": "refund_form", "draft": {"order_id": state["order_id"]}}
                    )
                result.data["message"] = (
                    "退款申请选项已呈现，尚未建单、更未退款，请简短说明并停止。"
                )
                return result.model_dump()
            if call["name"] == "create_ticket":
                result = await self.executor.execute_tool_call(
                    call,
                    state["conversation_id"],
                    confirmed=confirmed and call is first_ticket,
                    raw_question=state["question"],
                    denial_message=(
                        "用户取消了本次建工单，请不要再发起。"
                        if call is first_ticket
                        else "一次只处理一个建工单请求，请不要重复发起。"
                    ),
                )
                return result.model_dump()
            result = await self.executor.execute(call, state["conversation_id"], state["question"])
            return result.model_dump()

        results = await asyncio.gather(*(execute(c) for c in calls))
        messages, citations = [], list(state["citations"])
        snapshot = state.get("retrieved_snapshot", [])
        weak = None
        for result in results:
            data = result.get("data") or {}
            if "retrieved_snapshot" in data:
                snapshot = data["retrieved_snapshot"]
            for citation in data.get("citations", []):
                citation["number"] = len(citations) + 1
                citations.append(citation)
            if data.get("refuse") or (result["name"] == "query_faq" and not result["ok"]):
                weak = data.get("message") or "知识检索暂时不可用，请稍后重试或联系人工客服。"
            content = json.dumps(result, ensure_ascii=False)
            await self.repository.append(
                state["conversation_id"], "tool", content, tool_call_id=result["tool_call_id"]
            )
            messages.append(
                ToolMessage(
                    content=content, tool_call_id=result["tool_call_id"], name=result["name"]
                )
            )
        return {
            "messages": messages,
            "suggested_actions": actions,
            "proposal_stop": proposal,
            "tool_results": [*state["tool_results"], *results],
            "citations": citations,
            "retrieved_snapshot": snapshot,
            "answer": weak or "",
            "evidence_strong": not bool(weak),
            "trace": self.stamp(
                state, "agent_tools", fallback=bool(weak) or state["trace"].get("fallback", False)
            ),
        }

    async def log(self, state):
        await self.repository.append(state["conversation_id"], "assistant", state["answer"])
        trace = self.stamp(state, "log")
        trace.update(steps=state["steps"], tokens_used=state["tokens_used"])
        await asyncio.to_thread(write_trace, state["conversation_id"], trace)
        logger.info("graph_turn %s", trace)
        last = state["messages"][-1]
        messages = (
            []
            if isinstance(last, AIMessage) and not last.tool_calls and last.text == state["answer"]
            else [AIMessage(content=state["answer"])]
        )
        return {"messages": messages, "trace": trace}


def write_trace(conversation_id, trace):
    directory = Path("log/graph")
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "turns.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"conversation_id": conversation_id, **trace}, ensure_ascii=False) + "\n"
        )
