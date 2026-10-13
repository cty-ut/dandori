"""阶段 7：AI 助手（全部使用假模型、假客户端：不需要密钥，不花钱）"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from dandori import api
from dandori_ai import evals
from dandori_ai.cli import main
from dandori_ai.loop import GenerationFailed, run_session
from dandori_ai.providers import make_provider
from dandori_ai.providers.base import AssistantTurn, ProviderError, ToolCall
from dandori_ai.providers.claude import ClaudeProvider
from dandori_ai.providers.fake import FakeProvider, call, submit
from dandori_ai.providers.openai_compat import OpenAICompatibleProvider
from dandori_ai.reference import render_catalog, system_prompt
from dandori_ai.tools import TOOLS, Toolbox

ROOT = Path(__file__).parent.parent
GOOD = "この処理が使うもの：\n  読み込み：「明細.csv」\nここまで。\n「明細.csv」を読み込んで、明細とする。\n明細の金額を合計して表示する。\n"
BAD = "「明細.csv」を読み込んで、明細とする。\n明細の金頭を合計して表示する。\n"


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "明細.csv").write_text("取引先,金額\n山田商事,1000\n佐藤物産,2000\n", encoding="utf-8")
    return tmp_path


# ---------- 架构：AI 包只通过公开 API 使用语言 ----------

def test_ai_package_uses_only_public_api():
    for path in (ROOT / "src" / "dandori_ai").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:from|import)\s+(dandori(?:\.\w+)*)", line)
            if m and not m.group(1).startswith("dandori_ai"):
                assert m.group(1) in ("dandori", "dandori.api"), f"{path.name}: {line}"


def test_language_core_does_not_depend_on_ai():
    for path in (ROOT / "src" / "dandori").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "dandori_ai" not in text and "import anthropic" not in text and "import openai" not in text, path


# ---------- 说明书 ----------

def test_system_prompt_contains_grammar_catalog_and_examples():
    prompt = system_prompt()
    assert "言語リファレンス" in prompt
    assert "消し込む（て形：消し込んで）" in prompt          # 目录从注册表自动生成
    assert "入金消込.dandori" in prompt                      # 范例
    assert not re.search(r"[语与带这设计]", prompt)          # 不混入中文的开发注释


def test_catalog_is_generated_from_registry():
    text = render_catalog(api.catalog())
    for verb in api.catalog()["verbs"]:
        assert verb["word"] in text
    assert "消込の手数料は、「880円まで」とする。" in text


# ---------- 工具 ----------

def test_check_program(folder):
    box = Toolbox(folder)
    bad = box.execute("check_program", {"program": BAD})
    assert bad.is_error
    errors = json.loads(bad.content)["errors"]
    assert errors[0]["code"] == "E3025" and errors[0]["line"] == 2
    good = box.execute("check_program", {"program": GOOD})
    assert not good.is_error
    assert json.loads(good.content)["files"] == ["「明細.csv」を読み込みます"]
    assert box.checks == 2


def test_inspect_csv_sends_no_data_by_default(folder):
    info = json.loads(Toolbox(folder).execute("inspect_csv", {"file": "明細.csv"}).content)
    assert info["columns"] == [{"name": "取引先", "type": "文字列", "blanks": 0},
                               {"name": "金額", "type": "数", "blanks": 0}]
    assert info["rows"] == 2
    assert "sample" not in info and "山田商事" not in json.dumps(info, ensure_ascii=False)


def test_inspect_csv_counts_blanks(folder):
    # 空欄数是统计信息：AI 要知道有空欄，才会想到问"空欄怎么处理"
    (folder / "空欄.csv").write_text("取引先,金額\nA,100\nB,\n,300\n", encoding="utf-8")
    info = json.loads(Toolbox(folder).execute("inspect_csv", {"file": "空欄.csv"}).content)
    assert [c["blanks"] for c in info["columns"]] == [1, 1]


def test_inspect_csv_with_samples(folder):
    info = json.loads(Toolbox(folder, allow_samples=True).execute("inspect_csv", {"file": "明細.csv"}).content)
    assert info["sample"][0] == ["山田商事", "1000"]


def test_inspect_csv_missing_and_outside(folder):
    assert Toolbox(folder).execute("inspect_csv", {"file": "無い.csv"}).is_error
    assert Toolbox(folder).execute("inspect_csv", {"file": "../外.csv"}).is_error


def test_ask_user(folder):
    assert Toolbox(folder, ask=lambda q: "880円まで").execute("ask_user", {"question": "手数料は？"}).content == "880円まで"
    result = Toolbox(folder, interactive=False).execute("ask_user", {"question": "手数料は？"})
    assert result.is_error and "open_questions" in result.content


def test_submit_requires_compiling_program_and_permissions(folder):
    box = Toolbox(folder)
    assert box.execute("submit_program", {"program": BAD, "summary": "", "open_questions": []}).is_error
    no_perm = box.execute("submit_program", {"program": "1を表示する。", "summary": "", "open_questions": []})
    assert no_perm.is_error and "この処理が使うもの" in no_perm.content
    ok = box.execute("submit_program", {"program": GOOD, "summary": "合計", "open_questions": ["?"]})
    assert ok.submitted == {"program": GOOD, "summary": "合計", "open_questions": ["?"]}


def test_bad_tool_calls(folder):
    box = Toolbox(folder)
    assert box.execute("rm_rf", {}).is_error
    assert box.execute("check_program", {"__invalid__": "{"}).is_error
    assert box.execute("check_program", {"wrong": 1}).is_error


def test_tool_schemas_are_strict():
    for tool in TOOLS:
        assert re.fullmatch(r"[a-z_]+", tool.name)                    # 三家都只接受英文名
        assert tool.parameters["additionalProperties"] is False
        assert set(tool.parameters["required"]) == set(tool.parameters["properties"])


# ---------- 纠错循环 ----------

def test_loop_fixes_errors_then_submits(folder):
    provider = FakeProvider([
        AssistantTurn("確認します", [call("inspect_csv", 1, file="明細.csv")]),
        AssistantTurn("", [call("check_program", 2, program=BAD)]),
        submit(BAD, n=3),                              # 差し戻される
        submit(GOOD, "金額の合計を表示します", n=4),
    ])
    outcome = run_session(provider, Toolbox(folder), "合計して", system="SYSTEM")
    assert outcome.program == GOOD and outcome.turns == 4 and outcome.checks == 3
    # 第 3 轮看到的是检查的错误；提交被退回后的错误也交还给模型
    last_results = provider.requests[-1][-1]
    assert last_results["role"] == "tool_results" and last_results["results"][0]["is_error"]


def test_loop_reminds_to_submit(folder):
    provider = FakeProvider([AssistantTurn("できました"), submit(GOOD)])
    outcome = run_session(provider, Toolbox(folder), "合計して", system="S")
    assert outcome.turns == 2
    assert provider.requests[1][-1]["text"].startswith("プログラムができたら submit_program")


def test_loop_gives_up(folder):
    provider = FakeProvider([submit(BAD, n=i) for i in range(5)])
    with pytest.raises(GenerationFailed) as e:
        run_session(provider, Toolbox(folder), "合計して", max_turns=3, system="S")
    assert e.value.last_errors == ["E3025"]


# ---------- 提供者：请求的形式 ----------

class FakeAnthropic:
    def __init__(self, content, stop_reason="tool_use"):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self._response = SimpleNamespace(content=content, stop_reason=stop_reason,
                                         usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                                               cache_read_input_tokens=7))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_claude_request_shape():
    content = [SimpleNamespace(type="thinking", thinking=""),
               SimpleNamespace(type="text", text="検査します"),
               SimpleNamespace(type="tool_use", id="tu_1", name="check_program", input={"program": "x"})]
    client = FakeAnthropic(content)
    provider = ClaudeProvider(client=client)
    turn = provider.complete("SYSTEM", [{"role": "user", "text": "要望"}], TOOLS)
    request = client.calls[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}       # 说明书使用缓存
    assert all(t["strict"] for t in request["tools"])
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in request and "tool_choice" not in request
    assert turn.tool_calls == [ToolCall("tu_1", "check_program", {"program": "x"})]
    assert turn.usage["cache_read_input_tokens"] == 7

    # 第二次请求：assistant 的内容（含思考块）原样回传，工具结果放在 user 消息中
    history = [{"role": "user", "text": "要望"},
               {"role": "assistant", "text": turn.text, "tool_calls": turn.tool_calls, "raw": turn.raw},
               {"role": "tool_results", "results": [{"id": "tu_1", "content": "{}", "is_error": True}]}]
    provider.complete("SYSTEM", history, TOOLS)
    messages = client.calls[1]["messages"]
    assert messages[1] == {"role": "assistant", "content": content}
    assert messages[2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}",
                                         "is_error": True}


def test_claude_no_fallback_for_other_models():
    client = FakeAnthropic([SimpleNamespace(type="text", text="")], stop_reason="end_turn")
    ClaudeProvider(model="claude-haiku-5-5", client=client).complete("S", [{"role": "user", "text": "x"}], TOOLS)
    assert "fallbacks" not in client.calls[0]


def test_claude_refusal():
    client = FakeAnthropic([], stop_reason="refusal")
    with pytest.raises(ProviderError):
        ClaudeProvider(client=client).complete("S", [{"role": "user", "text": "x"}], TOOLS)


class FakeOpenAI:
    def __init__(self, message):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self._response = SimpleNamespace(choices=[SimpleNamespace(message=message)],
                                         usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_openai_compatible_request_shape():
    tool_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="inspect_csv",
                                                                  arguments='{"file": "明細.csv"}'))
    client = FakeOpenAI(SimpleNamespace(content=None, tool_calls=[tool_call]))
    provider = OpenAICompatibleProvider("deepseek-chat", "DEEPSEEK_API_KEY", "deepseek", client=client)
    turn = provider.complete("SYSTEM", [{"role": "user", "text": "要望"}], TOOLS)
    assert turn.tool_calls == [ToolCall("c1", "inspect_csv", {"file": "明細.csv"})]
    request = client.calls[0]
    assert request["messages"][0] == {"role": "system", "content": "SYSTEM"}
    assert request["tools"][0]["type"] == "function"

    history = [{"role": "user", "text": "要望"},
               {"role": "assistant", "text": "", "tool_calls": turn.tool_calls, "raw": None},
               {"role": "tool_results", "results": [{"id": "c1", "content": "{}", "is_error": False}]}]
    provider.complete("SYSTEM", history, TOOLS)
    messages = client.calls[1]["messages"]
    assert messages[2]["tool_calls"][0]["function"] == {"name": "inspect_csv", "arguments": '{"file": "明細.csv"}'}
    assert messages[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}


def test_openai_invalid_json_arguments():
    tool_call = SimpleNamespace(id="c1", function=SimpleNamespace(name="check_program", arguments="{壊れた"))
    client = FakeOpenAI(SimpleNamespace(content="", tool_calls=[tool_call]))
    turn = OpenAICompatibleProvider("m", "X", "openai", client=client).complete("S", [{"role": "user", "text": "x"}], TOOLS)
    assert "__invalid__" in turn.tool_calls[0].arguments


def test_provider_factory(monkeypatch):
    monkeypatch.delenv("DANDORI_AI_MODEL", raising=False)
    with pytest.raises(ProviderError, match="モデル"):
        make_provider("openai")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="DEEPSEEK_API_KEY"):
        make_provider("deepseek")
    with pytest.raises(ProviderError, match="提供元"):
        make_provider("gemini")


# ---------- 命令行 ----------

def test_cli_make(folder, capsys):
    output = folder / "合計.dandori"
    provider = FakeProvider([submit(GOOD, "金額の合計を表示します", ["端数の扱い"])])
    assert main(["作る", "合計して", "--出力", str(output)], provider_factory=lambda: provider) == 0
    assert output.read_text(encoding="utf-8") == GOOD
    out = capsys.readouterr().out
    assert "【確認事項】" in out and "端数の扱い" in out
    assert "「明細.csv」を読み込みます" in out                # 编译器给出的事实
    request = provider.requests[0][0]["text"]
    assert "明細.csv" in request and "山田商事" not in request  # 只告诉文件名，不发送数据


def test_cli_make_refuses_to_overwrite(folder):
    output = folder / "合計.dandori"
    output.write_text("既存", encoding="utf-8")
    assert main(["作る", "x", "--出力", str(output)], provider_factory=lambda: FakeProvider([])) == 1
    assert output.read_text(encoding="utf-8") == "既存"


def test_cli_fix(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(BAD, encoding="utf-8")
    provider = FakeProvider([submit(GOOD)])
    assert main(["直す", str(program), "--はい"], provider_factory=lambda: provider) == 0
    assert program.read_text(encoding="utf-8") == GOOD
    assert "E3025" in provider.requests[0][0]["text"]           # 错误交给 AI
    assert "-明細の金頭を合計して表示する。" in capsys.readouterr().out


def test_cli_fix_nothing_to_do(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(GOOD, encoding="utf-8")
    assert main(["直す", str(program)], provider_factory=lambda: FakeProvider([])) == 0
    assert "直すところはありません" in capsys.readouterr().out


def test_cli_change_warns_about_permissions_and_asks(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(GOOD, encoding="utf-8")
    changed = GOOD.replace("ここまで。", "  書き出し：「出力」フォルダ\nここまで。") + "明細を「出力/a.csv」に書き出す。\n"
    provider = FakeProvider([submit(changed)])
    answers = iter(["いいえ"])
    assert main(["変える", str(program), "書き出して"], provider_factory=lambda: provider,
                ask=lambda prompt: next(answers)) == 0
    out = capsys.readouterr().out
    assert "改めて承認が必要です" in out and "＋ 書き出し：「出力」フォルダ" in out
    assert program.read_text(encoding="utf-8") == GOOD                 # 回答「いいえ」：不写入


def test_cli_provider_error(folder, capsys):
    def broken():
        raise ProviderError("キーがありません")
    assert main(["作る", "x", "--出力", str(folder / "a.dandori")], provider_factory=broken) == 1
    assert "キーがありません" in capsys.readouterr().err


# ---------- 评测 ----------

@pytest.mark.parametrize("case", evals.load_cases(ROOT / "evals"), ids=lambda c: c["name"])
def test_eval_cases_are_correct(case, tmp_path):
    """评测用例本身要正确：标准答案程序必须通过用例的所有检查"""
    evals.prepare(case, tmp_path)
    checks, minimal = evals.evaluate_program(case, case["reference_program"], tmp_path)
    assert all(ok for _, ok, _ in checks), checks
    assert minimal is True                                      # 标准答案的权限是最小的
    if case["expect"].get("must_ask"):
        assert case["reference_questions"]


def test_eval_categories():
    categories = {c["category"] for c in evals.load_cases(ROOT / "evals")}
    assert {"基本", "落とし穴", "例にない機能", "最小権限", "質問すべき"} <= categories


def by_name(name):
    return next(c for c in evals.load_cases(ROOT / "evals") if c["name"] == name)


def test_eval_runner_with_fake_provider(tmp_path):
    case = by_name("取引先別集計")
    good = evals.run_case(case, FakeProvider([submit(case["reference_program"])]))
    assert good.generated and good.correct and good.compiles == 1 and good.minimal_permissions
    # 能通过编译、但结果不对（合计 → 平均）：评测要测出来的正是这种情况
    wrong = evals.run_case(case, FakeProvider([submit(case["reference_program"].replace("合計して", "平均して"))]))
    assert wrong.generated and not wrong.correct
    failed = evals.run_case(case, FakeProvider([AssistantTurn("")] * 3))
    assert not failed.generated
    text = evals.report([good, wrong, failed])
    assert "生成できた：2/3" in text and "正解：1/3" in text


def test_must_ask_and_forbid(tmp_path):
    case = by_name("入金消込の手数料")
    silent = evals.run_case(case, FakeProvider([submit(case["reference_program"])]))
    assert not silent.correct and "確認事項" in silent.note            # 没有提出确认事项
    asked = evals.run_case(case, FakeProvider([submit(case["reference_program"], questions=["手数料は？"])]))
    assert asked.correct
    invented = case["reference_program"].replace("入金を請求に", "消込の手数料は、880円までとする。\n入金を請求に")
    guessed = evals.run_case(case, FakeProvider([submit(invented, questions=["?"])]))
    assert not guessed.correct and "消込の手数料" in guessed.note      # 自己编了手续费


def test_must_ask_program_must_also_run():
    # 提了问题，但程序在数据上一运行就报错（空欄）：也算错
    case = by_name("空欄のある平均")
    crashes = "この処理が使うもの：\n  読み込み：「明細.csv」\nここまで。\n「明細.csv」を読み込んで、明細とする。\n明細の金額を平均して表示する。\n"
    result = evals.run_case(case, FakeProvider([submit(crashes, questions=["空欄は？"])]))
    assert not result.correct and "実行エラー" in result.note
    assert evals.run_case(case, FakeProvider([submit(case["reference_program"], questions=["空欄は？"])])).correct


def test_reference_has_no_default_fee():
    # 说明书的语法摘要不能把某个公司的手续费当作"标准写法"展示
    prompt = system_prompt()
    assert "標準値ではない" in prompt and "銀行の営業日" in prompt and "実行するとエラーになるプログラムは提出しない" in prompt


def test_read_only_and_minimal_permissions():
    case = by_name("表示だけ")
    extra = case["reference_program"].replace("ここまで。", "  書き出し：「出力」フォルダ\nここまで。")
    result = evals.run_case(case, FakeProvider([submit(extra)]))
    assert not result.correct and result.minimal_permissions is False


def test_numeric_output():
    case = by_name("消費税の端数")
    program = case["reference_program"].replace("合計×10%を切り捨てて", "合計×1円×10%を切り捨てて")
    assert evals.run_case(case, FakeProvider([submit(program)])).correct   # 「1,334円」でも正解


def test_results_are_saved(tmp_path):
    case = by_name("取引先別集計")
    provider = FakeProvider([submit(case["reference_program"])])
    provider.model = "fake-1"
    results, saved = evals.run_all([case], provider, tmp_path)
    assert (saved / "取引先別集計.dandori").read_text(encoding="utf-8") == case["reference_program"]
    assert json.loads((saved / "results.json").read_text(encoding="utf-8"))[0]["correct"] is True
    assert "正解：1/1" in (saved / "report.txt").read_text(encoding="utf-8")


def test_model_is_recorded(folder):
    provider = FakeProvider([AssistantTurn("", [call("submit_program", 1, program=GOOD, summary="",
                                                     open_questions=[])], model="deepseek-flash")])
    outcome = run_session(provider, Toolbox(folder), "x", system="S")
    assert outcome.models == ["deepseek-flash"]
