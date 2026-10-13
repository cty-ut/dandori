"""構文解析：把 Token 排成语法树（语法设计第 2 节）

用的方法叫"递归下降"：语法里的每一条规则写成一个函数，函数之间互相调用。

    プログラム ＝ { 代入文 | 手順文 }                         → program()
    代入文    ＝ 名前 は 名詞句 とする 。                      → _assignment()
    手順文    ＝ 手順 { 、? 手順 } [ 、? 名前 とする ] 。       → _sentence()
    手順     ＝ { 引数句 } 動詞                              → _step()
    引数句    ＝ 名詞句 助詞                                  → _arg()
    名詞句    ＝ 式 { と 式 }                                 → _noun_phrase()
    式       ＝ 項 { (+|-) 項 }                              → _expr()
    項       ＝ 因子 { (×|÷) 因子 }                          → _term()
    因子     ＝ 数 | 文字列 | それ | 名前 | ( 式 ) | - 因子     → _factor()

出错时的策略：一句话里出了错，就跳到下一个「。」，从下一句继续解析。
这样一次就能报出整个程序里的所有错误。
"""

from __future__ import annotations

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.conjugation import Form
from dandori.frontend.source import Source, Span
from dandori.frontend.tokens import Token, TokenKind as K


def parse(tokens: list[Token], source: Source) -> tuple[ast.Program, list[Diagnostic]]:
    return _Parser(tokens, source).program()


class _SentenceError(Exception):
    """这句话没法继续解析了：放弃这句，跳到下一个「。」"""


