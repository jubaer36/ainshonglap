"""Canned Q&A data standing in for real retrieval + LLM generation.

This is the single source of truth for demo data: the frontend has no
local copy and always gets this through POST /api/query. Swapping this
module's internals for a real retrieval/LLM pipeline later requires no
change to the route or to the frontend.

The two seeded answers and the citation text are ported from the
sample.html mockup's `citations` object and its two chat turns.
"""

from __future__ import annotations

import uuid

from app.models.schemas import (
    AssistantMessage,
    BulletsBlock,
    Citation,
    CopyQuickAction,
    FollowupQuickAction,
    ParagraphBlock,
    PreviewQuickAction,
    Reference,
    StepsBlock,
    StrongSegment,
    TextSegment,
)
from app.models.schemas import CitationSegment as Cite

CITATIONS: dict[str, Citation] = {
    "crpc-154": Citation(
        key="crpc-154",
        title="CrPC Section 154 • Cognizable Offence Information",
        badge="CrPC Act V 1898",
        source="Ministry of Law BDLaws Archive • Act V of 1898",
        text=(
            "\"Every information relating to the commission of a cognizable "
            "offence given to an officer in charge of a police station shall "
            "be reduced to writing, read over to the informant, and signed "
            "by them. A copy shall be given forthwith, free of cost.\""
        ),
        note=(
            "Mandatory initial procedure: the officer cannot refuse or "
            "condition recording upon preliminary inquiry once a cognizable "
            "offence is disclosed."
        ),
    ),
    "prb-243": Citation(
        key="prb-243",
        title="Police Regulations Bengal • Rule 243 (FIR Protocol)",
        badge="PRB Rule 243",
        source="Police Regulations of Bengal • Thana Administration",
        text=(
            "\"First Information Reports under Section 154 CrPC must be "
            "recorded immediately in Bangladesh Form No. 5349. The substance "
            "must be entered in the General Station Diary without delay.\""
        ),
        note=(
            "Requires immediate carbon copying and dispatch to the relevant "
            "Chief Judicial Magistrate within 24 hours."
        ),
    ),
    "crpc-156": Citation(
        key="crpc-156",
        title="CrPC Section 156(1) • Investigation into Cognizable Cases",
        badge="CrPC Act V 1898",
        source="Ministry of Law BDLaws Archive • Section 156",
        text=(
            "\"Any officer in charge of a police station may, without the "
            "order of a Magistrate, investigate any cognizable case which a "
            "Court having jurisdiction over the local area would have power "
            "to try.\""
        ),
        note="Confers statutory investigative jurisdiction automatically upon registration of the FIR.",
    ),
    "comp-2": Citation(
        key="comp-2",
        title="Companies Act 1994 • Section 2(1) (Definition of Company)",
        badge="Act XVIII 1994",
        source="Ministry of Law BDLaws • Companies Act 1994",
        text=(
            "\"'Company' means a company formed and registered under this "
            "Act or an existing company. Corporate personality requires "
            "lawful certification under the Registrar of Joint Stock "
            "Companies.\""
        ),
        note=(
            "Autonomous code arrangements without natural-person promoters "
            "registered with RJSC cannot be recognized as corporate persons."
        ),
    ),
    "csa-2023": Citation(
        key="csa-2023",
        title="Cyber Security Act 2023 • Digital Offence Jurisdiction",
        badge="CSA Act 2023",
        source="National Digital Security Framework • Bangladesh Gazette",
        text=(
            "\"Enacts liability for unauthorized access, data alteration, "
            "financial deceit, and fraud orchestrated over digital networks "
            "or computer systems.\""
        ),
        note=(
            "Used in financial crime investigations where smart contracts or "
            "deceptive tokens are promoted within Bangladesh jurisdiction."
        ),
    ),
    "penal-302": Citation(
        key="penal-302",
        title="Penal Code 1860 • Section 302 (Punishment for Murder)",
        badge="Penal Code XLV",
        source="The Penal Code 1860 • Act XLV of 1860",
        text=(
            "\"Whoever commits murder shall be punished with death, or "
            "imprisonment for life, and shall also be liable to fine.\""
        ),
        note=(
            "Requires proof beyond reasonable doubt, corroborated by medical "
            "evidence and eyewitness testimony."
        ),
    ),
    "evidence-25": Citation(
        key="evidence-25",
        title="Evidence Act 1872 • Section 25 (Confession to Police)",
        badge="Act I 1872",
        source="The Evidence Act 1872 • Section 25",
        text='"No confession made to a police-officer shall be proved as against a person accused of any offence."',
        note=(
            "Absolute evidentiary exclusion. Confessions must be recorded "
            "before a Magistrate under Section 164 CrPC."
        ),
    ),
}

