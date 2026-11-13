"""The shapes the assistant's steps hand to one another (ARCHITECTURE §17.2, §17.3; DEC-64 to DEC-69).

Each step takes one of these and returns one, so plain code checks every hand-over. Two shapes are what the model may
send back, `ModelClassification` and `ModelRewrite`: they have no confidence, no reasons and no sources, because code
works those out (DEC-66, DEC-69), and anything else the model adds is dropped unread. Every other shape refuses an
unknown field. Nothing here reads or writes the database, and a trace holds no question or document text (DEC-13).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from po_dashboard.display import day

MAX_PARTS = 3  # more parts than this gets ONE_AT_A_TIME (DEC-66)
MAX_QUESTION = 1000  # characters: a question, not a pasted document
INSUFFICIENT = "Insufficient information in the current data"  # DEC-69, word for word
ONE_AT_A_TIME = "Please ask one question at a time"
BUSY = "The assistant is busy with other questions. Please ask again in a minute."  # DEC-68: a fourth waiting question


class Intent(StrEnum):
    ASK = "ask"  # a fact
    SUMMARIZE = "summarize"  # an overview of records or flags
    DRAFT = "draft"  # a message for a person to send
    CLARIFY = "clarify"  # something essential is missing
    UNSUPPORTED = "unsupported"  # would change a record or send mail, or is outside the app's records


WORKFLOWS = (Intent.ASK, Intent.SUMMARIZE, Intent.DRAFT)  # the intents that read records


class Side(StrEnum):
    PURCHASING = "purchasing"  # POs to vendors
    CUSTOMER = "customer"  # customer POs, sales invoices and QuickBooks uploads


class Role(StrEnum):
    ADMIN = "admin"
    BUYER = "buyer"


class Confidence(StrEnum):
    """Worked out from checks (rules and model agree, every identifier found in the words), never the model's own."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Reason(StrEnum):
    """Why a part got its intent. Fixed codes, so a trace or a test can name them without any of the question's text."""

    IDENTIFIER = "identifier"  # a pattern found an identifier in the words
    QUESTION_WORDS = "question_words"  # why, status, when, where, how many
    SUMMARY_WORDS = "summary_words"  # summarise, overview, list, all the
    DRAFT_WORDS = "draft_words"  # draft, write, a note to, an email to
    CHANGE_WORDS = "change_words"  # delete, update, approve, accept, close, cancel, send, pay, mark
    OUTSIDE_RECORDS = "outside_records"  # not about the app's records
    NO_RECORD_NAMED = "no_record_named"  # it needs a record and names none
    MORE_THAN_ONE_RECORD = "more_than_one_record"  # a reference that fits two or more records
    MODEL = "model"  # the rules could not decide, so the model did
    IDENTIFIER_DROPPED = "identifier_dropped"  # the model named an identifier the words do not hold (never invent)
    RULES_AND_MODEL_DIFFER = "rules_and_model_differ"


class Level(StrEnum):
    """How firm a finding is. A confirmed discrepancy is never mixed up with a preliminary alert (DEC-59)."""

    FACT = "fact"  # read from accepted records
    CONFIRMED = "confirmed"  # a confirmed discrepancy
    PRELIMINARY = "preliminary"  # a preliminary alert


class SourceKind(StrEnum):
    PO = "po"
    PO_LINE = "po_line"
    VENDOR = "vendor"
    CUSTOMER = "customer"
    CUSTOMER_PO = "customer_po"
    SALES_INVOICE = "sales_invoice"
    UPLOAD = "upload"
    DOCUMENT = "document"  # stored email or attachment text (Stage 2)


class Step(StrEnum):
    REFERENCE_CHECK = "reference_check"
    REWRITE = "rewrite"
    CLASSIFY = "classify"
    ROUTE = "route"
    READ = "read"
    EVIDENCE_CHECK = "evidence_check"
    ANSWER = "answer"


