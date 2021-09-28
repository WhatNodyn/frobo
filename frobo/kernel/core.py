import asyncio
import functools
import itertools
import os
import sys
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Iterator, NoReturn, Optional, Union

from .cog import Cog, CogFeature, CogMeta
from .exc import BadModule, ProcessTerminated
from .module import Module
from .registration import Registration
from .utilities import Key, is_dict_subset, is_list_prefix

CORE_PATH = Path(__file__).parent.parent

class Core:
    '''
    Manages modules, cogs and communication between them

    Decorators:
        core.event: Registers a handler on the common event system
            By the nature of this system, events may have multiple handlers

        core.injectable: Defines an injectable that may be used as a function or
            class annotation, ensuring the presence of these values if possible

        core.transform: Defines a transform to apply to registrations of a
            specific types

    Events:
        core.mount: Fired for the concerned module when it has been inserted in
        the core

        core.mounted: Fired for other modules when a module has been inserted in
        the core

        core.unmounting: Fired for other modules before a module is removed from
        the core

        core.unmount: Fired for the concerned module before it is removed from
        the core
    '''

    cogs: dict[tuple[str], Cog]
    loop: asyncio.AbstractEventLoop
    modules: dict[tuple[str], Module]
    path: list[Path]

    def __init__(self, path: list[Path]=[], loop: Optional[asyncio.AbstractEventLoop]=None):
        self.cogs = {}
        self.loop = loop or asyncio.get_event_loop()
        self.modules = {}
        self.path = path.copy()
        self.path.append(CORE_PATH / 'modules')

    def run(self, args: list[str]=sys.argv[1:], exit: bool=True) -> Union[NoReturn, int]:
        '''
        Index all modules and starts running forever, unless a module exits the
        program or Ctrl-C is hit, ensures proper shutdown too
        '''
        code = 0
        try:
            self.loop.run_until_complete(self._start(args))
            self.loop.run_forever()
        except ProcessTerminated as e:
            code = e.code
        except KeyboardInterrupt:
            pass
        self.loop.run_until_complete(self._close())
        self.loop.stop()
        if exit:
            sys.exit(code)
        else:
            return code

    async def _start(self, args=sys.argv[1:]):
        await self.index_modules()
        cli = await self.mount_cog('cli.parser')

        await cli.run(*args)

    async def _close(self) -> Awaitable[None]:
        await self.gather(map(self.unmount_cog, self.cogs))

    def exit(self, code: int=0) -> NoReturn:
        '''
        Schedule the program for exit with the provided exit code
        '''
        raise ProcessTerminated(code)


    async def index_modules(self) -> Awaitable[None]:
        '''
        Search for cogs in all directories of the path.

        Locates any Python packages (directories with an __init__.py) or modules
        outside of a package and attempts to load them.
        '''

        excluded = set()
        for base in self.path:
            for path, _, files in os.walk(base):
                path = Path(path)
                if path.name == '__pycache__':
                    excluded.add(path)
                    continue
                relative_path = path.relative_to(base)

                if any(path.is_relative_to(p) for p in excluded):
                    continue
                if '__init__.py' in files:
                    await self.load_module(path / '__init__.py', relative_path.parts)
                    excluded.add(path)
                else:
                    for file in files:
                        name = (relative_path / file).with_suffix('').parts
                        await self.load_module(path / file, name)

    async def load_module(self, path: Optional[Path]=None, name: Optional[Key]=None) -> Awaitable[Module]:
        '''
        Attempt to load a module from its path, name or both, returning the
        existing module if existing
        '''
        if isinstance(name, str):
            name = tuple(name.split('.'))
        if name in self.modules:
            return self.modules[name]
        return await self.reload_module(name=name)

    async def reload_module(self, path: Optional[Path]=None, name: Optional[Key]=None) -> Awaitable[Module]:
        '''
        Attempt to load a module from its path, name or both, reloading it if it
        already is loaded
        '''

        if path is None and name is None:
            raise ValueError('One of path or name must be provided')
        if name is None:
            if isinstance(path, str):
                path = Path(path).expanduser().absolute()
            for base in self.path:
                if path.is_relative_to(base):
                    name = path.relative_to(base)
                    break
            if name is None:
                raise BadModule('Cannot determine module name')
        elif path is None:
            if isinstance(name, str):
                name = tuple(name.split('.'))
            for base in self.path:
                base = base.resolve()
                pkg_path = base / Path(*name) / '__init__.py'
                mod_path = (base / Path(*name)).with_suffix('.py')
                if pkg_path.exists():
                    path = pkg_path
                    break
                elif mod_path.exists():
                    path = mod_path
            if path is None:
                raise BadModule('Cannot determine module source')
        if name in self.modules:
            await self.unload_module(self.modules[name])
        mod = self.modules[name] = Module(self, path, name)
        await mod.autoload()
        return mod

    async def unload_module(self, name: Union[Module, Key]) -> Awaitable[None]:
        '''
        Forget a module after unloading all of its loaded cogs
        '''
        if isinstance(name, str):
            name = tuple(name.split('.'))
        if isinstance(name, tuple):
            if name not in self.modules:
                return
            mod = self.modules[name]
        else:
            mod, name = name, name.name
        await self.gather(map(self.unmount_cog, mod.required_by))
        await self.gather(map(mod.unmount_cog, mod.cogs))
        del self.modules[name]

    async def mount_cog(self, name: Union[CogMeta, Key]) -> Awaitable[Cog]:
        '''
        Mount a cog, loading the corresponding module if necessary
        '''
        mod = None
        if isinstance(name, CogMeta):
            mod = await self.load_module(name=name.qualname[:-1])
        if isinstance(name, str):
            name = tuple(name.split('.'))
        if isinstance(name, tuple):
            try:
                mod = await self.load_module(name=name[:-1])
            except BadModule:
                return await self.load_module(name=name)
            name = name[-1]

        return await mod.mount_cog(name)

    async def unmount_cog(self, name: Union[Cog, CogMeta,  Key]) -> Awaitable[None]:
        '''
        Unmount a cog if it is loaded
        '''
        if isinstance(name, Cog):
            name = name.__class__.qualname
        if isinstance(name, CogMeta):
            name = name.qualname
        if isinstance(name, str):
            name = tuple(name.split('.'))
        mod = self.modules.get(name[:-1], None)
        if mod is None:
            return
        await mod.unmount_cog(name[-1])

    async def _ensure(self, name: Union[CogMeta, Key], source: Key) -> Awaitable[None]:
        '''
        Load a cog as a dependency and add the requester to that cog's list of
        requesting cogs
        '''
        cog = await self.mount_cog(name)
        if isinstance(source, str):
            source = tuple(source.split('.'))
        cog.required_by.append(source)


    @functools.wraps(asyncio.gather)
    def gather(self, *args, **kwargs):
        '''
        Wraps asyncio.gather using the core's loop

        Mostly a readability helper
        '''

        if len(args) == 1 and hasattr(args[0], '__iter__'):
            args = args[0]
        return asyncio.gather(*args, **kwargs, loop=self.loop)

    def registered(
        self,
        name: Key,
        *filter_args,
        filter_kwargs: dict[str, Any]={},
        context_injectables:dict[Key, Any]={},
        only: Optional[list[Key]]=None,
        without: Optional[list[Key]]=None,
        skip_unmounted: bool=True,
    ) -> Iterator[Any]:
        '''
        Find all registrations for a given name and set of filters and return
        them after loading their wrapped versions
        '''
        found_registrations = []

        if isinstance(name, str):
            name = tuple(name.split('.'))
        for cog_name, cog in self.cogs.items():
            if skip_unmounted and not cog.mounted:
                continue
            if only is not None and cog_name not in only:
                continue
            if without is not None and cog_name in without:
                continue
            for key in cog.__class__.__dict__.keys():
                if key.startswith('__') and key.endswith('__'):
                    continue
                reg = cog.__class__.__dict__.get(key, None)
                if not isinstance(reg, Registration):
                    continue
                if reg.name != name \
                or not is_list_prefix(filter_args, reg.args) \
                or not is_dict_subset(filter_kwargs, reg.kwargs):
                    continue
                else:
                    found_registrations.append((cog, reg))

        for cog, reg in found_registrations:
            yield Registration(reg.name, reg.args, reg.kwargs, reg.raw, reg.__get__(cog, cog.__class__, context_injectables))

    def injectable(self, reg: Registration, context: Optional[dict[Key, Any]]={}) -> Any:
        for key, value in context.items():
            if isinstance(key, str):
                key = tuple(key.split('.'))
            if key == reg.name:
                return value
        res = list(self.registered(
            'core.injectable',
            '.'.join(reg.name),
            context_injectables=context,
        ))
        if len(res) != 0:
            return res[0].wrapped(*reg.args, **reg.kwargs)
        return self.cogs.get(reg.name, None)

    async def invoke(
        self,
        name: Key,
        *handler_args,
        handler_kwargs: dict[str, Any]={},
        filter_args: list[Any]=[],
        filter_kwargs: dict[str, Any]={},
        context_injectables: dict[Key, Any]={},
        only: Optional[list[Key]]=None,
        without: Optional[list[Key]]=None,
        skip_unmounted: bool=True,
        **kwargs
    ) -> Awaitable[list[Any]]:
        return await self.gather(map(

            lambda x: x.wrapped(*handler_args, **handler_kwargs),
            self.registered(
                name,
                *filter_args,
                filter_kwargs=filter_kwargs,
                context_injectables=context_injectables,
                only=only,
                without=without,
                skip_unmounted=skip_unmounted,
            ),
        ), **kwargs)

    async def emit(
        self,
        name: Key,
        *handler_args,
        handler_kwargs: dict[str, Any]={},
        filter_args: list[Any]=[],
        filter_kwargs: dict[str, Any]={},
        context_injectables: dict[Key, Any]={},
        only: Optional[list[Key]]=None,
        without: Optional[list[Key]]=None,
        skip_unmounted: bool=True,
        **kwargs,
    ) -> NotImplemented:
        if isinstance(name, tuple):
            name = '.'.join(name)
        return await self.invoke(
            'core.event',
            *handler_args,
            handler_kwargs=handler_kwargs,
            filter_args=[name, *filter_args],
            filter_kwargs=filter_kwargs,
            context_injectables=context_injectables,
            only=only,
            without=without,
            skip_unmounted=skip_unmounted,
            **kwargs,
        )