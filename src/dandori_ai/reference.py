"""语言说明书：系统提示（AI助手设计.md 第 2 节）

模型训练时没见过段取り。说明书由三部分组成：
- 语法摘要（reference/grammar.md，手写）
- 动词、设定、日付属性的目录（从注册表自动生成：语言增加了动词，这里自动就有）
- 范例程序（reference/examples/）

内容在一次运行中固定不变，所以可以使用提示词缓存。
"""

from __future__ import annotations

from importlib import resources

from dandori import api

INSTRUCTIONS = """\
あなたは「段取り」のプログラムを書く担当者です。段取りは日本の業務自動化のための新しいプログラミング言語で、あなたの学習データには含まれていません。下のリファレンス、目録、例にある書き方だけを使ってください。

進め方：
- CSV・Excel を使うときは、まず inspect_file で列名と型（Excel はシートも）を確かめる。列名やシート名を推測しない
- 書いたら check_program で検査し、エラーがあれば直す。エラーの「提案」に従うとたいてい直る
- できあがったら submit_program で提出する。提出したプログラムも検査され、エラーがあれば差し戻される

決まり：
- 業務のルールは会社ごとに違う。要望から決まらないときは、自分で決めずに ask_user で質問する。質問できないときは、提出時の open_questions に書く。リファレンスや例にある値（例の「880円」など）は、その例の会社のルールで、標準値ではない。会社ごとに違うルールの例：
  - 振込手数料をいくらまで差し引いてよいか（消込の手数料）
  - 空欄をどう扱うか（除く、0とみなす、エラーにする）
  - 1円未満の端数の処理（切り捨て、四捨五入、切り上げ）
  - 期日が休日のとき前倒しか後倒しか、締め日と支払日
  - 銀行の営業日か、会社の営業日か
  - どの列を使うか、段取りにない機能（メール送信など）をどうするか
- 質問できずに提出するときは、ルールを次の二つに分けて扱う：
  - 書かなくても動く任意の設定（消込の手数料 など）：要望になければ書かない。書かなかったときに何が起きるか（例：手数料を差し引いた入金は「要確認」になる）を open_questions に書く
  - 決めないとプログラムが動かない判断（計算に使う列の空欄 など）：最も一般的で意外の少ないやり方を選び、プログラムの文として目に見える形で書く。選んだやり方とほかの選択肢を open_questions に書く。実行するとエラーになるプログラムは提出しない
- 空欄の扱いでは、空欄の行を除く（`〜が空欄でないもの` を絞り込む）のが Excel の AVERAGE などと同じで意外が少ない。inspect_file で空欄がある列（blanks が1以上）を計算に使うときは、必ずどう扱うかを決めて確認する
- 数値（許容額、税率、日数 など）は要望にないかぎり自分で作らない
- プログラムの最初に「この処理が使うもの」を書き、使うファイルだけを必要最小限に宣言する
- 業務担当者が読んで分かるように、短く書く。表の操作（絞り込む・合計する など）で書けることは繰り返しで書かない
- プログラムを実行するのは人です。あなたは実行しません
"""


def _text(name: str) -> str:
    return resources.files("dandori_ai").joinpath(f"reference/{name}").read_text(encoding="utf-8")


def render_catalog(catalog: dict) -> str:
    lines = ["# 目録（段取りが今提供している動詞・設定・日付の属性）", "", "## 動詞"]
    for verb in catalog["verbs"]:
        params = []
        for p in verb["params"]:
            accepts = "・".join(p["accepts"]) if p["accepts"] else "何でも"
            kind = "" if p["kind"] == "値" else f"{p['kind']}："
            optional = "、省略可" if not p["required"] else ""
            params.append(f"〜{p['particle']}［{kind}{accepts}{optional}］")
        doc = f" 例：{verb['doc']}" if verb["doc"] else ""
        lines.append(f"- {verb['word']}（て形：{verb['te_form']}）{' '.join(params)}{doc}")
    lines += ["", "## 設定文"]
    lines += [f"- {s['namespace']}の{s['key']}は、{s['example']}とする。" for s in catalog["settings"]]
    lines += ["", "## 日付の属性（「期日の月末」のように「の」で取り出す）"]
    lines.append("、".join(f"{p['name']}（{p['result']}）" for p in catalog["date_properties"]))
    return "\n".join(lines)


def examples() -> str:
    parts = ["# 例"]
    folder = resources.files("dandori_ai").joinpath("reference/examples")
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if item.name.endswith(".dandori"):
            parts.append(f"## {item.name}\n```\n{item.read_text(encoding='utf-8').strip()}\n```")
    return "\n\n".join(parts)


def system_prompt() -> str:
    return "\n\n".join([INSTRUCTIONS, _text("grammar.md"), render_catalog(api.catalog()), examples()])
