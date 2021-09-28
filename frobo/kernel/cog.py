import enum

from frobo.kernel.registration import Registration
from frobo.kernel.utilities import camel_to_snakecase, key_starts_with

class CogFeature(enum.IntFlag):
    NONE        = 0
    NO_AUTOLOAD = 1 << 0
    INJECTABLE  = 1 << 1

class CogDict(dict):
    def __missing__(self, key: str):
        if key == 'core' or any(key_starts_with(dep, key) for dep in self['dependencies']):
            return Registration((key,), [], {}, id)
        raise KeyError(key)

class CogMeta(type):
    # NOTE: __new__ should migrate injection annotations
    
    @classmethod
    def __prepare__(meta, name: str, bases: list[type], **kwargs) -> 'CogDict':
        return CogDict(
            name=camel_to_snakecase(name),
            features=CogFeature.NONE,
            dependencies=[],
            required_by=[],
            description=None,
            mounted=False,
        )

    def __new__(meta, name: str, bases: list[type], cls: 'CogMeta', **kwargs) -> 'Cog':
        cls['dependencies'] = [tuple(d.split('.')) for d in cls.get('dependencies', [])]
        cls = super().__new__(meta, name, bases, dict(cls), **kwargs)
        setattr(
            cls,
            'qualname',
            tuple(map(
                camel_to_snakecase, 
                f'{cls.__module__}.{cls.__name__}'.split('.')
            )),
        )
        for key, ann in getattr(cls, '__annotations__', {}).items():
            setattr(cls, key, ann)
        return cls

    def __getattribute__(self, key: str):
        val = super().__getattribute__(key)
        if hasattr(val, '__get__'):
            return val.__get__(self, self.__class__)
        return val

class Cog(metaclass=CogMeta):
    def __init__(self, module):
        self.module = module
        self.core = module.core

    def __getattribute__(self, key: str):
        val = super().__getattribute__(key)
        if hasattr(val, '__get__'):
            return val.__get__(self, self.__class__)
        return val