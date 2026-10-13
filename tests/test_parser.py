from decimal import Decimal

import pytest

from dandori.frontend import ast
from dandori.frontend.conjugation import Form
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.parser import parse
from dandori.frontend.source import Source
from dandori.plugin.registry import default_registry


@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


@pytest.fixture
def run(lexicon):
    def run(code):
        source = Source(code)
        tokens, lex_errors = tokenize(source, lexicon)
        assert lex_errors == []
        return parse(tokens, source)
    return run


def only_sentence(program):
    assert len(program.sentences) == 1
    return program.sentences[0]


def value_of(step, particle):
    return next(arg.value for arg in step.args if arg.particle == particle)


def test_basic_sentence(run):
    program, errors = run("100と200を足して表示する。")
    assert errors == []
    tasu, hyouji = only_sentence(program).steps

    assert (tasu.verb.lemma, tasu.verb.form) == ("足す", Form.TE)
    target = value_of(tasu, "を")
    assert isinstance(target, ast.ListExpr)
    assert [item.value for item in target.items] == [100, 200]

    assert (hyouji.verb.lemma, hyouji.verb.form) == ("表示する", Form.DICTIONARY)
    assert hyouji.args == ()


def test_particles_keep_written_order(run):
    # 構文解析只记录写了什么，助词对应参数槽是意味解析的事
    program, errors = run("3を10から引く。")
    assert errors == []
    step = only_sentence(program).steps[0]
    assert [arg.particle for arg in step.args] == ["を", "から"]


def test_multiplication_binds_tighter(run):
    program, _ = run("100+200×3を表示する。")
    expr = value_of(only_sentence(program).steps[0], "を")
    # 100 + (200 × 3)
    assert expr.op == "+"
    assert expr.left.value == 100
    assert expr.right.op == "*"
    assert (expr.right.left.value, expr.right.right.value) == (200, 3)


def test_left_associative(run):
    program, _ = run("10-3-2を表示する。")
    expr = value_of(only_sentence(program).steps[0], "を")
    # (10 - 3) - 2，而不是 10 - (3 - 2)
    assert expr.op == "-"
    assert expr.left.op == "-"
    assert expr.right.value == 2


def test_parentheses(run):
    program, _ = run("(100+200)×3を表示する。")
    expr = value_of(only_sentence(program).steps[0], "を")
    assert expr.op == "*"
    assert expr.left.op == "+"


def test_negation_and_decimal(run):
    program, _ = run("-1.5を表示する。")
    expr = value_of(only_sentence(program).steps[0], "を")
    assert isinstance(expr, ast.Negate)
    assert expr.operand.value == Decimal("1.5")


def test_and_as_list_vs_particle(run):
    # と 后面是值 → 列表；と 后面是动词 → 助词
    program, errors = run("1と2を足す。3を4と足す。")
    assert errors == []
    first, second = program.sentences
    assert isinstance(value_of(first.steps[0], "を"), ast.ListExpr)
    assert [arg.particle for arg in second.steps[0].args] == ["を", "と"]


def test_sore_and_strings(run):
    program, errors = run("「こんにちは」を表示する。それを表示する。")
    assert errors == []
    first, second = program.sentences
    assert value_of(first.steps[0], "を").value == "こんにちは"
    assert isinstance(value_of(second.steps[0], "を"), ast.Sore)


def test_comma_after_te_form(run):
    program, errors = run("2と3を掛けて、10から引いて表示する。")
    assert errors == []
    assert [s.verb.lemma for s in only_sentence(program).steps] == ["掛ける", "引く", "表示する"]


def test_sentence_may_span_lines(run):
    program, errors = run("100と200を足して\n  表示する。")
    assert errors == []
    assert len(only_sentence(program).steps) == 2


def test_spans_point_to_source(run):
    code = "100と200を足す。"
    program, _ = run(code)
    step = only_sentence(program).steps[0]
    assert code[step.span.start:step.span.end] == "100と200を足す"
    assert code[step.args[0].span.start:step.args[0].span.end] == "100と200を"


@pytest.mark.parametrize("code, expected", [
    ("100を表示する", ["E2001"]),
    ("100を足す表示する。", ["E2003"]),
    ("100を表示して。", ["E2004"]),
    ("(1+2を表示する。", ["E2005"]),
    ("100と200を。", ["E2006"]),
    ("100表示する。", ["E2007"]),
    ("100、200を足す。", ["E2002"]),
    ("を表示する。", ["E2008"]),
])
def test_errors(run, code, expected):
    _, errors = run(code)
    assert [e.code for e in errors] == expected


def test_missing_period_before_next_line(run):
    # 忘了「。」但下一句写在下一行：报 E2001，下一句照常解析
    program, errors = run("100を表示する\n200を表示する。")
    assert [e.code for e in errors] == ["E2001"]
    assert len(program.sentences) == 2


def test_recovers_and_reports_all_errors(run):
    program, errors = run("100表示する。\n200を表示する。\n(1を表示する。")
    assert [e.code for e in errors] == ["E2007", "E2005"]
    # 中间那句没有错，照常解析出来
    assert len(program.sentences) == 1
    assert value_of(program.sentences[0].steps[0], "を").value == 200


