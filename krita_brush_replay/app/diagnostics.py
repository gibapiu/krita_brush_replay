
from collections.abc import Callable

IssueReporter = Callable[[str, BaseException], None]


def ignore_issue(operation: str, error: BaseException) -> None:
    pass
