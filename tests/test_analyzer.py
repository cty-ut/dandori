import pytest

from dandori.api import compile_source
from dandori.frontend import ast
from dandori.frontend.source import Source


def compile_ok(code):
    result = compile_source(Source(code))
    assert result.errors == [], [e.message for e in result.errors]
    return result.program


def error_codes(code):
    return [e.code for e in compile_source(Source(code)).errors]


def steps_of(program):
    return [step for sentence in program.sentences for step in sentence.steps]


def test_particles_bind_by_name_not_position():
    a, b = steps_of(compile_ok("10から3を引く。3を10から引く。"))
    for step in (a, b):
        assert step.args["minuend"].value == 10
        assert step.args["subtrahend"].value == 3


def test_optional_slot():
    (step,) = steps_of(compile_ok("100に5を足す。"))
    assert step.args["base"].value == 100
    assert step.args["numbers"].value == 5


@pytest.mark.parametrize("code, verb, filled_param", [
    ("2と3を掛けて、10から引く。", "引く", "subtrahend"),   # 10 − 6
    ("2と3を掛けて、10を引く。", "引く", "minuend"),        # 6 − 10
    ("2と3を掛けて、10を足す。", "足す", "base"),           # 可选槽也算空槽
    ("100を足して、表示する。", "表示する", "value"),
])
def test_previous_result_fills_the_only_empty_slot(code, verb, filled_param):
    step = steps_of(compile_ok(code))[-1]
    assert step.spec.dictionary_form == verb
    assert isinstance(step.args[filled_param], ast.PreviousResult)


def test_sore_disables_auto_fill():
    step = steps_of(compile_ok("2と3を掛けて、それを表示する。"))[-1]
    assert isinstance(step.args["value"], ast.Sore)


def test_sore_crosses_sentences():
    compile_ok("100と200を足す。それを表示する。")


def test_auto_fill_does_not_cross_sentences():
    assert error_codes("100と200を足す。表示する。") == ["E3003"]


def test_no_fill_after_verb_without_result():
    # 表示する 没有结果，所以下一步不自动填入，也不算"结果没被使用"
    compile_ok("100を表示して、200を表示する。")


@pytest.mark.parametrize("code, expected", [
    ("10に3を引く。", ["E3001", "E3003"]),
    ("1を2を足す。", ["E3002"]),
    ("10から引く。", ["E3003"]),
    ("2と3を掛けて、引く。", ["E3004"]),
    ("2と3を掛けて、10から3を引く。", ["E3005"]),
    ("それを表示する。", ["E3006"]),
    ("100を表示する。それを表示する。", ["E3006"]),
    ("「あ」を足す。", ["E3007"]),
    ("「あ」と1を足す。", ["E3007"]),
    ("「あ」+1を表示する。", ["E3008"]),
    ("-「あ」を表示する。", ["E3008"]),
])
def test_errors(code, expected):
    assert error_codes(code) == expected


def test_type_error_messages():
    result = compile_source(Source("「あ」と1を足す。「こんにちは」を引いて表示する。"))
    messages = [e.message for e in result.errors]
    assert "「足す」の「〜を」には数が必要です（『「あ」と1』は並びです）" in messages
    assert "「引く」の「〜を」には数か日付が必要です（「こんにちは」は文字列です）" in messages


@pytest.mark.parametrize("code", [
    "ねこを表示する。",       # 不认识的名词
    "100とねこを足す。",      # 列表中不认识的词
    "100を計算する。",        # 不认识的动词
    "ねこ+1を表示する。",     # 算式中不认识的词
])
def test_unknown_word_does_not_cascade(code):
    assert error_codes(code) == ["E1001"]


def test_errors_from_all_stages_are_collected_and_sorted():
    codes = error_codes("ねこを表示する。\n100表示する。\nそれを表示する。")
    assert codes == ["E1001", "E2007", "E3006"]


# ---------- 阶段 2-1：变量 ----------

def test_variables_compile():
    compile_ok("税率は0.1とする。価格は1000とする。価格×税率を表示する。")


def test_reassignment_keeps_type():
    compile_ok("合計は0とする。合計に5を足して、合計とする。合計は合計+1とする。")


def test_sore_can_name_previous_sentence():
    compile_ok("100と200を足す。合計はそれとする。合計を表示する。")


@pytest.mark.parametrize("code, expected", [
    ("税率を表示する。", ["E3010"]),
    ("合計は合計+1とする。", ["E3010"]),                           # 右边先求值
    ("税率を表示する。税率は0.1とする。", ["E3010"]),               # 先使用后定义
    ("合計は100とする。合計は「多い」とする。", ["E3009"]),
    ("名前は「山田」とする。100を足して、名前とする。", ["E3009"]),
    ("100を表示して、件数とする。", ["E3011"]),
    ("合計は100とする。それを表示する。", ["E3006"]),               # 赋值不产生结果
    ("100を足して、合計とする。それを表示する。", ["E3006"]),
    ("文字は「あ」とする。文字+1を表示する。", ["E3008"]),
])
def test_variable_errors(code, expected):
    assert error_codes(code) == expected


def test_misspelled_name_in_assignment_does_not_cascade():
    assert error_codes("答えは1とする。答えを表示する。") == ["E1004", "E1004"]