class _Contract(BaseModel):
    """A hand-over between the assistant's own steps: an unknown field is a mistake, and nothing changes later."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class _Reply(BaseModel):
    """What the model sends back. Anything it adds beyond the schema is dropped, never trusted."""

    model_config = ConfigDict(extra="ignore", frozen=True)


# ---------------------------------------------------------------------------------------------------------------------
# Sources and the turn


class SourceRef(_Contract):
    """A record an answer cites: the only context a later question may lean on, never an answer's words (DEC-67)."""

    kind: SourceKind
    key: str = Field(min_length=1, max_length=200)  # as a person writes it: PO-1005, CP-2001, 3001, a vendor's name
    line_no: int | None = Field(default=None, ge=1)  # for a PO line
    customer: str | None = None  # a customer PO number is unique only within its customer (DEC-57)
    period: tuple[date, date] | None = None  # an upload's period

    @model_validator(mode="after")
    def _fits_its_kind(self) -> Self:
        if (self.line_no is not None) != (self.kind is SourceKind.PO_LINE):
            raise ValueError("a line number belongs to a PO line, and a PO line needs one")
        if self.period is not None and self.kind is not SourceKind.UPLOAD:
            raise ValueError("only an upload has a period")
        if self.period is not None and self.period[0] > self.period[1]:
            raise ValueError("a period ends on or after its start")
        return self

    @property
    def label(self) -> str:
        """How an answer names it: "PO-1005 line 2", "Customer PO CP-2001 (Northwind Racing)"."""
        match self.kind:
            case SourceKind.PO:
                text = self.key
            case SourceKind.PO_LINE:
                text = f"{self.key} line {self.line_no}"
            case SourceKind.VENDOR:
                text = f"Vendor {self.key}"
            case SourceKind.CUSTOMER:
                text = f"Customer {self.key}"
            case SourceKind.CUSTOMER_PO:
                text = f"Customer PO {self.key}"
            case SourceKind.SALES_INVOICE:
                text = f"Sales invoice {self.key}"
            case SourceKind.UPLOAD:
                text = f"Upload {self.key}"
            case SourceKind.DOCUMENT:
                text = f"Document {self.key}"
        if self.customer:
            text += f" ({self.customer})"
        if self.period:
            text += f" ({day(self.period[0])} to {day(self.period[1])})"
        return text


class Turn(_Contract):
    """One question, who asks it, and the records the conversation's earlier answers cited, newest first."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    role: Role
    context: tuple[SourceRef, ...] = ()

    @model_validator(mode="after")
    def _has_words(self) -> Self:
        if not self.question.strip():
            raise ValueError("a question needs words")
        return self


# ---------------------------------------------------------------------------------------------------------------------
# Rewriting (DEC-67)


class RewriteHow(StrEnum):
    UNCHANGED = "unchanged"  # no reference back, no history, or history about something else
    SUBSTITUTED = "substituted"  # code put the one record that fits in place of the reference
    MODEL = "model"  # the model's rewrite, kept because it passed every check
    KEPT = "kept"  # the model's rewrite failed a check, so the question is kept as written
    CLARIFY = "clarify"  # two or more records fit: the person is asked which


class Rewrite(_Contract):
    original: str = Field(min_length=1)
    question: str = Field(min_length=1)  # what classification reads
    how: RewriteHow
    candidates: tuple[SourceRef, ...] = ()  # the records the reference could mean, when there is more than one

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.how in (RewriteHow.UNCHANGED, RewriteHow.KEPT, RewriteHow.CLARIFY) and self.question != self.original:
            raise ValueError(f"a question {self.how} is the question as written")
        if (self.how is RewriteHow.CLARIFY) != (len(self.candidates) >= 2):
            raise ValueError("a question back needs two or more candidates, and only a question back has them")
        return self


class ModelRewrite(_Reply):
    """The model's reply to the rewriting prompt: the question, standing on its own."""

    question: str = Field(min_length=1, max_length=MAX_QUESTION)


# ---------------------------------------------------------------------------------------------------------------------
# Classification (DEC-66)


