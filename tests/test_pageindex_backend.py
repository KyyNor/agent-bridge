from __future__ import annotations

import json
import os
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
        completion=kwargs.get("completion"),
        agent_runner=kwargs.get("agent_runner"),
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
    calls: list[dict] = []

    def agent_runner(model: str | None, base_url: str | None, api_key: str | None, tools: dict[str, object], prompt: str) -> str:
        calls.append({"model": model, "base_url": base_url, "api_key": api_key})
        return "ok"

    backend = _backend(
        tmp_path,
        client_class=FakePageIndexV3Client,
        model="deepseek-v4-flash",
        retrieve_model="deepseek-v4-flash",
        agent_runner=agent_runner,
    )
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")

    backend.upload("docs", "guide", source, "guide.md")
    backend.ask("docs", "hello")

    client = FakePageIndexV3Client.created[-1]
    assert client.model == "openai/deepseek-v4-flash"
    assert client.retrieve_model == "openai/deepseek-v4-flash"
    assert calls[0]["model"] == "openai/deepseek-v4-flash"


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
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"doc")

    with pytest.raises(ValueError, match="unsupported PageIndex file format"):
        backend.upload("docs", "legacy", source, "legacy.doc")


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


def test_ask_uses_agentic_pageindex_tools_with_chat_model(tmp_path: Path) -> None:
    calls: list[dict] = []

    def agent_runner(model: str | None, base_url: str | None, api_key: str | None, tools: dict[str, object], prompt: str) -> str:
        calls.append(
            {
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                "prompt": prompt,
                "document": tools["get_document"]("pi-1"),
                "structure": tools["get_document_structure"]("pi-1"),
                "page_content": tools["get_page_content"]("pi-1", "1"),
            }
        )
        return "Revenue grew from enterprise expansion."

    backend = _backend(tmp_path, agent_runner=agent_runner)
    backend.create_kb("docs", "Docs")
    source = tmp_path / "guide.md"
    source.write_text("# Revenue\n\nRevenue grew.", encoding="utf-8")
    backend.upload("docs", "guide", source, "guide.md")

    result, chat_id = backend.ask("docs", "why did revenue grow?")

    assert isinstance(result, AskResult)
    assert result.answer == "Revenue grew from enterprise expansion."
    assert result.chunks
    assert calls[0]["model"] == "openai/local-chat"
    assert calls[0]["base_url"] == "http://litellm.internal/v1"
    assert calls[0]["api_key"] == "internal-key"
    assert "Available PageIndex documents" in calls[0]["prompt"]
    assert "why did revenue grow?" in calls[0]["prompt"]
    assert "guide.md" in calls[0]["document"]
    assert "Revenue" in calls[0]["structure"]
    assert "enterprise customers" in calls[0]["page_content"]
    assert chat_id == ""
