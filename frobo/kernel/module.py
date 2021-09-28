import importlib as imp
from pathlib import Path
from typing import Awaitable, Union

from .cog import Cog, CogFeature, CogMeta
from .utilities import Key, camel_to_snakecase

class Module:
    '''
    Represents a loaded Python module

    Automatically loads module and all of its cogs on construction
    '''

    cogs:        dict[str, Cog]
    core:        'Core'
    name:        tuple[str, ...]
    path:        Path
    spec:        imp._bootstrap.ModuleSpec
    module:      'module'
    required_by: list[Key]

    def __init__(self, core: 'Core', path: Path, name: tuple[str, ...]):
        self.cogs = {}
        self.core = core
        self.name = name
        self.path = path
        self.required_by = []

        if isinstance(name, tuple):
            name = '.'.join(name)
        self.spec = imp.util.spec_from_file_location(name, path)
        self.module = imp.util.module_from_spec(self.spec)
        self.spec.loader.exec_module(self.module)

    async def autoload(self) -> Awaitable[None]:
        '''
        Finds all autoloadable cogs in module and loads them
        '''
        for _, value in vars(self.module).items():
            if not isinstance(value, CogMeta):
                continue
            if value.features & CogFeature.NO_AUTOLOAD:
                continue
            await self.mount_cog(value)

    async def mount_cog(self, cog_name: Union[str, CogMeta]) -> Awaitable[Cog]:
        '''
        Mount a cog from this module by name or class after loading its
        dependencies, then fire the core.mount event for that cog, and the
        core.mounted event for all others
        '''
        if isinstance(cog_name, str):
            cog_class = next(filter(
                lambda c: isinstance(c, CogMeta) and c.name == cog_name,
                vars(self.module).values()
            ))
        else:
            cog_class, cog_name = cog_name, cog_name.name
        if cog_name not in self.cogs:
            await self.core.gather(map(lambda name: self.core._ensure(name, (*self.name, cog_name)), cog_class.dependencies))
            self.cogs[cog_name] = self.core.cogs[(*self.name, cog_name)] = cog = cog_class(self)
            await self.core.emit('core.mount', only=[cog.qualname], skip_unmounted=False)
            cog.mounted = True
            await self.core.emit('core.mounted', cog, without=[cog.qualname])
        return self.cogs[cog_name]


    async def unmount_cog(self, cog_name: Union[str, CogMeta, Cog]) -> Awaitable[None]:
        '''
        Unmount a cog from this module after unloading all modules depending on
        it, untagging it from all its dependencies and firing the 
        core.unmount and core.unmounting for, respectively, the unmounted module
        and all others
        '''

        if isinstance(cog_name, CogMeta):
            cog_name = cog_name.name
        if isinstance(cog_name, str):
            if cog_name not in self.cogs:
                return
            cog = self.cogs[cog_name]
        else:
            cog, cog_name = cog_name, cog_name.name

        await self.core.gather(map(self.core.unmount_cog, cog.required_by))
        for dep_name in cog.dependencies:
            dep = self.cogs.get(dep_name, None)
            if dep is not None:
                dep.required_by.remove(cog)
        await self.core.emit('core.unmounting', cog, without=[cog_name])
        await self.core.emit('core.unmount', only=[cog_name])
        try:
            del self.cogs[cog_name]
            del self.core.cogs[(*self.name, cog_name)]
        except KeyError:
            pass