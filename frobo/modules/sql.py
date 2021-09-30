import frobo
import sqlalchemy, sqlalchemy.orm

class Database(frobo.Cog):
    dependencies = ['config', 'cli']

    config: config.manager

    @core.event('core.mount')
    async def on_mount(self):
        uri = self.config.get('sql.uri', 'sqlite:///:memory:')
        echo = bool(self.config.get('sql.echo', False))
        self.engine = sqlalchemy.create_engine(uri, echo=echo)
        self.Base = sqlalchemy.orm.declarative_base()

    @core.event('core.mounted')
    async def on_mounted(self, cog):
        for reg in self.core.registered('sql.model', only=[cog.qualname]):
            pass

    @core.event('core.unmount')
    async def on_unmount(self):
        self.engine.dispose()
        del self.engine


    @core.transform('sql.model')
    def make_model(self, reg, fields, table_name=None):
        table_name = table_name or frobo.util.camel_to_snakecase(fields.__name__)
        return type(fields.__name__, (fields, self.Base,), {'__tablename__': table_name, **fields.__annotations__})

    @core.injectable('sql.session')
    def get_session(self):
        return sqlalchemy.orm.Session(self.engine)

    @cli.command('init', 'Create tables in database', daemon=False)
    async def on_init(self):
        models = list(map(lambda r: r.wrapped.__bases__, self.core.registered('sql.model')))
        self.Base.metadata.create_all(self.engine)
        print('Tables created')
        