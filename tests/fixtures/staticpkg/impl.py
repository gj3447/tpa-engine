from .base import Base, traced

MODULE_CONST = 7


class Worker(Base):
    @traced
    def run(self):
        local_value = MODULE_CONST
        return helper(local_value)


def helper(value):
    return value + 1
