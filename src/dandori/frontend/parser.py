"""構文解析：把 Token 排成语法树（语法设计第 2 节）

用的方法叫"递归下降"：语法里的每一条规则写成一个函数，函数之间互相调用。

    プログラム ＝ { 文 }                                    → program()
    文       ＝ もし文 | 繰り返し文 | 代入文 | 手順文           → _statement()
    もし文    ＝ もし 条件 ならば 、? { 文 }                    → _if()
                { そうでなく 条件 ならば 、? { 文 } }
                [ そうでなければ 、? { 文 } ] ここまで 。
    繰り返し文 ＝ 式 回繰り返して 、? { 文 } ここまで 。          → _loop()
               | 名前を 式から 式まで 繰り返して 、? { 文 } ここまで 。
               | 式 のそれぞれについて 、? { 文 } ここまで 。      → _each()
               | 繰り返しをやめる 。 | 次に進む 。
    条件     ＝ 比較 { かつ 比較 } | 比較 { または 比較 }        → _condition()
    比較     ＝ 式 比較記号 式 | 式 が 式 [以上|以下|…]          → _comparison()
    代入文    ＝ 名前 は 名詞句 とする 。                      → _assignment()
    設定文    ＝ 名前 の 名前 は 、? 名詞句 [まで] とする 。      → _setting()
    手順文    ＝ 手順 { 、? 手順 } [ 、? 名前 とする ] 。       → _sentence()
    手順     ＝ { 引数句 } 動詞                              → _step()
    引数句    ＝ ( 条件名詞 | 名詞句 ) 助詞                      → _arg()
    条件名詞  ＝ 条件 ( のもの | もの )                          （金額が10000以上のもの）
    名詞句    ＝ 式 { と 式 }                                 → _noun_phrase()
    式       ＝ 項 { (+|-) 項 }                              → _expr()
    項       ＝ 因子 { (×|÷) 因子 }                          → _term()
    因子     ＝ 基本因子 { の ( 名前 | 文字列 | 空欄 | 大きい順 | 小さい順 ) }   → _factor()
    基本因子  ＝ 数 | 文字列 | それ | その 列名 | 名前 | 空欄 | ( 式 ) | - 因子 → _primary()

出错时的策略：一句话里出了错，就跳到下一个「。」，从下一句继续解析。
这样一次就能报出整个程序里的所有错误。
跳过时遇到「そうでなく」「ここまで」等块的关键字就停下，保证もし文的结构不被破坏。
"""

from __future__ import annotations

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.conjugation import Form
from dandori.frontend.normalize import normalize
from dandori.frontend.source import Source, Span
from dandori.frontend.tokens import Token, TokenKind as K

_BLOCK_KEYWORDS = ("そうでなく", "そうでなければ", "ここまで")
_LOOP_KEYWORDS = ("繰り返して", "回繰り返して")
# 判断一句话是不是繰り返し文时，遇到这些就可以停止往后看
_NOT_LOOP_KEYWORDS = ("もし", "ならば", "とする", "のそれぞれについて") + _BLOCK_KEYWORDS
_JUMP_KEYWORDS = ("繰り返しをやめる", "次に進む")
_COMPARISON_KEYWORDS = {"以上": ">=", "以下": "<=", "未満": "<", "を超える": ">", "でない": "<>",
                        # 日付用（语法设计 13.4）
                        "以降": ">=", "以前": "<=", "より前": "<", "より後": ">"}
