"""tinypkg.core — one class with two methods + a free function.

Deterministic call graph (repo-local resolutions only):
    Engine.run   -> util.helper        (imported repo def)
    Engine.run   -> Engine.step        (NOTE: 'step' bare name resolves via the
                                        unique global simple-name 'step')
    top_level    -> util.helper        (imported repo def)

External calls (range(), print()) are dropped, keeping the graph honest.
"""

from .util import helper


class Engine:
    """A two-method class."""

    def step(self, n):
        """Called by run()."""
        return n * 2

    def run(self, n):
        """Calls helper() (import) and step() (unique global name)."""
        total = 0
        for _ in range(n):           # range() = external, dropped
            total = self.step(helper(total))
        return total


def top_level(n):
    """Free function that calls helper()."""
    print(n)                          # print() = external, dropped
    return helper(n)