class Identifiers(_Reply):
    """What a part of a question names. Also part of the model's reply, so extra keys are dropped. Whether a record
    exists is found by the read step, never here."""

    po_numbers: tuple[str, ...] = ()
    customer_po_numbers: tuple[str, ...] = ()
    invoice_numbers: tuple[str, ...] = ()
    vendors: tuple[str, ...] = ()
    customers: tuple[str, ...] = ()
    dates: tuple[date, ...] = ()
    side: Side | None = None

    @property
    def named(self) -> tuple[str, ...]:
        """Every identifier and name, for the check that each is in the person's words or the verified context."""
        return (*self.po_numbers, *self.customer_po_numbers, *self.invoice_numbers, *self.vendors, *self.customers)

    @property
    def empty(self) -> bool:
        return not (self.named or self.dates)


class Part(_Contract):
    """One part of a question, as classification left it."""

    intent: Intent
    words: str = Field(min_length=1)  # the part as the person wrote it (after any rewrite)
    identifiers: Identifiers = Identifiers()
    missing: tuple[str, ...] = ()  # for CLARIFY: what is needed, put as the question back ("which PO?")
    reasons: tuple[Reason, ...] = Field(min_length=1)
    confidence: Confidence

    @model_validator(mode="after")
    def _clarify_says_what_is_missing(self) -> Self:
        if (self.intent is Intent.CLARIFY) != bool(self.missing):
            raise ValueError("a CLARIFY part says what is missing, and only a CLARIFY part does")
        return self


class Classification(_Contract):
    """Up to 3 parts in the person's order, or none when there were more (the answer is then ONE_AT_A_TIME)."""

    parts: tuple[Part, ...] = Field(max_length=MAX_PARTS)
    too_many_parts: bool = False

    @model_validator(mode="after")
    def _parts_or_too_many(self) -> Self:
        if bool(self.parts) == self.too_many_parts:
            raise ValueError("a classification has parts, or says there were too many, not both")
        return self

    @property
    def needs_clarify(self) -> bool:
        """Any part needing CLARIFY means the assistant asks first and runs nothing (DEC-66)."""
        return any(p.intent is Intent.CLARIFY for p in self.parts)


class ModelPart(_Reply):
    intent: Intent
    words: str = Field(min_length=1)
    identifiers: Identifiers = Identifiers()
    missing: tuple[str, ...] = ()


class ModelClassification(_Reply):
    """The model's reply to the classification prompt. Code checks it: more than 3 parts is ONE_AT_A_TIME, an
    identifier not in the words is dropped and its part becomes CLARIFY, and code sets the confidence."""

    parts: tuple[ModelPart, ...] = Field(min_length=1)


# ---------------------------------------------------------------------------------------------------------------------
# Workflows: what is asked of the read step, and what it found


class Finding(_Contract):
    """One thing the records say, in a template's words with figures copied from the backend (DEC-69)."""

    text: str = Field(min_length=1)
    level: Level = Level.FACT
    sources: tuple[SourceRef, ...] = Field(min_length=1)


class Evidence(_Contract):
    """What the read step found for one part. A conflict is reported, never settled; a limit is what the answer does
    not cover (the period the uploads cover, something not available yet)."""

    findings: tuple[Finding, ...] = ()
    missing: tuple[str, ...] = ()  # what the records do not hold that the part needs: what would settle it
    conflicts: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()


class Draft(_Contract):
    """A message for a person to copy. Marked DRAFT, editable, never sent, and addressed only to the contact on the
    vendor or customer record (DEC-40 (3), DEC-69)."""

    marked: Literal["DRAFT"] = "DRAFT"
    to: str | None  # the contact's address on record; None when there is none, and `unfilled` says so
    subject: str = Field(min_length=1)
    body: str = Field(min_length=1)
    unfilled: tuple[str, ...] = ()  # what it could not fill in


class WorkflowRequest(_Contract):
    intent: Literal[Intent.ASK, Intent.SUMMARIZE, Intent.DRAFT]
    part: Part
    role: Role
    earlier: Evidence | None = None  # a DRAFT after an ASK in the same question uses that ASK's evidence (DEC-66)

    @model_validator(mode="after")
    def _part_matches(self) -> Self:
        if self.part.intent is not self.intent:
            raise ValueError("the request's intent is its part's intent")
        if self.earlier is not None and self.intent is not Intent.DRAFT:
            raise ValueError("only a draft carries an earlier part's evidence")
        return self


