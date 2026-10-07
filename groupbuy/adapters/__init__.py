"""Only explicitly registered platforms are available."""
from .dianping import DianpingAdapter
from ..common import GateError


def get_adapter(platform):
    if platform != 'dianping':
        raise GateError('unsupported_platform')
    return DianpingAdapter()
