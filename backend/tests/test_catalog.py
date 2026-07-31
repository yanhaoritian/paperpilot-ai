from app.services.intent import (
    DocInventoryItem,
    QueryIntent,
    detect_intent,
    format_inventory_block,
    is_catalog_question,
    is_compare_question,
)
from app.config import Settings


def test_catalog_intent():
    assert is_catalog_question("当前库里有哪些文献？")
    assert is_catalog_question("列出知识库中的论文")
    assert is_catalog_question("我上传了几篇PDF")
    assert detect_intent("这篇论文的方法是什么？") == QueryIntent.QA


def test_compare_intent():
    assert is_compare_question("库里两篇文章有何共同点")
    assert is_compare_question("对比这两篇论文的差异")
    assert is_compare_question("总结这些文献")
    assert detect_intent("α激酶的活性温度是多少？") == QueryIntent.QA


def test_query_module_compat():
    from app.api.query import _is_catalog_question

    assert _is_catalog_question("当前库里有哪些文献？")


def test_configured_model_allowlist():
    settings = Settings(
        jwt_require_strong=False,
        model_options="model-a,model-b",
    )
    assert settings.chat_model_is_allowed(None)
    assert settings.chat_model_is_allowed("model-a")
    assert not settings.chat_model_is_allowed("model-c")


def test_inventory_prompt_is_bounded():
    items = [
        DocInventoryItem(
            document_id=f"d{i}",
            file_name=f"{i}.pdf",
            library_id="l",
            library_name="L",
            status="ready",
            page_count=1,
        )
        for i in range(5)
    ]
    text = format_inventory_block(items, max_items=2)
    assert "0.pdf" in text and "1.pdf" in text
    assert "2.pdf" not in text
    assert "另有 3 篇未展开" in text
