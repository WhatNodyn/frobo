import frobo
import functools
import toml
import os

class Manager(frobo.Cog):
    features = frobo.CogFeature.INJECTABLE

    def get(self, key: frobo.util.Key, default=None):
        if isinstance(key, str):
            key = key.split('.')
        # TODO: Improve with array handling and such
        loaded = self._loaded
        for entry in key:
            loaded = loaded.get(entry, None)
            if not isinstance(loaded, dict):
                break
        if loaded is None:
            return os.getenv('FROBO_' + '_'.join(map(str.upper, key)), default)
        return loaded

    @core.event('core.mount')
    async def on_mount(self):
        self._loaded = {}
        try:
            with open(self.get('config.path', 'frobo.toml')) as f:
                self._loaded = toml.load(f)
        except FileNotFoundError:
            pass

    @core.injectable('config.value')
    def value(self, key: str, default=None):
        return self.get(key, default)