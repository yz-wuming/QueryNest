"""Citation system tests (pure Python)."""

from querynest.core.models import Citation, ContextItem, ContentType
from querynest.query.citation import CitationBuilder


def _hit(document="paper.pdf", page=2, ctype="table", chunk="c1", score=0.9):
    return {
        "document_name": document,
        "document_id": "doc-1",
        "page": page,
        "content_type": ctype,
        "chunk_id": chunk,
        "source": "/data/paper.pdf",
        "score": score,
        "content": "some table body",
    }


def test_build_creates_citations():
    builder = CitationBuilder()
    citations = builder.build([_hit(), _hit(document="paper.pdf", page=8, ctype="text", chunk="c2")])
    assert len(citations) == 2
    assert citations[0].document_name == "paper.pdf"
    assert citations[0].page == 2
    assert citations[0].content_type == "table"


def test_dedupe_by_chunk():
    builder = CitationBuilder()
    citations = builder.build([_hit(), _hit()])  # 相同 chunk
    assert len(citations) == 1


def test_order_by_score_desc():
    builder = CitationBuilder()
    citations = builder.build([_hit(score=0.5, chunk="a"), _hit(score=0.9, chunk="b")])
    assert [c.score for c in citations] == [0.9, 0.5]


def test_none_and_empty_filtered():
    builder = CitationBuilder()
    cites = builder.build([{"score": 0.1}])  # 无 document/source 的占位应被丢弃
    assert cites == []


def test_contextitem_coercion():
    item = ContextItem(
        type=ContentType.EQUATION, content="x=1", source="paper.pdf", page=5,
        score=0.8, document_id="d1", chunk_id="eq1",
    )
    cites = CitationBuilder().build([item])
    assert len(cites) == 1
    assert cites[0].content_type == "equation"
    assert cites[0].page == 5


def test_display_label():
    c = Citation(document_name="paper.pdf", page=4, content_type="table")
    assert "paper.pdf" in c.display()
    assert "Page 4" in c.display()
    assert "Table" in c.display()


def test_max_sources_limit():
    builder = CitationBuilder(max_sources=2)
    hits = [_hit(chunk=f"c{i}", score=1.0 - i / 10) for i in range(5)]
    assert len(builder.build(hits)) == 2