# ---------- 阶段 2-2：条件 ----------

@pytest.mark.parametrize("code", [
    # 所有分支都定义了
    "合計は1とする。もし合計が1ならば、区分は「a」とする。そうでなければ、区分は「b」とする。ここまで。区分を表示する。",
    # else-if 也算
    "合計は1とする。もし合計が1ならば、区分は「a」とする。そうでなく合計が2ならば、区分は「b」とする。"
    "そうでなければ、区分は「c」とする。ここまで。区分を表示する。",
    # もし之前已经定义过
    "区分は「x」とする。合計は1とする。もし合計が1ならば、区分は「a」とする。ここまで。区分を表示する。",
    # 只在分支内使用
    "合計は1とする。もし合計が1ならば、区分は「a」とする。区分を表示する。ここまで。",
    # 嵌套：内层两个分支都定义了，外层的这个分支也就定义了
    "合計は1とする。もし合計が1ならば、もし合計が2ならば、区分は「a」とする。そうでなければ、区分は「b」とする。"
    "ここまで。そうでなければ、区分は「c」とする。ここまで。区分を表示する。",
])
def test_definite_assignment_ok(code):
    compile_ok(code)


@pytest.mark.parametrize("code", [
    # 没有「そうでなければ」
    "合計は1とする。もし合計が1ならば、区分は「a」とする。ここまで。区分を表示する。",
    # 有一个分支没定义
    "合計は1とする。もし合計が1ならば、区分は「a」とする。そうでなく合計が2ならば、1を表示する。"
    "そうでなければ、区分は「c」とする。ここまで。区分を表示する。",
    # 嵌套：内层没有「そうでなければ」
    "合計は1とする。もし合計が1ならば、もし合計が2ならば、区分は「a」とする。ここまで。"
    "そうでなければ、区分は「c」とする。ここまで。区分を表示する。",
])
def test_definite_assignment_errors(code):
    assert error_codes(code) == ["E3014"]


def test_type_must_match_across_branches():
    code = "合計は1とする。もし合計が1ならば、区分は「a」とする。そうでなければ、区分は2とする。ここまで。"
    assert error_codes(code) == ["E3009"]


def test_sore_in_condition_refers_to_outer():
    compile_ok("100と200を足す。もしそれが300ならば、1を表示する。ここまで。")


@pytest.mark.parametrize("code", [
    "100と200を足す。もし1が1ならば、それを表示する。ここまで。",     # 块内第一句
    "合計は1とする。もし合計が1ならば、2を足す。ここまで。それを表示する。",  # ここまで之后
])
def test_sore_does_not_cross_blocks(code):
    assert error_codes(code) == ["E3006"]


@pytest.mark.parametrize("code, expected", [
    ("文字は「a」とする。もし文字が1以上ならば、ここまで。", ["E3012"]),
    ("文字は「a」とする。もし文字>1ならば、ここまで。", ["E3012"]),
    ("文字は「a」とする。もし文字が1ならば、ここまで。", ["E3013"]),
    ("一覧は1と2とする。もし一覧が1ならば、ここまで。", ["E3013"]),
    ("もし未定が1ならば、ここまで。", ["E3010"]),
])
def test_comparison_type_errors(code, expected):
    assert error_codes(code) == expected


# ---------- 阶段 2-3：循环 ----------

@pytest.mark.parametrize("code", [
    "合計は0とする。月を1から12まで繰り返して、合計に月を足して、合計とする。ここまで。合計を表示する。",
    "月を1から3まで繰り返して、月を表示する。ここまで。月を1から5まで繰り返して、月を表示する。ここまで。",  # 依次复用变量名
    "月を1から3まで繰り返して、日を1から月まで繰り返して、日を表示する。ここまで。ここまで。",  # 内层范围用外层变量
    "100と200を足す。それ回繰り返して、ここまで。",              # 次数中的それ指向循环之前
    "件数は3とする。件数回繰り返して、件数に1を足して、件数とする。ここまで。",  # 修改次数的变量是允许的
])
def test_loops_ok(code):
    compile_ok(code)


@pytest.mark.parametrize("code, expected", [
    ("月を1から3まで繰り返して、月は5とする。ここまで。", ["E3017"]),
    ("月を1から3まで繰り返して、1を足して、月とする。ここまで。", ["E3017"]),
    ("月を1から3まで繰り返して、ここまで。月を表示する。", ["E3018"]),
    ("3回繰り返して、小計は1とする。ここまで。小計を表示する。", ["E3014"]),   # 可能执行 0 次
    ("月は1とする。月を1から3まで繰り返して、ここまで。", ["E3016"]),
    ("月を1から3まで繰り返して、月を1から2まで繰り返して、ここまで。ここまで。", ["E3016"]),
    ("「三」回繰り返して、ここまで。", ["E3015"]),
    ("月を1から「十」まで繰り返して、ここまで。", ["E3015"]),
    ("100を足す。3回繰り返して、それを表示する。ここまで。", ["E3006"]),       # 块内第一句
    ("3回繰り返して、1を足す。ここまで。それを表示する。", ["E3006"]),         # ここまで之后
])
def test_loop_errors(code, expected):
    assert error_codes(code) == expected