def test_dump(run):
    program, _ = run("100+200×3を表示する。")
    assert ast.dump(program) == "\n".join([
        "プログラム",
        "└─ 文",
        "   └─ 手順 表示する（辞書形）",
        "      └─ を",
        "         └─ ＋",
        "            ├─ 数 100",
        "            └─ ×",
        "               ├─ 数 200",
        "               └─ 数 3",
    ])


# ---------- 阶段 2-1：赋值 ----------

def test_assignment(run):
    program, errors = run("税率は0.1とする。")
    assert errors == []
    (stmt,) = program.sentences
    assert isinstance(stmt, ast.Assignment)
    assert stmt.target.name == "税率"
    assert stmt.value.value == Decimal("0.1")


def test_assignment_with_expression_and_list(run):
    program, errors = run("合計は合計+1とする。一覧は1と2とする。")
    assert errors == []
    first, second = program.sentences
    assert first.value.op == "+"
    assert isinstance(second.value, ast.ListExpr)


def test_binding_at_end_of_sentence(run):
    program, errors = run("100と200を足して、合計とする。")
    assert errors == []
    (sentence,) = program.sentences
    assert sentence.bind.name == "合計"
    assert [s.verb.lemma for s in sentence.steps] == ["足す"]


def test_name_as_value(run):
    program, errors = run("価格×税率を表示する。")
    assert errors == []
    expr = value_of(only_sentence(program).steps[0], "を")
    assert (expr.left.name, expr.right.name) == ("価格", "税率")


@pytest.mark.parametrize("code, expected", [
    ("合計とする。", ["E2009"]),
    ("100とする。", ["E2009"]),
    ("単価は500。", ["E2010"]),
    ("単価は表示する。", ["E2010"]),
    ("合計は1とする", ["E2001"]),
    ("100を足して、合計とする表示する。", ["E2008"]),
])
def test_assignment_errors(run, code, expected):
    _, errors = run(code)
    assert [e.code for e in errors] == expected


# ---------- 阶段 2-2：もし文 ----------

IF_PROGRAM = """
もし合計が100以上ならば、
  区分は「大」とする。
そうでなく合計>=10ならば、
  区分は「中」とする。
そうでなければ、
  区分は「小」とする。
ここまで。
"""


def test_if_structure(run):
    program, errors = run(IF_PROGRAM)
    assert errors == []
    (stmt,) = program.sentences
    assert isinstance(stmt, ast.If)
    first, second, other = stmt.branches
    assert (first.condition.op, first.condition.right.value) == (">=", 100)
    assert (second.condition.op, second.condition.right.value) == (">=", 10)
    assert other.condition is None
    assert [b.body[0].value.value for b in stmt.branches] == ["大", "中", "小"]


@pytest.mark.parametrize("cond, op", [
    ("合計が1以上", ">="), ("合計が1以下", "<="), ("合計が1未満", "<"),
    ("合計が1を超える", ">"), ("合計が1", "="), ("合計が1でない", "<>"),
    ("合計≧1", ">="), ("合計<>1", "<>"),
])
def test_comparison_forms(run, cond, op):
    program, errors = run(f"もし{cond}ならば、ここまで。")
    assert errors == []
    assert program.sentences[0].branches[0].condition.op == op


def test_logical(run):
    program, errors = run("もし合計が1以上かつ件数<5かつ区分が「大」ならば、ここまで。")
    assert errors == []
    condition = program.sentences[0].branches[0].condition
    assert condition.op == "かつ"
    assert len(condition.items) == 3


def test_nested_if(run):
    program, errors = run("""
        もし合計が1以上ならば、
          もし件数が2以上ならば、
            1を表示する。
          ここまで。
        ここまで。
    """)
    assert errors == []
    inner = program.sentences[0].branches[0].body[0]
    assert isinstance(inner, ast.If)


def test_comma_after_naraba_is_optional(run):
    _, errors = run("もし合計が1ならば\n1を表示する。\nそうでなければ\n2を表示する。\nここまで。")
    assert errors == []


@pytest.mark.parametrize("code, expected", [
    ("もし合計が1、ここまで。", ["E2011"]),
    ("もし合計が1ならば、1を表示する。", ["E2012"]),
    ("ここまで。", ["E2013"]),
    ("そうでなければ、1を表示する。", ["E2013"]),
    ("もし合計>1かつ合計<5または合計=9ならば、ここまで。", ["E2014"]),
    ("もし合計ならば、ここまで。", ["E2015"]),
    ("もしならば、ここまで。", ["E2015"]),
    ("もし合計が1件ならば、ここまで。", ["E2015"]),     # 条件后面有看不懂的内容
])
def test_if_errors(run, code, expected):
    _, errors = run(code)
    assert [e.code for e in errors] == expected


def test_error_inside_branch_keeps_if_structure(run):
    # 分支内的错误不会吞掉「ここまで」，所以不会有连锁错误
    program, errors = run("もし合計が1ならば、\n100表示する。\nここまで。\n2を表示する。")
    assert [e.code for e in errors] == ["E2007"]
    assert len(program.sentences) == 2
