from eidolon_admin_server.app.management.recollecting import _view
from eidolon_memory_contracts.owner import MemoryRecollection


def test_recollection_uses_the_realm_contract_text_and_time() -> None:
    result = _view(
        MemoryRecollection(
            text="我的测试代号是轻松8365。",
            remembered_at="2026-08-28T04:24:36+00:00",
        )
    )

    assert result.text == "我的测试代号是轻松8365。"
    assert result.remembered_at == "2026-08-28T04:24:36+00:00"


def test_a_recollection_without_a_time_says_none_rather_than_now() -> None:
    assert _view(MemoryRecollection(text="说不清什么时候")).remembered_at is None
