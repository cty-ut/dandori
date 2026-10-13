"""阶段 1 的动词：足す、引く、掛ける、割る、表示する"""

from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import Registry, VerbSpec


def register(registry: Registry) -> None:
    registry.add_verb(VerbSpec("std.足す", "足す", Conjugation.GODAN))
    registry.add_verb(VerbSpec("std.引く", "引く", Conjugation.GODAN))
    registry.add_verb(VerbSpec("std.掛ける", "掛ける", Conjugation.ICHIDAN))
    registry.add_verb(VerbSpec("std.割る", "割る", Conjugation.GODAN))
    registry.add_verb(VerbSpec("std.表示する", "表示する", Conjugation.SURU))
