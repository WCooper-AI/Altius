"""YAML in and out without aliases.

``yaml.safe_load`` keeps an alias (``*name``) as a shared reference, so a few hundred bytes of
nested aliases parse into a small object that turns into hundreds of millions of items once
anything serialises or walks it (the "billion laughs" pattern). Nothing Trading Alerts writes
uses aliases, so they are refused on the way in and never written on the way out.
"""

from __future__ import annotations

from typing import Any

import yaml
from yaml.composer import ComposerError

ALIAS_PROBLEM = "aliases (*name) aren't supported here; write the value out in full"
DEPTH_PROBLEM = "it's nested too deeply to read"


class _NoAliasLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ComposerError(None, None, ALIAS_PROBLEM, self.peek_event().start_mark)
        return super().compose_node(parent, index)


class _NoAliasDumper(yaml.SafeDumper):
    def ignore_aliases(self, data) -> bool:
        return True


def load(text: str | bytes) -> Any:
    """``yaml.safe_load`` that raises ``yaml.YAMLError`` on any alias, and on nesting too deep
    for PyYAML's recursive parser (which would otherwise escape as a ``RecursionError``)."""
    try:
        return yaml.load(text, Loader=_NoAliasLoader)
    except RecursionError:
        raise ComposerError(None, None, DEPTH_PROBLEM, None) from None


def dump(data: Any, stream=None, **kwargs) -> Any:
    """``yaml.safe_dump`` that writes shared values out in full rather than as aliases."""
    return yaml.dump(data, stream, Dumper=_NoAliasDumper, **kwargs)
