import aiohttp.web
import frobo

class Server(frobo.Cog):
    dependencies = ['cli', 'config']

    config: config.manager

    @core.event('core.mount')
    async def on_mount(self):
        self.app = aiohttp.web.Application()

    @core.event('core.unmount')
    async def on_unmount(self):
        if hasattr(self, 'site'):
            await self.site.stop()

    @core.event('core.mounted')
    async def on_mounted(self, cog):
        # NOTE: We will need to redo this to implement unmounting
        for reg in self.core.registered('web.route', only=[cog.qualname]):
            self.app.router.add_route(*reg.args, reg.wrapped, **reg.kwargs)

    # TODO: @core.event('core.unmounting')

    @cli.command('start', 'Listen for and serve web requests')
    async def start(self):
        host = self.config.get('web.host', '0.0.0.0')
        port = self.config.get('web.port', 8080)
        self.runner = aiohttp.web.AppRunner(self.app)
        await self.runner.setup()
        self.site = aiohttp.web.TCPSite(self.runner, host, port)
        await self.site.start()