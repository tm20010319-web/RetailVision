import threading

import pytest

from scripts.vlx_target_controls import TargetSelection, TargetWindow, query_for


def test_default_is_unselected_and_single_target_query_excludes_other_target():
    selection = TargetSelection()
    assert selection.snapshot() == (0, ())
    assert not selection.accepts(0)
    revision, targets = selection.select('小蜜蜂')
    assert query_for(targets) == 'bee plush toy'
    assert selection.accepts(revision)
    _, targets = selection.select('大吉他')
    assert query_for(targets) == 'white electric guitar'
    _, targets = selection.select('小吉他')
    assert query_for(targets) == 'white ukulele'


def test_clear_and_reselect_same_target_rejects_inflight_old_result():
    selection = TargetSelection('小蜜蜂')
    old_revision, _ = selection.snapshot()
    selection.clear()
    assert not selection.accepts(old_revision)
    selection.select('小蜜蜂')
    assert not selection.accepts(old_revision)


def test_switch_and_invalid_input_do_not_keep_previous_target_active():
    selection = TargetSelection('小蜜蜂')
    old, _ = selection.snapshot()
    revision, targets = selection.select('大吉他')
    assert targets == (1,) and not selection.accepts(old)
    with pytest.raises(ValueError):
        selection.select('椅子')
    assert selection.snapshot()[1] == ()
    assert not selection.accepts(revision)


def test_group_expands_and_mixed_aliases_are_deduplicated():
    selection = TargetSelection('小蜜蜂、白色电吉他')
    assert query_for(selection.snapshot()[1]) == 'bee plush toy; white electric guitar; white ukulele'
    for name in ('吉他', '电吉他', '白色吉他', '白色电吉他', '大吉他、小吉他、电吉他'):
        assert selection.select(name)[1] == (1, 2)


def test_guitar_switching_and_group_results_cannot_survive_clear():
    selection = TargetSelection('吉他')
    old, _ = selection.snapshot()
    assert selection.select('小吉他')[1] == (2,)
    assert not selection.accepts(old)
    small, _ = selection.snapshot()
    selection.clear()
    assert not selection.accepts(small)


def test_actual_text_field_buttons_submit_edit_and_clear():
    selection = TargetSelection()
    window = TargetWindow(selection, threading.Event(), width=800)
    try:
        window.pump()
        assert selection.snapshot()[1] == ()
        window.text.set('小蜜蜂')
        window.apply_button.invoke()
        bee_revision, targets = selection.snapshot()
        assert targets == (0,)
        window.text.set('大吉他')
        assert not selection.accepts(bee_revision)
        assert selection.snapshot()[1] == ()
        window.apply_button.invoke()
        assert selection.snapshot()[1] == (1,)
        window.text.set('小吉他')
        window.apply_button.invoke()
        assert selection.snapshot()[1] == (2,)
        window.text.set('吉他')
        window.apply_button.invoke()
        assert selection.snapshot()[1] == (1, 2)
        assert window.message.get() == '当前标注：大吉他、小吉他'
        window.clear_button.invoke()
        assert window.text.get() == '' and selection.snapshot()[1] == ()
    finally:
        window.destroy()