_FIR_MESSAGE = AssistantMessage(
    id=str(uuid.uuid4()),
    heading="AinShonglap Plain Legal Guide",
    subheading="Hover citations to preview text",
    blocks=[
        ParagraphBlock(
            segments=[
                TextSegment(
                    text=(
                        "When you report a serious crime (a cognizable "
                        "offence) to a police station in Bangladesh, the "
                        "process is primarily governed by "
                    )
                ),
                Cite(key="crpc-154", label="CrPC Section 154"),
                TextSegment(text=" alongside police administrative standards in "),
                Cite(key="prb-243", label="PRB Rule 243"),
                TextSegment(text="."),
            ]
        ),
        StepsBlock(
            items=[
                [
                    StrongSegment(text="The Police must write it down: "),
                    TextSegment(
                        text=(
                            "if you explain your complaint verbally, the "
                            "Officer-in-Charge (O.C.) must write it down "
                            "verbatim or have an officer record it."
                        )
                    ),
                ],
                [
                    StrongSegment(text="Read back & Sign: "),
                    TextSegment(
                        text=(
                            "they must read the complaint back to you so you "
                            "can confirm it, then you sign or give your "
                            "thumbprint."
                        )
                    ),
                ],
                [
                    StrongSegment(text="Case Entry in Thana Diary: "),
                    TextSegment(
                        text=(
                            "the officer must record it right away in the "
                            "Thana's official register (General Diary) "
                            "without delay."
                        )
                    ),
                ],
                [
                    StrongSegment(text="Free Copy: "),
                    TextSegment(
                        text="by law, you are entitled to receive a free certified copy of the FIR immediately."
                    ),
                ],
            ]
        ),
        ParagraphBlock(
            segments=[
                TextSegment(
                    text=(
                        "Once the FIR is registered, the police immediately "
                        "acquire the authority to launch an investigation "
                        "under "
                    )
                ),
                Cite(key="crpc-156", label="CrPC Section 156(1)"),
                TextSegment(text=" without needing prior permission from a Magistrate."),
            ]
        ),
    ],
    citations={k: CITATIONS[k] for k in ("crpc-154", "prb-243", "crpc-156")},
    references=[
        Reference(
            title="Section 154 — Information in Cognizable Cases",
            subtitle="BDLaws Code of Criminal Procedure 1898, p. 48",
            url="http://bdlaws.minlaw.gov.bd/act-75/section-31580.html",
            icon="menu_book",
            icon_color="amber",
        ),
        Reference(
            title="Police Regulations of Bengal (PRB) — Rule 243",
            subtitle="Mandatory FIR Recording & Thana Entry Procedure",
            url="http://bdlaws.minlaw.gov.bd/act-75.html",
            icon="local_police",
            icon_color="blue",
        ),
        Reference(
            title="Section 156(1) — Investigation into Cognizable Cases",
            subtitle="Police investigative mandate without Magistrate order",
            url="http://bdlaws.minlaw.gov.bd/act-75/section-31582.html",
            icon="search",
            icon_color="emerald",
        ),
    ],
    quick_actions=[
        CopyQuickAction(
            label="Copy Summary",
            copy_text=(
                "When filing an FIR in Bangladesh:\n"
                "1. The Police must write it down if explained verbally (CrPC S. 154).\n"
                "2. Read back & Sign: They must read it back to you, and you sign or provide thumbprint.\n"
                "3. Case Entry: It must be logged immediately into the Thana General Diary (PRB Rule 243).\n"
                "4. Free Copy: You are entitled to an immediate free certified copy."
            ),
        ),
        PreviewQuickAction(label="Preview S. 154", citation_key="crpc-154"),
    ],
)

