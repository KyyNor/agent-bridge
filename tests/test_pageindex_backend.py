from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_bridge.core.domain import AskResult


class FakeMarkItDown:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown
        self.converted: list[str] = []

    def convert_local(self, path: str):
        self.converted.append(path)

        class Result:
            text_content = self.markdown

        return Result()


class FakePageIndexClient:
    created: list["FakePageIndexClient"] = []

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        retrieve_model: str | None = None,
        workspace: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.retrieve_model = retrieve_model
        self.workspace = workspace
        self.indexed: list[tuple[str, str]] = []
        self.documents: dict[str, dict] = {}
        FakePageIndexClient.created.append(self)

    def index(self, file_path: str, mode: str = "auto") -> str:
        doc_id = f"pi-{len(self.indexed) + 1}"
        self.indexed.append((file_path, mode))
        self.documents[doc_id] = {
            "id": doc_id,
            "doc_name": Path(file_path).name,
            "doc_description": "",
            "type": "md" if file_path.endswith(".md") else "pdf",
            "structure": [
                {
                    "title": "Revenue",
                    "node_id": "0001",
                    "line_num": 1,
                    "text": "Revenue grew because enterprise customers expanded.",
                }
            ],
        }
        return doc_id

    def get_document(self, doc_id: str) -> str:
        doc = self.documents[doc_id]
        return json.dumps({"doc_id": doc_id, "status": "completed", "doc_name": doc["doc_name"]})

    def get_document_structure(self, doc_id: str) -> str:
        return json.dumps(self.documents[doc_id]["structure"])

    def get_page_content(self, doc_id: str, pages: str) -> str:
        return json.dumps([{"page": 1, "content": self.documents[doc_id]["structure"][0]["text"]}])


class FakePageIndexCollection:
    def __init__(self) -> None:
        self.added: list[str] = []
        self.documents: dict[str, dict] = {}
        self.deleted: list[str] = []
        self.queries: list[dict] = []

    def add(self, file_path: str) -> str:
        doc_id = f"pi3-{len(self.added) + 1}"
        self.added.append(file_path)
        self.documents[doc_id] = {
            "id": doc_id,
            "structure": [
                {
                    "title": "Revenue",
                    "node_id": "0001",
                    "text": "Revenue grew because enterprise customers expanded.",
                }
            ],
        }
        return doc_id

    def get_document_structure(self, doc_id: str) -> list[dict]:
        return self.documents[doc_id]["structure"]

    def query(self, question: str, doc_ids: list[str] | None = None, stream: bool = False) -> str:
        self.queries.append({"question": question, "doc_ids": doc_ids, "stream": stream})
        return "Revenue grew from official PageIndex query."

    def delete_document(self, doc_id: str) -> None:
        self.deleted.append(doc_id)


class FakePageIndexV3Client:
    created: list["FakePageIndexV3Client"] = []

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        retrieve_model: str | None = None,
        storage_path: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.retrieve_model = retrieve_model
        self.storage_path = storage_path
        self.collection_names: list[str] = []
        self._collection = FakePageIndexCollection()
        FakePageIndexV3Client.created.append(self)

    def collection(self, name: str = "default") -> FakePageIndexCollection:
        self.collection_names.append(name)
        return self._collection


class FakeCloudOnlyPageIndexClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def submit_document(self, file_path: str) -> dict:
        return {"doc_id": "cloud-doc"}


def _backend(tmp_path: Path, **kwargs):
    from agent_bridge.knowledge_management.docs_knowledge.backends.pageindex import PageIndexBackend

    return PageIndexBackend(
        root=tmp_path / "pageindex",
        client_class=kwargs.get("client_class", FakePageIndexClient),
        markitdown_factory=kwargs.get("markitdown_factory"),
        base_url="http://litellm.internal/v1",
        api_key="internal-key",
        model=kwargs.get("model", "openai/local-chat"),
        retrieve_model=kwargs.get("retrieve_model", "openai/local-chat"),
    )