_MONO_KEYWORDS = ("のもの", "もの")
_SORT_KEYWORDS = {"大きい順": True, "小さい順": False}   # 是否降序


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
        self.each_depth = 0     # 逐行处理的嵌套深度：其中もし条件主语位置的「」表示列名

    # ---------- 文的层次 ----------

    def program(self) -> tuple[ast.Program, list[Diagnostic]]:
        statements = self._statements(top_level=True)
        end = self._current().span.end
        return ast.Program(tuple(statements), Span(0, end)), self.errors

    def _statements(self, top_level: bool) -> list[ast.Statement]:
        """一串文。在块内遇到「そうでなく」「そうでなければ」「ここまで」就停下，交给 _if() 处理"""
        statements = []
        while self._current().kind is not K.EOF:
            if self._at_block_keyword():
                if not top_level:
                    break
                token = self._advance()
                opener = "「もし」や「繰り返して」" if token.text == "ここまで" else "「もし」"
                self._error("E2013", token.span, word=token.text, opener=opener)
                self._skip_to_next_sentence()
                continue
            try:
                statements.append(self._statement())
            except _SentenceError:
                self._skip_to_next_sentence()
        return statements

    def _statement(self) -> ast.Statement:
        if self._is_keyword("もし"):
            return self._if()
        if self._current().kind is K.KEYWORD and self._current().text in _JUMP_KEYWORDS:
            token = self._advance()
            self._end_sentence()
            node = ast.Break if token.text == "繰り返しをやめる" else ast.Continue
            return node(token.span)
        if self._starts_each():
            return self._each()
        if self._starts_loop():
            return self._loop()
        if self._starts_setting():
            return self._setting()
        if self._starts_assignment():
            return self._assignment()
        return self._sentence()

    # ---------- 設定文 ----------

    def _starts_setting(self) -> bool:
        """以「名前 の 名前 は」开头的是設定文：消込の手数料は、…とする。"""
        t = self.tokens[self.pos:self.pos + 4]
        return (len(t) == 4 and t[0].kind is K.NAME and t[1].kind is K.PARTICLE and t[1].text == "の"
                and t[2].kind is K.NAME and t[3].kind is K.PARTICLE and t[3].text == "は")

    def _setting(self) -> ast.Setting:
        """設定文 ＝ 名前 の 名前 は 、? 名詞句 [まで] とする 。"""
        namespace = self._advance()
        self._advance()                      # の
        key = self._advance()
        self._advance()                      # は
        if self._current().kind is K.COMMA:
            self._advance()
        label = f"{namespace.value}の{key.value}"
        if not self._starts_value(self._current()):
            self._error("E2010", key.span, name=label)
            raise _SentenceError
        value = self._noun_phrase()
        up_to = False
        if self._current().kind is K.PARTICLE and self._current().text == "まで":
            self._advance()
            up_to = True
        if not self._is_keyword("とする"):
            self._error("E2010", Span(namespace.span.start, value.span.end), name=label)
            raise _SentenceError
        self._advance()
        self._end_sentence()
        return ast.Setting(namespace.value, key.value, value, up_to, key.span,
                           Span(namespace.span.start, self._previous().span.end))

    # ---------- 繰り返し文 ----------

    def _starts_each(self) -> bool:
        """跳过构成值的词之后，紧接着是「のそれぞれについて」"""
        i = self.pos
        while (self.tokens[i].kind in (K.NAME, K.PRONOUN, K.STRING, K.LPAREN, K.RPAREN, K.UNKNOWN)
               or (self.tokens[i].kind is K.PARTICLE and self.tokens[i].text == "の")):
            i += 1
        token = self.tokens[i]
        return token.kind is K.KEYWORD and token.text == "のそれぞれについて"

    def _each(self) -> ast.EachLoop:
        """明細のそれぞれについて、…ここまで。"""
        start = self._current().span.start
        table = self._expr()
        keyword = self._current()
        if not self._is_keyword("のそれぞれについて"):
            self._error("E2021", Span(start, keyword.span.end))
            raise _SentenceError
        self._advance()
        if self._current().kind is K.COMMA:
            self._advance()
        self.each_depth += 1
        body = tuple(self._statements(top_level=False))
        self.each_depth -= 1
        if self._is_keyword("ここまで"):
            self._advance()
            self._end_sentence()
        else:
            self._error("E2016", keyword.span, opener="それぞれについて")
        return ast.EachLoop(table, body, Span(start, self._previous().span.end))

    def _starts_loop(self) -> bool:
        """从句首往后看：在遇到动词之前先遇到「繰り返して」，就是繰り返し文"""
        for token in self.tokens[self.pos:]:
            if token.kind is K.KEYWORD and token.text in _LOOP_KEYWORDS:
                return True
            if (token.kind in (K.VERB, K.PERIOD, K.EOF)
                    or (token.kind is K.KEYWORD and token.text in _NOT_LOOP_KEYWORDS)):
                return False
        return False

    def _loop(self) -> ast.CountLoop | ast.RangeLoop:
        """3回繰り返して、…ここまで。　／　月を1から12まで繰り返して、…ここまで。"""
        start = self._current().span.start
        args: list[ast.Arg] = []
        count = None

        while self._starts_value(self._current()):
            value = self._noun_phrase()
            token = self._current()
            if self._is_keyword("回繰り返して"):
                count = value
                break
            if token.kind is not K.PARTICLE:
                self._error("E2017", value.span)
                raise _SentenceError
            self._advance()
            args.append(ast.Arg(token.text, value, Span(value.span.start, token.span.end)))

        keyword = self._current()
        if count is not None:
            if args:                                   # 「月を3回繰り返して」
                self._error("E2017", Span(start, keyword.span.end))
            self._advance()
            header = None
        elif self._is_keyword("繰り返して"):
            self._advance()
            header = self._range_header(args, Span(start, keyword.span.end))
        else:
            self._error("E2017", Span(start, keyword.span.end))
            raise _SentenceError

        if self._current().kind is K.COMMA:
            self._advance()
        body = tuple(self._statements(top_level=False))

        if self._is_keyword("ここまで"):
            self._advance()
            self._end_sentence()
        else:
            self._error("E2016", keyword.span, opener="繰り返して")
        span = Span(start, self._previous().span.end)

        if count is not None:
            return ast.CountLoop(count, body, span)
        if header is None:                             # 范围写法有误，已经报过错
            return ast.CountLoop(ast.ErrorExpr(keyword.span), body, span)
        var, first, last = header
        return ast.RangeLoop(var, first, last, body, span)

    def _range_header(self, args: list[ast.Arg], span: Span):
        """范围写法：助词须恰好为 を（名字）、から、まで，顺序自由"""
        by_particle = {arg.particle: arg.value for arg in args}
        target = by_particle.get("を")
        if (len(args) != 3 or set(by_particle) != {"を", "から", "まで"}
                or not isinstance(target, ast.Name)):
            self._error("E2017", span)
            return None
        return ast.NameRef(target.name, target.span), by_particle["から"], by_particle["まで"]

    # ---------- もし文 ----------

    def _if(self) -> ast.If:
        """もし 条件 ならば、{文} { そうでなく 条件 ならば、{文} } [ そうでなければ、{文} ] ここまで。"""
        moshi = self._advance()
        branches = [self._branch(moshi, with_condition=True)]
        while self._is_keyword("そうでなく"):
            branches.append(self._branch(self._advance(), with_condition=True))
        if self._is_keyword("そうでなければ"):
            branches.append(self._branch(self._advance(), with_condition=False))

        if self._is_keyword("ここまで"):
            self._advance()
            self._end_sentence()
        else:
            self._error("E2012", moshi.span)
        return ast.If(tuple(branches), Span(moshi.span.start, self._previous().span.end))

    def _branch(self, keyword: Token, with_condition: bool) -> ast.Branch:
        condition = self._branch_condition() if with_condition else None
        if self._current().kind is K.COMMA:
            self._advance()
        body = self._statements(top_level=False)
        return ast.Branch(condition, tuple(body), Span(keyword.span.start, self._previous().span.end))

    def _branch_condition(self) -> ast.Condition:
        """条件 ならば。条件写错时跳到「ならば」，继续解析分支的内容，保住整个もし文的结构"""
        try:
            condition = self._condition()
            if self.each_depth > 0:
                # 逐行处理中：「支払い期日」が空欄ならば → 当前行的列（与「〜のもの」相同，语法设计 10.3）
                condition = _columns_as_subjects(condition)
        except _SentenceError:
            while not (self._is_keyword("ならば") or self._current().kind in (K.PERIOD, K.EOF)
                       or self._at_block_keyword()):
                self._advance()
            # 用一个"不明"的比较占位，后续阶段不会再为它报错
            span = self._current().span
            condition = ast.Comparison("=", ast.ErrorExpr(span), ast.ErrorExpr(span), span)

        if self._is_keyword("ならば"):
            self._advance()
        elif self._current().kind is K.UNKNOWN:
            self._advance()        # 「なら」等，断词时已经报过 E1009
        elif self._naraba_ahead():
            # 条件后面还有看不懂的内容，但再往后能找到「ならば」：报一次错，跳到「ならば」
            self._error("E2015", self._current().span)
            while not self._is_keyword("ならば"):
                self._advance()
            self._advance()
        else:
            self._error("E2011", self._point_after(self._previous().span))
        return condition

    def _naraba_ahead(self) -> bool:
        """在这句话结束之前，后面还有没有「ならば」"""
        for token in self.tokens[self.pos:]:
            if token.kind is K.KEYWORD and token.text == "ならば":
                return True
            if token.kind in (K.PERIOD, K.EOF) or (token.kind is K.KEYWORD and token.text in _BLOCK_KEYWORDS):
                return False
        return False

    def _condition(self) -> ast.Condition:
        """条件 ＝ 比較 { かつ 比較 } | 比較 { または 比較 }，不能混用（语法设计 6.3）"""
        items = [self._comparison()]
        op = None
        while self._is_keyword("かつ") or self._is_keyword("または"):
            token = self._advance()
            if op is None:
                op = token.text
            elif token.text != op:
                self._error("E2014", token.span)
            items.append(self._comparison())

        if len(items) == 1:
            return items[0]
        return ast.Logical(op, tuple(items), Span(items[0].span.start, items[-1].span.end))

    def _comparison(self) -> ast.Comparison:
        """比較 ＝ 式 比較記号 式 | 式 が 式 [以上|以下|未満|を超える|でない]"""
        if not self._starts_value(self._current()):
            self._error("E2015", self._current().span)
            raise _SentenceError
        left = self._expr()
        token = self._current()

        if token.kind is K.COMPARE:                     # 合計>=100
            self._advance()
            right = self._expr()
            return ast.Comparison(token.value, left, right, Span(left.span.start, right.span.end))

        if token.kind is K.KEYWORD and token.text in ("が休日", "が営業日"):   # 期日が休日（でない）
            self._advance()
            op = "="
            if self._is_keyword("でない"):
                op = "<>"
                self._advance()
            right = ast.DayKind(token.text[1:], token.span)
            return ast.Comparison(op, left, right, Span(left.span.start, self._previous().span.end))

        if token.kind is K.PARTICLE and token.text == "が":   # 合計が100以上
            self._advance()
            if not self._starts_value(self._current()):
                self._error("E2015", self._current().span)
                raise _SentenceError
            right = self._expr()
            word = self._current()
            op, used = "=", None                        # 合計が0ならば：没有比较词就是"等于"
            if word.kind is K.KEYWORD and word.text in _COMPARISON_KEYWORDS:
                op, used = _COMPARISON_KEYWORDS[word.text], word.text
                self._advance()
            return ast.Comparison(op, left, right, Span(left.span.start, self._previous().span.end), used)

        self._error("E2015", left.span)
        raise _SentenceError

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
        """引数句 ＝ ( 条件名詞 | 名詞句 ) 助詞"""
        if self._row_condition_ahead():
            value = self._row_filter()
        else:
            value = self._noun_phrase()
        token = self._current()
        if token.kind is K.UNKNOWN or (isinstance(value, ast.ErrorExpr) and token.kind is not K.PARTICLE):
            # 值后面是不认识的词（「3回繰り返す」），或者不认识的词后面没有助词（「100を計算する」）：
            # 多半是写错的动词或关键字，断词时已经报过错，这里安静地放弃这一句
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

    def _row_condition_ahead(self) -> bool:
        """这个参数是不是条件名词（金額が10000以上のもの）？

        从参数开头往后看：跳过构成值的词之后，第一个结构性的词是「が」或比较符号，
        而且在动词、句号之前出现「のもの」或「もの」。
        「明細から金額が…のもの」中，从「明細」开始看时第一个结构性的词是「から」，所以不是。
        """
        i = self.pos
        while (self.tokens[i].kind in (K.NAME, K.NUMBER, K.DATE, K.STRING, K.PRONOUN, K.OPERATOR,
                                       K.LPAREN, K.RPAREN, K.UNKNOWN)
               or (self.tokens[i].kind is K.KEYWORD and self.tokens[i].text == "今日")
               or (self.tokens[i].kind is K.PARTICLE and self.tokens[i].text == "の")):
            i += 1
        first = self.tokens[i]
        if not (first.kind is K.COMPARE or (first.kind is K.PARTICLE and first.text == "が")
                or (first.kind is K.KEYWORD and first.text in ("が休日", "が営業日"))):
            return False
        for token in self.tokens[i:]:
            if token.kind is K.KEYWORD and token.text in _MONO_KEYWORDS:
                return True
            if token.kind in (K.VERB, K.PERIOD, K.EOF, K.COMMA) or (
                    token.kind is K.KEYWORD and token.text in _NOT_LOOP_KEYWORDS + _LOOP_KEYWORDS):
                return False
        return False

    def _row_filter(self) -> ast.RowFilter:
        """条件名詞 ＝ 条件 ( のもの | もの )"""
        start = self._current().span.start
        condition = self._condition()
        token = self._current()
        if not (token.kind is K.KEYWORD and token.text in _MONO_KEYWORDS):
            self._error("E2020", Span(start, self._previous().span.end))
            raise _SentenceError
        self._advance()
        return ast.RowFilter(_columns_as_subjects(condition), Span(start, token.span.end))

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
        """因子 ＝ 基本因子 { の ( 名前 | 文字列 ) }

        「の」取表的列：明細の金額、明細の「支払い期日」（语法设计 8.4）
        """
        expr = self._primary()
        while self._current().kind is K.PARTICLE and self._current().text == "の":
            no = self._advance()
            token = self._current()
            if token.kind is K.KEYWORD and token.text in _SORT_KEYWORDS:      # 金額の大きい順
                self._advance()
                return ast.SortKey(expr, _SORT_KEYWORDS[token.text], Span(expr.span.start, token.span.end))
            if token.kind is K.KEYWORD and token.text == "空欄":              # 明細の金額の空欄
                self._advance()
                return ast.BlanksOf(expr, Span(expr.span.start, token.span.end))
            if token.kind is K.NAME:
                column = token.value
            elif token.kind is K.STRING:
                column = normalize(token.value)     # 列名经过正規化：ＩＤ 和 ID 是同一列
            else:
                self._error("E2018", no.span)
                raise _SentenceError
            self._advance()
            expr = ast.ColumnRef(expr, column, token.span, Span(expr.span.start, token.span.end))
        return expr

    def _primary(self) -> ast.Expr:
        """基本因子 ＝ 数 | 文字列 | それ | その 列名 | 名前 | 空欄 | ( 式 ) | - 因子    最内层"""
        token = self._current()

        if token.kind is K.PRONOUN and token.text == "その":
            # その金額 ＝ それの金額（语法设计 9.5）
            self._advance()
            column = self._current()
            if column.kind not in (K.NAME, K.STRING):
                self._error("E2019", token.span)
                raise _SentenceError
            self._advance()
            name = column.value if column.kind is K.NAME else normalize(column.value)
            return ast.ColumnRef(ast.Sore(token.span), name, column.span,
                                 Span(token.span.start, column.span.end))
        if token.kind is K.KEYWORD and token.text == "空欄":
            self._advance()
            return ast.Blank(token.span)

        if token.kind is K.NUMBER:
            self._advance()
            return ast.Number(token.value, token.span)
        if token.kind is K.DATE:
            self._advance()
            return ast.DateLiteral(token.value, token.span)
        if token.kind is K.KEYWORD and token.text == "今日":
            self._advance()
            return ast.Today(token.span)
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
        return (token.kind in (K.NUMBER, K.DATE, K.STRING, K.PRONOUN, K.NAME, K.LPAREN, K.UNKNOWN)
                or (token.kind is K.KEYWORD and token.text in ("空欄", "今日"))
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

    def _at_block_keyword(self) -> bool:
        token = self._current()
        return token.kind is K.KEYWORD and token.text in _BLOCK_KEYWORDS

    def _skip_to_next_sentence(self) -> None:
        """跳到下一个「。」之后。遇到块的关键字就停下，不吞掉它"""
        while self._current().kind not in (K.PERIOD, K.EOF) and not self._at_block_keyword():
            self._advance()
        if self._current().kind is K.PERIOD:
            self._advance()


def _columns_as_subjects(condition: ast.Condition) -> ast.Condition:
    """「〜のもの」中，主语位置（「が」的前面）的「」表示列名（语法设计 9.4）

        「支払い期日」が空欄のもの   →  列「支払い期日」
        区分が「大口」のもの         →  「大口」是值，不变
    """
    if isinstance(condition, ast.Logical):
        return ast.Logical(condition.op, tuple(_columns_as_subjects(c) for c in condition.items),
                           condition.span)
    left = condition.left
    if isinstance(left, ast.String):
        left = ast.Name(normalize(left.value), left.span)
    return ast.Comparison(condition.op, left, condition.right, condition.span, condition.word)
