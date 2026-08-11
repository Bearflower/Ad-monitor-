from adwatch.knowledge.models import ExtractedDocument
from adwatch.knowledge.repository import KnowledgeRepository
from adwatch.storage.db import Database


def _document(content: str) -> ExtractedDocument:
    return ExtractedDocument(
        canonical_url="https://seller.shopee.co.th/edu/article/1",
        document_type="article",
        title_original="Policy",
        language="en",
        content_original=content,
        published_at=None,
        remote_updated_at=None,
        metadata={},
    )


def test_store_extracted_reuses_historical_version_when_content_reverts(
    tmp_path,
):
    database = Database(tmp_path / "test.sqlite3")
    database.migrate()
    repository = KnowledgeRepository(database)
    repository.upsert_source(
        source_key="shopee-policy",
        source_type="help_center",
        name_zh="Shopee 政策",
        entry_url="https://seller.shopee.co.th/edu",
        allowed_hosts=("seller.shopee.co.th",),
        extractor_version="v1",
        enabled=True,
    )

    first = repository.store_extracted(
        "shopee-policy", _document("Version A")
    )
    second = repository.store_extracted(
        "shopee-policy", _document("Version B")
    )
    reverted = repository.store_extracted(
        "shopee-policy", _document("Version A")
    )
    third = repository.store_extracted(
        "shopee-policy", _document("Version C")
    )

    assert first.change == "new"
    assert second.change == "updated"
    assert reverted.change == "updated"
    assert reverted.version_id == first.version_id
    assert third.change == "updated"
    assert third.version_number == 3
    with database.connect() as connection:
        document = connection.execute(
            "SELECT current_version_id FROM knowledge_documents"
        ).fetchone()
        version_count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_document_versions"
        ).fetchone()[0]
    assert document["current_version_id"] == third.version_id
    assert version_count == 3
