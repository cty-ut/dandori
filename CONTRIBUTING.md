# 段取りへの貢献

**日本語** | [简体中文](#简体中文)

段取りに興味を持っていただき、ありがとうございます。バグの報告、テストデータ（各銀行の CSV の形式など）、ドキュメント、コード、どれも歓迎します。

## はじめに

```bash
git clone https://github.com/cty-ut/dandori.git
cd dandori
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,ai]"
pytest                             # すべてのテスト
ruff check src tests               # 静的検査
```

Python 3.11 以上が必要です。CI は Ubuntu・Windows・macOS で動きます。

## 進め方

1. **まず Issue を確認してください。** [`good first issue`](https://github.com/cty-ut/dandori/labels/good%20first%20issue) は初めての方向けです
2. 取り組む Issue にコメントしてください（作業が重ならないように）
3. フォークしてブランチを作り、Pull Request を送ってください
4. CI（テスト・カバレッジ 90% 以上・静的検査）が通ったら、メンテナーが確認してマージします

### 先に相談が必要なもの

段取りは「業務担当者が読める」ことが一番大事です。次の変更は、**コードを書く前に Issue で設計を相談してください**（`needs-design` ラベル）。

- 文法の追加・変更（新しい動詞、助詞の使い方、文の形）
- エラーメッセージの言い回し
- 業務のルール（消込の判定、丸め方、休日の扱いなど）
- AI 助手のリファレンス（`src/dandori_ai/reference/`）

設計の決定は [语法设计.md](语法设计.md) などの設計文書に記録されています（現在は中国語）。

### コードの決まり

- 新しい機能・修正には**テストをつけてください**
- 外部のツール（AI 助手、基盤）は**公開 API（`dandori.api`）だけ**を使います（テストで確認しています）
- 金額・数は `Decimal` を使い、`float` は使いません
- ファイルを読み書きするときは**文字コードを明示**してください（`encoding="utf-8"`）。Windows では既定が cp932 です
- 開発者向けのコメントは現在中国語で書かれています。新しいコメントは日本語・中国語・英語のどれでも構いません。**利用者に見えるもの（エラーメッセージ、表示）は必ず日本語**です

## 行動規範

お互いに敬意を持って接してください。

---

## 简体中文

感谢你对段取り的关注！欢迎报告 Bug、提供测试数据（各银行的 CSV 格式等）、写文档和代码。

### 开始

见上方的命令：`pip install -e ".[dev,ai]"`，然后 `pytest`、`ruff check src tests`。需要 Python 3.11 以上。

### 流程

1. 先看 Issue，[`good first issue`](https://github.com/cty-ut/dandori/labels/good%20first%20issue) 适合第一次参与
2. 在要做的 Issue 下留言，避免重复劳动
3. Fork、建分支、提交 Pull Request
4. CI（测试、覆盖率不低于 90%、静态检查）通过后，由维护者审查并合并

### 需要先讨论的改动

段取り最重要的是"业务人员读得懂"。以下改动请**写代码之前先在 Issue 中讨论设计**（`needs-design` 标签）：语法的增加和修改、错误信息的措辞、业务规则（消込的判定、舍入、休日处理等）、AI 助手的说明书。

### 代码规则

- 新功能和修复都要**附测试**
- 外部工具（AI 助手、平台）**只使用公开 API（`dandori.api`）**
- 金额和数用 `Decimal`，不用 `float`
- 读写文件时**明确指定编码**（Windows 的默认编码是 cp932）
- 开发者注释目前是中文；新的注释用日语、中文、英语都可以。**用户看到的内容（错误信息、显示）一律用日语**
