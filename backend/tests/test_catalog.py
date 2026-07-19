from app.services.intent import QueryIntent, detect_intent, is_catalog_question, is_compare_question


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