class _Parser:
    def __init__(self, tokens: list[Token], source: Source):
        self.tokens = tokens
        self.source = source
        self.pos = 0
        self.errors: list[Diagnostic] = []

    # ---------- 文的层次 ----------

    def program(self) -> tuple[ast.Program, list[Diagnostic]]:
        sentences = []
        while self._current().kind is not K.EOF:
            try:
                if self._starts_assignment():
                    sentences.append(self._assignment())
                else:
                    sentences.append(self._sentence())
            except _SentenceError:
                self._skip_to_next_sentence()
        end = self._current().span.end
        return ast.Program(tuple(sentences), Span(0, end)), self.errors

    def _starts_assignment(self) -> bool:
        """以「名前 は」开头的是代入文

        名字写错了（如「答え」）、被断词器标成"不明"时也算，避免连锁报错。
        """
        return (self._current().kind in (K.NAME, K.UNKNOWN)
                and self._peek(1).kind is K.PARTICLE and self._peek(1).text == "は")

    def _assignment(self) -> ast.Assignment:
        """代入文 ＝ 名前 は 名詞句 とする 。"""
        name_token = self._advance()
        self._advance()   # は
        target = ast.NameRef(name_token.value or name_token.text, name_token.span)

        if not self._starts_value(self._current()):
            self._error("E2010", name_token.span, name=name_token.text)
            raise _SentenceError
        value = self._noun_phrase()
        if not self._is_keyword("とする"):
            self._error("E2010", Span(name_token.span.start, value.span.end), name=name_token.text)
            raise _SentenceError
        self._advance()

        self._end_sentence()
        return ast.Assignment(target, value, Span(name_token.span.start, self._previous().span.end))

    def _sentence(self) -> ast.Sentence:
        """手順文 ＝ 手順 { 、? 手順 } [ 、? 名前 とする ] 。"""
        steps = [self._step()]
        bind = None

        while True:
            verb = steps[-1].verb
            token = self._current()

            if token.kind is K.PERIOD:
                self._advance()
                if verb.form is not Form.DICTIONARY:
                    self._error("E2004", verb.span, verb=verb.lemma)
                break

            if token.kind is K.EOF:
                self._error("E2001", self._point_after(steps[-1].span))
                break

            if verb.form is Form.DICTIONARY:
                # 辞书形后面没有「。」，有两种可能：
                #   同一行还有内容 → 多半是想接着写下一步，却忘了用て形
                #   内容在下一行   → 多半是忘了写「。」
                if self._same_line(verb.span, token.span):
                    self._error("E2003", verb.span, verb=verb.lemma)
                else:
                    self._error("E2001", self._point_after(steps[-1].span))
                    break

            if token.kind is K.COMMA:
                self._advance()

            # 句末给结果起名：〜して、合計とする。
            if self._current().kind is K.NAME and self._peek(1).kind is K.KEYWORD:
                name_token = self._advance()
                self._advance()   # とする
                bind = ast.NameRef(name_token.value, name_token.span)
                self._end_sentence()
                break

            steps.append(self._step())

        span = Span(steps[0].span.start, self._previous().span.end)
        return ast.Sentence(tuple(steps), span, bind)

    def _end_sentence(self) -> None:
        """句子在这里必须结束：期待「。」"""
        token = self._current()
        if token.kind is K.PERIOD:
            self._advance()
        elif token.kind is K.EOF or not self._same_line(self._previous().span, token.span):
            self._error("E2001", self._point_after(self._previous().span))
        else:
            self._unexpected(token)
            raise _SentenceError

    def _step(self) -> ast.Step:
        """手順 ＝ { 引数句 } 動詞"""
        start = self._current().span.start
        args = []
        while self._starts_value(self._current()):
            args.append(self._arg())

        token = self._current()
        if token.kind is not K.VERB:
            if args and token.kind in (K.PERIOD, K.EOF, K.COMMA):
                self._error("E2006", Span(start, args[-1].span.end))
            else:
                self._unexpected(token)
            raise _SentenceError

        self._advance()
        verb = ast.Verb(token.verb_id, token.lemma, token.form, token.span)
        return ast.Step(tuple(args), verb, Span(start, token.span.end))

    def _arg(self) -> ast.Arg:
        """引数句 ＝ 名詞句 助詞"""
        value = self._noun_phrase()
        token = self._current()
        if isinstance(value, ast.ErrorExpr) and token.kind is not K.PARTICLE:
            # 不认识的词后面没有助词：多半是写错的动词（如「計算する」），
            # 断词时已经报过错，这里安静地放弃这一句
            raise _SentenceError
        if token.kind is K.COMMA:
            self._error("E2002", token.span)
            raise _SentenceError
        if token.kind is K.KEYWORD:
            # 「合計とする。」「100とする。」：とする 的用法不对
            self._error("E2009", token.span)
            raise _SentenceError
        if token.kind is not K.PARTICLE:
            self._error("E2007", value.span, word=self.source.slice(value.span))
            raise _SentenceError
        self._advance()
        return ast.Arg(token.text, value, Span(value.span.start, token.span.end))

    def _noun_phrase(self) -> ast.Expr:
        """名詞句 ＝ 式 { と 式 }

        「と」有两种意思，要往后多看一个词来区分（语法设计 2.4）：
            100と200を足す   と 后面是值   → "和"，连成列表
            AをBと照合する   と 后面是动词 → 参数槽的助词，这里不处理
        """
        items = [self._expr()]
        while (self._current().kind is K.PARTICLE and self._current().text == "と"
               and self._starts_value(self._peek(1))):
            self._advance()
            items.append(self._expr())

        if len(items) == 1:
            return items[0]
        return ast.ListExpr(tuple(items), Span(items[0].span.start, items[-1].span.end))

    # ---------- 算式的层次：优先级就藏在这里 ----------

    def _expr(self) -> ast.Expr:
        """式 ＝ 項 { (+|-) 項 }    加减在最外层"""
        left = self._term()
        while self._is_operator("+", "-"):
            op = self._advance().value
            right = self._term()
            left = ast.BinaryOp(op, left, right, Span(left.span.start, right.span.end))
        return left

    def _term(self) -> ast.Expr:
        """項 ＝ 因子 { (×|÷) 因子 }    乘除在内一层

        遇到 × 时，这里会先把乘法打包好再交回给 _expr()，
        所以 100+200×3 中的 200×3 会先成为一个整体：乘法先算。
        """
        left = self._factor()
        while self._is_operator("*", "/"):
            op = self._advance().value
            right = self._factor()
            left = ast.BinaryOp(op, left, right, Span(left.span.start, right.span.end))
        return left

    def _factor(self) -> ast.Expr:
        """因子 ＝ 数 | 文字列 | それ | ( 式 ) | - 因子    最内层"""
        token = self._current()

        if token.kind is K.NUMBER:
            self._advance()
            return ast.Number(token.value, token.span)
        if token.kind is K.STRING:
            self._advance()
            return ast.String(token.value, token.span)
        if token.kind is K.PRONOUN:
            self._advance()
            return ast.Sore(token.span)
        if token.kind is K.NAME:
            self._advance()
            return ast.Name(token.value, token.span)
        if token.kind is K.UNKNOWN:
            self._advance()
            return ast.ErrorExpr(token.span)
        if self._is_operator("-"):
            self._advance()
            operand = self._factor()
            return ast.Negate(operand, Span(token.span.start, operand.span.end))
        if token.kind is K.LPAREN:
            self._advance()
            inner = self._expr()          # 括号里重新从最外层开始：括号内整体成为最内层
            if self._current().kind is not K.RPAREN:
                self._error("E2005", token.span)
                raise _SentenceError
            self._advance()
            return inner

        self._unexpected(token)
        raise _SentenceError

    # ---------- 小工具 ----------

    def _current(self) -> Token:
        return self.tokens[self.pos]

    def _peek(self, offset: int) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def _previous(self) -> Token:
        return self.tokens[self.pos - 1]

    def _advance(self) -> Token:
        token = self.tokens[self.pos]
        if token.kind is not K.EOF:
            self.pos += 1
        return token

    def _is_keyword(self, word: str) -> bool:
        token = self._current()
        return token.kind is K.KEYWORD and token.text == word

    def _is_operator(self, *ops: str) -> bool:
        token = self._current()
        return token.kind is K.OPERATOR and token.value in ops

    @staticmethod
    def _starts_value(token: Token) -> bool:
        """这个 Token 能否作为一个值的开头"""
        return (token.kind in (K.NUMBER, K.STRING, K.PRONOUN, K.NAME, K.LPAREN, K.UNKNOWN)
                or (token.kind is K.OPERATOR and token.value == "-"))

    def _same_line(self, a: Span, b: Span) -> bool:
        return self.source.line_col(a.start)[0] == self.source.line_col(b.start)[0]

    @staticmethod
    def _point_after(span: Span) -> Span:
        """指向某段内容的末尾，用于"这里缺了「。」"这类错误"""
        return Span(span.end - 1, span.end)

    def _error(self, code: str, span: Span, **params: str) -> None:
        self.errors.append(Diagnostic.make(code, span, **params))

    def _unexpected(self, token: Token) -> None:
        if token.kind is K.EOF:
            self._error("E2001", self._point_after(self._previous().span))
        elif token.kind is K.COMMA:
            self._error("E2002", token.span)
        else:
            self._error("E2008", token.span, word=token.text)

    def _skip_to_next_sentence(self) -> None:
        while self._current().kind not in (K.PERIOD, K.EOF):
            self._advance()
        self._advance()
