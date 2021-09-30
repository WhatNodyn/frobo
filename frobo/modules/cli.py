import asyncio
import frobo
import sys

class Parser(frobo.Cog):
    async def help(self, exit_code=0):
        commands = {'help': ['List available commands']}
        for registration in self.core.registered('cli.command'):
            name = registration.args[0] if len(registration.args) > 0 else registration.handle.__name__
            cmd = commands.setdefault(name, [])
            if len(registration.args) > 1:
                cmd.append(registration.args[1])

        modules = "\n    ".join(map(".".join, self.core.cogs.keys()))
        print(f'Loaded Cogs\n    {modules}\n')
        
        print('Available CLI commands')
        max_name_len = max(map(len, commands.keys()))
        for name, desc in sorted(commands.items()):
            full_desc = f'\n    {" " * max_name_len}   '.join(sorted(desc))
            print(f'    {name}{" " * (max_name_len - len(name))} - {full_desc}')
        if exit_code is not None:
            await self.core.exit(exit_code)

    async def run(self, command: str='help', *args, should_exit=True, terse=False, **kwargs):
        if not terse:
            print('\033[90mFrobo starting up...\033[0m')
        if command == 'help':
            await self.help(0 if exit else None)
        else:
            daemon = False
            found = False
            processes = []
            for reg in self.core.registered('cli.command', command, *kwargs.get('filters', []), **kwargs):
                daemon = daemon or reg.kwargs.get('daemon', True)
                found = True
                processes.append(reg.wrapped(*args))
            if not found:
                print(f'\033[91;1mUnknown command: {command}\033[0m')
                await self.help(1)
            await asyncio.gather(*processes, loop=self.core.loop)
            if not daemon and should_exit:
                self.core.loop.stop()

    @core.injectable('cli.args')
    def args(self):
        return sys.argv[2:]
