import frobo
import pathlib

class Command(frobo.Cog):
    dependencies = ['cli']

    cli: cli.parser

    @cli.command('autorun', 'Run init if it never has been, then start')
    async def autorun(self):
        path = pathlib.Path.cwd() / '.frobo_initialized'
        if not path.exists():
            path.touch()
            await self.cli.run('init', should_exit=False, terse=True)
        await self.cli.run('start', terse=True)