_DAO_MESSAGE = AssistantMessage(
    id=str(uuid.uuid4()),
    heading="AinShonglap Plain Legal Guide",
    subheading="Digital & Corporate Scope",
    blocks=[
        ParagraphBlock(
            segments=[
                TextSegment(
                    text=(
                        "No statutory law in Bangladesh currently recognizes "
                        "DAOs as separate registered legal entities. Here is "
                        "what current legislation dictates in practical terms:"
                    )
                )
            ]
        ),
        BulletsBlock(
            items=[
                [
                    StrongSegment(text="Registration Requirement: "),
                    TextSegment(text="under "),
                    Cite(key="comp-2", label="Companies Act S. 2(1)"),
                    TextSegment(
                        text=(
                            ", a company must be formally incorporated with "
                            "the Registrar of Joint Stock Companies and Firms "
                            "(RJSC). A decentralized protocol does not "
                            "satisfy this requirement."
                        )
                    ),
                ],
                [
                    StrongSegment(text="Individual Liability: "),
                    TextSegment(
                        text=(
                            "while the algorithm itself cannot be put on "
                            "trial, the individuals who organize or profit "
                            "from unlawful schemes can still be prosecuted "
                            "under the "
                        )
                    ),
                    Cite(key="csa-2023", label="Cyber Security Act 2023"),
                    TextSegment(text=" or the Penal Code."),
                ],
            ]
        ),
    ],
    citations={k: CITATIONS[k] for k in ("comp-2", "csa-2023")},
    references=[],
    quick_actions=[
        FollowupQuickAction(
            label="Ask about Electronic Evidence",
            question="What constitutes admissible electronic evidence under Section 65B of the Evidence Act?",
        ),
    ],
)

_UNSUPPORTED_MESSAGE = AssistantMessage(
    id=str(uuid.uuid4()),
    heading="AinShonglap Plain Legal Guide",
    subheading="Outside current scope",
    unsupported=True,
    blocks=[
        ParagraphBlock(
            segments=[
                TextSegment(
                    text=(
                        "This question can't be answered from the legal "
                        "sources currently ingested into AinShonglap "
                        "(the Code of Criminal Procedure, 1898 and a small "
                        "set of related acts). It may be outside that scope, "
                        "too ambiguous to map to a specific provision, or "
                        "simply not something this demo's mock data covers "
                        "yet."
                    )
                )
            ]
        ),
        ParagraphBlock(
            segments=[
                TextSegment(
                    text=(
                        "Try rephrasing with a specific act, section, or "
                        "procedure (e.g. \"FIR recording under CrPC\" or "
                        "\"bail under Section 498\"), or ask one of the "
                        "sidebar's suggested topics."
                    )
                )
            ]
        ),
    ],
    citations={},
    references=[],
    quick_actions=[],
)


def get_response(question: str) -> AssistantMessage:
    """Keyword-match the seeded demo answers; fall back to 'unsupported'.

    Placeholder for real retrieval + LLM generation. Always returns a
    fresh copy (new id) so repeated identical questions don't collide in
    the frontend's React key space.
    """

    q = question.lower()

    if any(term in q for term in ("fir", "154", "cognizable", "thana", "first information")):
        return _FIR_MESSAGE.model_copy(update={"id": str(uuid.uuid4())}, deep=True)

    if any(term in q for term in ("dao", "decentralized", "smart contract", "companies act")):
        return _DAO_MESSAGE.model_copy(update={"id": str(uuid.uuid4())}, deep=True)

    return _UNSUPPORTED_MESSAGE.model_copy(update={"id": str(uuid.uuid4())}, deep=True)
