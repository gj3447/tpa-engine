class Base:
    def run(self):
        return "base"


def traced(fn):
    return fn