class WorkflowResult(_Contract):
    intent: Literal[Intent.ASK, Intent.SUMMARIZE, Intent.DRAFT]
    evidence: Evidence
    draft: Draft | None = None

    @model_validator(mode="after")
    def _draft_only_for_draft(self) -> Self:
        if self.draft is not None and self.intent is not Intent.DRAFT:
            raise ValueError("only a DRAFT result holds a draft")
        return self


# ---------------------------------------------------------------------------------------------------------------------
# The answer and its trace


class ModelCall(_Contract):
    """One call through `models/llm.py`, kept for the trace (DEC-68). Never the prompt or the reply."""

    step: Step
    prompt_version: str = Field(min_length=1)  # "intent_classification v1"
    seconds: float = Field(ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)  # Ollama's own counts
    reply_tokens: int | None = Field(default=None, ge=0)
    outcome: Literal["ok", "bad_answer", "unavailable", "busy"]


class StepRun(_Contract):
    step: Step
    seconds: float = Field(ge=0)


class Trace(_Contract):
    """The steps that ran and every model call, with their seconds and tokens. No question or document text."""

    steps: tuple[StepRun, ...] = ()
    model_calls: tuple[ModelCall, ...] = ()

    @property
    def seconds(self) -> float:
        return sum(s.seconds for s in self.steps)

    @property
    def model_seconds(self) -> float:
        return sum(c.seconds for c in self.model_calls)


class AnswerKind(StrEnum):
    ANSWER = "answer"  # findings with their sources (and a draft, for DRAFT)
    INSUFFICIENT = "insufficient"  # INSUFFICIENT, and what would settle it
    CLARIFY = "clarify"  # one question back
    REFUSED = "refused"  # UNSUPPORTED: what it cannot do, said in words
    ONE_AT_A_TIME = "one_at_a_time"
    BUSY = "busy"


class PartAnswer(_Contract):
    intent: Intent | None  # None for ONE_AT_A_TIME and BUSY, which answer the whole question
    kind: AnswerKind
    text: str = Field(min_length=1)
    findings: tuple[Finding, ...] = ()
    draft: Draft | None = None
    limits: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.kind is AnswerKind.ANSWER and not self.findings:
            raise ValueError("an answer has findings; with none it is INSUFFICIENT")
        if self.kind is not AnswerKind.ANSWER and (self.findings or self.draft):
            raise ValueError("only an answer carries findings or a draft")
        if self.kind is AnswerKind.INSUFFICIENT and not self.text.startswith(INSUFFICIENT):
            raise ValueError(f"an insufficient answer starts with {INSUFFICIENT!r}")
        if self.draft is not None and self.intent is not Intent.DRAFT:
            raise ValueError("only a DRAFT part carries a draft")
        if (self.intent is None) != (self.kind in (AnswerKind.ONE_AT_A_TIME, AnswerKind.BUSY)):
            raise ValueError("only ONE_AT_A_TIME and BUSY answer without an intent")
        return self


class Answer(_Contract):
    """What the person reads: one answer per part, in their order, each finding with its sources."""

    question: str = Field(min_length=1)  # the question as written
    question_used: str = Field(min_length=1)  # after any rewrite
    parts: tuple[PartAnswer, ...] = Field(min_length=1, max_length=MAX_PARTS)
    trace: Trace = Trace()

    @property
    def text(self) -> str:
        return "\n\n".join(p.text for p in self.parts)

    @property
    def sources(self) -> tuple[SourceRef, ...]:
        """Every record the findings cite, once each, in order: the links shown, and the next question's context."""
        seen: dict[SourceRef, None] = {}
        for part in self.parts:
            for finding in part.findings:
                for source in finding.sources:
                    seen.setdefault(source, None)
        return tuple(seen)
