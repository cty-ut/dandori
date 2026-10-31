"""Issue #1：出力が cp932 のとき、表示できない文字で止まらないこと

日本語版 Windows で出力をリダイレクトすると標準出力が cp932 になり、
「⚠」「𠮷」のような文字で UnicodeEncodeError になる。3 つの CLI は
入口で tolerate_unrepresentable_chars() を呼び、? に置き換えて続ける。
"""

import io
import sys

import pytest

from dandori.cli import main as dandori_main
from dandori.console import tolerate_unrepresentable_chars
from dandori_ai.cli import main as ai_main
from dandori_platform.cli import main as platform_main

PROGRAM = """この処理が使うもの：
  読み込み：「明細.csv」
  書き出し：「出力」フォルダ
ここまで。
「明細.csv」を読み込んで、明細とする。
明細の金額を取引先ごとに合計して、「出力/集計.csv」に書き出す。
"""


def cp932_streams(monkeypatch):
    """標準出力・標準エラーを cp932 のストリームに差し替える。

    fixture ではなくテストの中で呼ぶこと（pytest が呼び出しフェーズで
    sys.stdout を差し替えるため、fixture だと上書きされてしまう）。
    """
    out_buffer, err_buffer = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(out_buffer, encoding="cp932")
    err = io.TextIOWrapper(err_buffer, encoding="cp932")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out, err, out_buffer, err_buffer


def test_helper_replaces_unrepresentable_chars(monkeypatch):
    throwaway = io.TextIOWrapper(io.BytesIO(), encoding="cp932")
    with pytest.raises(UnicodeEncodeError):   # 対策前は実際に落ちる
        throwaway.write("⚠")
        throwaway.flush()
    out, _, out_buffer, _ = cp932_streams(monkeypatch)
    tolerate_unrepresentable_chars()
    out.write("⚠𠮷")                          # 落ちずに ? になる
    out.flush()
    assert out_buffer.getvalue() == b"??"


def test_helper_tolerates_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    tolerate_unrepresentable_chars()           # 何も起きない


def test_dandori_cli_survives_cp932_output(monkeypatch):
    cp932_streams(monkeypatch)
    out_buffer = sys.stdout.buffer
    assert dandori_main(["実行", "-c", "「⚠」を表示する。"]) == 0
    sys.stdout.flush()
    assert out_buffer.getvalue().decode("cp932").strip() == "?"


def test_platform_cli_list_with_warning_mark(monkeypatch, tmp_path):
    cp932_streams(monkeypatch)
    out_buffer = sys.stdout.buffer
    work = tmp_path / "集計"
    work.mkdir()
    (work / "明細.csv").write_text("取引先,金額\n山田商事,1000\n", encoding="utf-8")
    (work / "集計.dandori").write_text(PROGRAM, encoding="utf-8")
    assert platform_main(["登録", str(work / "集計.dandori"), "--場所", str(tmp_path)]) == 0
    assert platform_main(["一覧", "--場所", str(tmp_path)]) == 0   # 未承認の「⚠ 」で落ちない
    sys.stdout.flush()
    shown = out_buffer.getvalue().decode("cp932")
    assert "集計" in shown and "?" in shown


def test_ai_cli_survives_cp932_output(monkeypatch, tmp_path):
    cp932_streams(monkeypatch)
    err_buffer = sys.stderr.buffer
    # 評価のケースが無いフォルダ：プロバイダは作られても使われない
    assert ai_main(["評価", "--ケース", str(tmp_path)], provider_factory=lambda: object()) == 1
    sys.stdout.write("⚠")                       # CLI の後も表示で落ちない
    sys.stdout.flush()
    sys.stderr.flush()
    assert "評価のケースがありません" in err_buffer.getvalue().decode("cp932")
