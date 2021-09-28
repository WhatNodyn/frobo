import dataclasses
import functools
from typing import Any

from .utilities import Key

@dataclasses.dataclass
class Registration:
    name:    Key
    args:    tuple[Any, ...]
    kwargs:  dict[str, Any]
    raw:     Any
    wrapped: Any=None

    def __get__(self, instance, base, context={}):
        from frobo.kernel.cog import CogMeta
        if isinstance(instance, CogMeta):
            return self
        if self.wrapped is not None:
            if callable(self.wrapped) and not isinstance(self.raw, type):  
                return functools.partial(self.wrapped, __context__=context)
            return self.wrapped
        elif self.raw is id:
            return instance.core.injectable(self, context=context)

        _wrapped = wrapped = self.raw
        if not isinstance(self.raw, type) and callable(wrapped):
            @functools.wraps(self.raw)
            def _wrapped(*args, __context__={}, **kwargs):
                bind_args = []
                bind_kwargs = {}
                if 'self' in self.raw.__code__.co_varnames:
                    bind_args.append(instance)
                if hasattr(self.raw, '__annotations__'):
                    for key, value in self.raw.__annotations__.items():
                        bind_kwargs[key] = instance.core.injectable(value, context=__context__)
                return wrapped(*bind_args, *args, **bind_kwargs, **kwargs)
        if len(self.name) != 0 and self.name[0] != 'core':
            # TODO: Sort transforms by dependency order
            for transform in instance.core.registered('core.transform', '.'.join(self.name)):
                _wrapped = transform.wrapped(self, _wrapped, *self.args, **self.kwargs)
        

        self.wrapped = _wrapped
        if callable(_wrapped) and not isinstance(self.raw, type):
            return functools.partial(_wrapped, __context__=context)
        return _wrapped

    def __getattr__(self, key: str) -> 'Registration':
        base = self.__getattribute__('name')
        if isinstance(base, str):
            base = base.split('.')
        return Registration(
            (*base, key),
            self.args,
            self.kwargs,
            self.raw,
        )

    def __call__(self, *args, **kwargs):
        if len(args) == 1 and len(kwargs) == 0 and callable(args[0]):
            return Registration(self.name, self.args, self.kwargs, args[0])
        return Registration(self.name, args, kwargs, self.raw)