def test_create_kb_creates_workspace(tmp_path: Path) -> None:
    backend = _backend(tmp_path)

    kb_id = backend.create_kb("finance-docs", "Finance Docs")

    assert kb_id == "finance-docs"
    assert (tmp_path / "pageindex" / "finance-docs").is_dir()
    assert json.loads((tmp_path / "pageindex" / "finance-docs" / "kb.json").read_text()) == {
        "slug": "finance-docs",
        "name": "Finance Docs",
    }


def test_upload_markdown_indexes_file_and_records_status(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")

    doc_id = backend.upload("docs", "guide", source, "guide.md")

    assert doc_id == "pi-1"
    client = FakePageIndexClient.created[-1]
    assert client.workspace == str(tmp_path / "pageindex" / "docs" / "workspace")
    assert client.api_key == "internal-key"
    assert client.model == "openai/local-chat"
    assert client.indexed == [(str(source), "md")]
    status = backend.get_status("docs", doc_id)
    assert status.status == "completed"
    assert status.progress == 1.0


def test_upload_uses_pageindex_v3_local_collection_client(tmp_path: Path) -> None:
    backend = _backend(tmp_path, client_class=FakePageIndexV3Client)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")

    doc_id = backend.upload("docs", "guide", source, "guide.md")
    results = backend.retrieve("docs", "enterprise revenue", top_k=2)

    assert doc_id == "pi3-1"
    client = FakePageIndexV3Client.created[-1]
    assert client.api_key is None
    assert client.model == "openai/local-chat"
    assert client.retrieve_model == "openai/local-chat"
    assert client.storage_path == str(tmp_path / "pageindex" / "docs" / "workspace")
    assert client.collection_names == ["default"]
    assert client._collection.added == [str(source)]
    assert results[0].chunk_id == "pi3-1:1"


def test_litellm_gateway_models_without_provider_use_openai_prefix(tmp_path: Path) -> None:
    backend = _backend(
        tmp_path,
        client_class=FakePageIndexV3Client,
        model="deepseek-v4-flash",
        retrieve_model="deepseek-v4-flash",
    )
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    backend.upload("docs", "guide", source, "guide.md")
    backend.ask("docs", "hello")

    client = FakePageIndexV3Client.created[-1]
    assert client.model == "openai/deepseek-v4-flash"
    assert client.retrieve_model == "openai/deepseek-v4-flash"
    assert client._collection.queries[0]["question"] == "hello"


def test_cloud_only_pageindex_sdk_raises_clear_error(tmp_path: Path) -> None:
    backend = _backend(tmp_path, client_class=FakeCloudOnlyPageIndexClient)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    with pytest.raises(RuntimeError, match="incompatible PageIndex SDK"):
        backend.upload("docs", "guide", source, "guide.md")


def test_client_creation_configures_internal_litellm_gateway(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    backend = _backend(tmp_path)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    backend.upload("docs", "guide", source, "guide.md")

    assert os.environ["OPENAI_API_KEY"] == "internal-key"
    assert os.environ["OPENAI_BASE_URL"] == "http://litellm.internal/v1"
    assert os.environ["OPENAI_API_BASE"] == "http://litellm.internal/v1"


def test_upload_docx_converts_with_markitdown_before_indexing(tmp_path: Path) -> None:
    converter = FakeMarkItDown("# Converted\n\nWord content")
    backend = _backend(tmp_path, markitdown_factory=lambda: converter)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "report.docx"
    source.write_bytes(b"docx")

    doc_id = backend.upload("docs", "report", source, "report.docx")

    assert doc_id == "pi-1"
    converted_path = tmp_path / "pageindex" / "docs" / "converted" / "report.md"
    assert converted_path.read_text(encoding="utf-8") == "# Converted\n\nWord content"
    assert converter.converted == [str(source)]
    assert FakePageIndexClient.created[-1].indexed == [(str(converted_path), "md")]


def test_upload_converted_plain_text_adds_markdown_heading(tmp_path: Path) -> None:
    converter = FakeMarkItDown("Plain text without markdown headings")
    backend = _backend(tmp_path, markitdown_factory=lambda: converter)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "plain.docx"
    source.write_bytes(b"docx")

    backend.upload("docs", "plain-doc", source, "plain.docx")

    converted_path = tmp_path / "pageindex" / "docs" / "converted" / "plain-doc.md"
    assert converted_path.read_text(encoding="utf-8").startswith(
        "# plain\n\nPlain text without markdown headings"
    )


def test_upload_plain_markdown_indexes_normalized_copy(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "notes.md"
    source.write_text("Plain text without markdown headings", encoding="utf-8")

    backend.upload("docs", "notes", source, "notes.md")

    normalized_path = tmp_path / "pageindex" / "docs" / "converted" / "notes.md"
    assert normalized_path.read_text(encoding="utf-8").startswith(
        "# notes\n\nPlain text without markdown headings"
    )
    assert FakePageIndexClient.created[-1].indexed == [(str(normalized_path), "md")]


def test_upload_unsupported_format_raises_clear_error(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "weird.rtf"
    source.write_bytes(b"rtf")

    with pytest.raises(ValueError, match="unsupported PageIndex file format"):
        backend.upload("docs", "weird", source, "weird.rtf")


def test_retrieve_returns_keyword_matching_pageindex_content(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")
    backend.upload("docs", "guide", source, "guide.md")

    results = backend.retrieve("docs", "enterprise revenue", top_k=2)

    assert len(results) == 1
    assert results[0].chunk_id == "pi-1:1"
    assert results[0].document_name == "guide.md"
    assert "enterprise customers" in results[0].content
    assert results[0].dataset_id == "docs"


def test_ask_prefers_official_pageindex_collection_query(tmp_path: Path) -> None:
    backend = _backend(tmp_path, client_class=FakePageIndexV3Client)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")
    backend.upload("docs", "guide", source, "guide.md")

    result, chat_id = backend.ask("docs", "why did revenue grow?")

    assert isinstance(result, AskResult)
    assert result.answer == "Revenue grew from official PageIndex query."
    assert result.chunks
    collection = FakePageIndexV3Client.created[-1]._collection
    assert collection.queries == [
        {"question": "why did revenue grow?", "doc_ids": ["pi3-1"], "stream": False}
    ]
    assert chat_id == ""


def test_ask_configures_pageindex_query_for_chat_completions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple] = []
    fake_agents = SimpleNamespace(
        set_default_openai_api=lambda api: calls.append(("api", api)),
        set_default_openai_key=lambda key, use_for_tracing=True: calls.append(
            ("key", key, use_for_tracing)
        ),
        set_tracing_disabled=lambda disabled: calls.append(("tracing", disabled)),
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    backend = _backend(tmp_path, client_class=FakePageIndexV3Client)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")
    backend.upload("docs", "guide", source, "guide.md")

    backend.ask("docs", "why did revenue grow?")

    assert ("api", "chat_completions") in calls
    assert ("key", "internal-key", False) in calls
    assert ("tracing", True) in calls


def test_ask_requires_official_pageindex_query_interface(tmp_path: Path) -> None:
    backend = _backend(tmp_path)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")
    backend.upload("docs", "guide", source, "guide.md")

    with pytest.raises(RuntimeError, match="official PageIndex query"):
        backend.ask("docs", "why did revenue grow?")


# ---------------------------------------------------------------------------
# Legacy Office (.doc/.ppt) → soffice → markitdown pipeline
# ---------------------------------------------------------------------------


def _install_fake_soffice(
    monkeypatch: pytest.MonkeyPatch, produced_content: bytes = b"OOXML"
) -> None:
    """Patch ``convert_via_soffice`` to drop a fake OOXML file in outdir."""

    def _fake(src: Path, target_suffix: str, outdir: Path, **kwargs) -> Path:
        outdir.mkdir(parents=True, exist_ok=True)
        out_file = outdir / (src.stem + target_suffix)
        out_file.write_bytes(produced_content)
        return out_file

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends.pageindex.convert_via_soffice",
        _fake,
    )


def test_upload_doc_converts_via_soffice_then_markitdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_soffice(monkeypatch)
    converter = FakeMarkItDown("# Title\n\nFrom legacy doc")
    backend = _backend(tmp_path, markitdown_factory=lambda: converter)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0")

    doc_id = backend.upload("docs", "legacy", source, "legacy.doc")

    assert doc_id == "pi-1"
    # markitdown must have been fed the soffice-produced docx, not the .doc.
    converted_docx = tmp_path / "pageindex" / "docs" / "converted" / "legacy.docx"
    assert converter.converted == [str(converted_docx)]
    # Final indexed artifact is the markdown produced from that docx.
    md_path = tmp_path / "pageindex" / "docs" / "converted" / "legacy.md"
    assert md_path.read_text(encoding="utf-8") == "# Title\n\nFrom legacy doc"
    assert FakePageIndexClient.created[-1].indexed == [(str(md_path), "md")]


def test_upload_ppt_converts_via_soffice_then_markitdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_soffice(monkeypatch)
    converter = FakeMarkItDown("# Slides\n\nSlide one")
    backend = _backend(tmp_path, markitdown_factory=lambda: converter)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "deck.ppt"
    source.write_bytes(b"\xd0\xcf\x11\xe0")

    doc_id = backend.upload("docs", "deck", source, "deck.ppt")

    assert doc_id == "pi-1"
    converted_pptx = tmp_path / "pageindex" / "docs" / "converted" / "deck.pptx"
    assert converter.converted == [str(converted_pptx)]
    md_path = tmp_path / "pageindex" / "docs" / "converted" / "deck.md"
    assert md_path.exists()


def test_upload_doc_propagates_soffice_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When soffice is unavailable, upload must surface the friendly error."""

    def _raise(src, target_suffix, outdir, **kwargs):
        from agent_bridge.knowledge_management.docs_knowledge.backends._office_convert import (
            OfficeConversionError,
        )

        raise OfficeConversionError("LibreOffice (soffice) is not installed.")

    monkeypatch.setattr(
        "agent_bridge.knowledge_management.docs_knowledge.backends.pageindex.convert_via_soffice",
        _raise,
    )
    backend = _backend(tmp_path, markitdown_factory=lambda: FakeMarkItDown(""))
    backend.create_kb("docs", "Docs")
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"\xd0\xcf\x11\xe0")

    with pytest.raises(RuntimeError, match="LibreOffice.*not installed"):
        backend.upload("docs", "legacy", source, "legacy.doc")


# ---------------------------------------------------------------------------
# Newly allowed direct-conversion formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", [".markdown", ".csv", ".json"])
def test_upload_newly_allowed_formats_route_to_markitdown(
    tmp_path: Path, suffix: str
) -> None:
    converter = FakeMarkItDown("# Heading\n\ncontent")
    backend = _backend(tmp_path, markitdown_factory=lambda: converter)
    backend.create_kb("docs", "Docs")
    source = tmp_path / f"data{suffix}"

    if suffix == ".markdown":
        source.write_text("plain markdown body", encoding="utf-8")
    elif suffix == ".csv":
        source.write_text("a,b\n1,2\n", encoding="utf-8")
    else:
        source.write_text('{"k": "v"}', encoding="utf-8")

    doc_id = backend.upload("docs", "data", source, source.name)

    assert doc_id == "pi-1"
    md_path = tmp_path / "pageindex" / "docs" / "converted" / "data.md"
    assert md_path.exists()
    # .markdown is in _DIRECT_EXTENSIONS and normalized in-place (no markitdown
    # call); .csv/.json go through markitdown.
    if suffix == ".markdown":
        assert converter.converted == []
        assert md_path.read_text(encoding="utf-8").startswith("# data\n\nplain markdown body")
    else:
        assert converter.converted == [str(source)]
