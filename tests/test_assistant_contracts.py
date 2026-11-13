"""The assistant's contracts (PD-9.1, ARCHITECTURE §17.3; DEC-66, DEC-67, DEC-69): every hand-over between its steps is
checked by plain code, and what the model sends back cannot set what code works out."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from po_dashboard.assistant.contracts import (
    INSUFFICIENT,
    MAX_QUESTION,
    Answer,
    AnswerKind,
    Classification,
    Confidence,
    Draft,
    Evidence,
    Finding,
    Identifiers,
    Intent,
    Level,
    ModelCall,
    ModelClassification,
    ModelRewrite,
    Part,
    PartAnswer,
    Reason,
    Rewrite,
    RewriteHow,
    Role,
    SourceKind,
    SourceRef,
    Step,
    StepRun,
    Trace,
    Turn,
    WorkflowRequest,
    WorkflowResult,
)

PO = SourceRef(kind=SourceKind.PO, key="PO-1005")
LINE = SourceRef(kind=SourceKind.PO_LINE, key="PO-1005", line_no=1)
OTHER = SourceRef(kind=SourceKind.PO, key="PO-1007")


def part(intent: Intent = Intent.ASK, missing: tuple[str, ...] = ()) -> Part:
    return Part(
        intent=intent,
        words="Why is PO-1005 flagged?",
        identifiers=Identifiers(po_numbers=("PO-1005",)),
        missing=missing,
        reasons=(Reason.IDENTIFIER, Reason.QUESTION_WORDS),
        confidence=Confidence.HIGH,
    )


def finding(*sources: SourceRef, level: Level = Level.FACT) -> Finding:
    return Finding(text="PO-1005 line 1 is 3 days late.", level=level, sources=sources or (LINE,))


def test_the_model_cannot_set_confidence_reasons_or_sources() -> None:
    reply = ModelClassification.model_validate_json(
        '{"parts": [{"intent": "ask", "words": "Why is PO-1005 flagged?", "confidence": "high",'
        ' "reasons": ["identifier"], "sources": [{"kind": "po", "key": "PO-9999"}],'
        ' "identifiers": {"po_numbers": ["PO-1005"], "secret": "x"}}], "confidence": 0.99}'
    )
    dumped = reply.model_dump()
    assert dumped == {
        "parts": ({
            "intent": Intent.ASK, "words": "Why is PO-1005 flagged?", "missing": (),
            "identifiers": {"po_numbers": ("PO-1005",), "customer_po_numbers": (), "invoice_numbers": (),
                            "vendors": (), "customers": (), "dates": (), "side": None},
        },)
    }  # fmt: skip
    schema = ModelClassification.model_json_schema()
    part_schema = schema["$defs"]["ModelPart"]
    assert "confidence" not in part_schema["properties"] and "reasons" not in part_schema["properties"]
    assert set(schema["$defs"]["Intent"]["enum"]) == {"ask", "summarize", "draft", "clarify", "unsupported"}
    rewrite = ModelRewrite.model_validate_json('{"question": "What about PO-1005\'s invoice?", "trusted": true}')
    assert rewrite.model_dump() == {"question": "What about PO-1005's invoice?"}


def test_a_model_reply_with_an_unknown_intent_or_no_parts_is_refused() -> None:
    with pytest.raises(ValidationError):
        ModelClassification.model_validate_json('{"parts": [{"intent": "delete", "words": "delete PO-1005"}]}')
    with pytest.raises(ValidationError):
        ModelClassification.model_validate_json('{"parts": []}')
    with pytest.raises(ValidationError):
        ModelRewrite.model_validate_json('{"question": ""}')


def test_a_hand_over_between_steps_refuses_an_unknown_field_and_cannot_be_changed() -> None:
    with pytest.raises(ValidationError):
        Part(**part().model_dump(), sql="DELETE FROM purchase_order")
    with pytest.raises(ValidationError):
        part().intent = Intent.DRAFT  # type: ignore[misc]
    with pytest.raises(ValidationError):  # a trace never keeps the prompt or the reply (DEC-13)
        ModelCall(step=Step.CLASSIFY, prompt_version="intent_classification v1", seconds=1.0, outcome="ok",
                  reply="the model's words")  # type: ignore[call-arg]  # fmt: skip


def test_up_to_three_parts_or_too_many() -> None:
    assert len(Classification(parts=(part(),) * 3).parts) == 3
    assert Classification(parts=(), too_many_parts=True).parts == ()
    with pytest.raises(ValidationError):
        Classification(parts=(part(),) * 4)
    with pytest.raises(ValidationError):
        Classification(parts=(part(),), too_many_parts=True)
    with pytest.raises(ValidationError):
        Classification(parts=())


def test_a_clarify_part_says_what_is_missing_and_stops_the_others() -> None:
    with pytest.raises(ValidationError):
        part(Intent.CLARIFY)
    with pytest.raises(ValidationError):
        part(Intent.ASK, missing=("which PO?",))
    with pytest.raises(ValidationError):
        Part(intent=Intent.ASK, words="status?", reasons=(), confidence=Confidence.LOW)
    asked = Classification(parts=(part(Intent.UNSUPPORTED), part(Intent.CLARIFY, missing=("which PO?",)), part()))
    assert asked.needs_clarify
    assert not Classification(parts=(part(Intent.UNSUPPORTED), part())).needs_clarify


def test_a_rewrite_keeps_the_question_as_written_unless_a_record_was_put_in() -> None:
    kept = "What about its invoice?"
    assert Rewrite(original=kept, question=kept, how=RewriteHow.UNCHANGED).question == kept
    put_in = Rewrite(original=kept, question="What about PO-1005's invoice?", how=RewriteHow.SUBSTITUTED)
    assert put_in.question != kept
    for how in (RewriteHow.UNCHANGED, RewriteHow.KEPT):
        with pytest.raises(ValidationError):
            Rewrite(original=kept, question="What about PO-1005's invoice?", how=how)
    assert Rewrite(original=kept, question=kept, how=RewriteHow.CLARIFY, candidates=(PO, OTHER)).candidates
    with pytest.raises(ValidationError):  # one candidate is substituted by code, never asked about
        Rewrite(original=kept, question=kept, how=RewriteHow.CLARIFY, candidates=(PO,))
    with pytest.raises(ValidationError):
        Rewrite(original=kept, question="What about PO-1005's invoice?", how=RewriteHow.SUBSTITUTED,
                candidates=(PO, OTHER))  # fmt: skip


def test_a_question_is_words_and_not_a_pasted_document() -> None:
    assert Turn(question="Why is PO-1005 flagged?", role=Role.BUYER, context=(PO,)).context == (PO,)
    for bad in ("", "   \n", "x" * (MAX_QUESTION + 1)):
        with pytest.raises(ValidationError):
            Turn(question=bad, role=Role.BUYER)
    with pytest.raises(ValidationError):
        Turn(question="Who signed in today?", role="owner")  # type: ignore[arg-type]


def test_a_source_fits_its_kind_and_names_itself() -> None:
    assert LINE.label == "PO-1005 line 1"
    order = SourceRef(kind=SourceKind.CUSTOMER_PO, key="CP-2002", customer="Tailspin Motorsport")
    assert order.label == "Customer PO CP-2002 (Tailspin Motorsport)"
    upload = SourceRef(kind=SourceKind.UPLOAD, key="sales_aug.csv", period=(date(2026, 8, 1), date(2026, 8, 31)))
    assert upload.label == "Upload sales_aug.csv (08/01/2026 to 08/31/2026)"
    with pytest.raises(ValidationError):
        SourceRef(kind=SourceKind.PO, key="PO-1005", line_no=1)
    with pytest.raises(ValidationError):
        SourceRef(kind=SourceKind.PO_LINE, key="PO-1005")
    with pytest.raises(ValidationError):
        SourceRef(kind=SourceKind.PO, key="PO-1005", period=(date(2026, 8, 1), date(2026, 8, 31)))
    with pytest.raises(ValidationError):
        SourceRef(kind=SourceKind.UPLOAD, key="sales_aug.csv", period=(date(2026, 8, 31), date(2026, 8, 1)))


def test_an_answer_is_findings_with_sources_or_says_insufficient() -> None:
    with pytest.raises(ValidationError):
        Finding(text="PO-1005 is late.", sources=())
    with pytest.raises(ValidationError):
        PartAnswer(intent=Intent.ASK, kind=AnswerKind.ANSWER, text="PO-1005 is late.")
    with pytest.raises(ValidationError):
        PartAnswer(intent=Intent.ASK, kind=AnswerKind.INSUFFICIENT, text="I think it is late.")
    with pytest.raises(ValidationError):
        PartAnswer(intent=Intent.UNSUPPORTED, kind=AnswerKind.REFUSED, text="I cannot delete.", findings=(finding(),))
    with pytest.raises(ValidationError):
        PartAnswer(intent=None, kind=AnswerKind.REFUSED, text="I cannot delete.")
    none = PartAnswer(intent=Intent.ASK, kind=AnswerKind.INSUFFICIENT, text=f"{INSUFFICIENT}: no PO-1099 on file.")
    assert none.text.startswith(INSUFFICIENT)


def test_an_answer_lists_each_cited_record_once_in_order() -> None:
    answer = Answer(
        question="Why is PO-1005 flagged, and what about PO-1007?",
        question_used="Why is PO-1005 flagged, and what about PO-1007?",
        parts=(
            PartAnswer(intent=Intent.ASK, kind=AnswerKind.ANSWER, text="a", findings=(finding(LINE), finding(PO))),
            PartAnswer(intent=Intent.ASK, kind=AnswerKind.ANSWER, text="b", findings=(finding(OTHER, LINE),)),
            PartAnswer(intent=Intent.UNSUPPORTED, kind=AnswerKind.REFUSED, text="c"),
        ),
    )
    assert answer.sources == (LINE, PO, OTHER)
    assert answer.text == "a\n\nb\n\nc"


def test_a_draft_is_marked_draft_has_no_way_to_send_and_belongs_to_a_draft() -> None:
    draft = Draft(to=None, subject="PO-1005 line 1", body="Hello,", unfilled=("no contact on file",))
    assert draft.marked == "DRAFT"
    assert not {"send", "sent", "cc", "bcc", "send_at"} & set(Draft.model_fields)
    with pytest.raises(ValidationError):
        Draft(marked="SENT", to=None, subject="s", body="b")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        WorkflowResult(intent=Intent.ASK, evidence=Evidence(), draft=draft)
    with pytest.raises(ValidationError):
        PartAnswer(intent=Intent.ASK, kind=AnswerKind.ANSWER, text="t", findings=(finding(),), draft=draft)
    done = PartAnswer(intent=Intent.DRAFT, kind=AnswerKind.ANSWER, text="t", findings=(finding(),), draft=draft)
    assert done.draft == draft


def test_a_workflow_request_matches_its_part() -> None:
    assert WorkflowRequest(intent=Intent.ASK, part=part(), role=Role.BUYER).part.intent is Intent.ASK
    with pytest.raises(ValidationError):
        WorkflowRequest(intent=Intent.SUMMARIZE, part=part(), role=Role.BUYER)
    with pytest.raises(ValidationError):  # CLARIFY and UNSUPPORTED never reach a workflow
        WorkflowRequest(intent=Intent.UNSUPPORTED, part=part(Intent.UNSUPPORTED), role=Role.BUYER)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):  # only a DRAFT after an ASK carries that ASK's evidence
        WorkflowRequest(intent=Intent.ASK, part=part(), role=Role.BUYER, earlier=Evidence())
    later = WorkflowRequest(intent=Intent.DRAFT, part=part(Intent.DRAFT), role=Role.BUYER, earlier=Evidence())
    assert later.earlier == Evidence()


def test_a_trace_adds_up_seconds_and_keeps_token_counts() -> None:
    trace = Trace(
        steps=(StepRun(step=Step.CLASSIFY, seconds=12.5), StepRun(step=Step.READ, seconds=0.25)),
        model_calls=(ModelCall(step=Step.CLASSIFY, prompt_version="intent_classification v1", seconds=12.0,
                               prompt_tokens=850, reply_tokens=40, outcome="ok"),),
    )  # fmt: skip
    assert trace.seconds == 12.75
    assert trace.model_seconds == 12.0
    with pytest.raises(ValidationError):
        StepRun(step=Step.READ, seconds=-1)
