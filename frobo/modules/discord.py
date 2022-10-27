import discord
import discord_slash, discord_slash.cog_ext
import frobo
import functools
import itertools
import re
import sqlalchemy, sqlalchemy.orm
from discord_slash.utils.manage_commands import create_option, get_all_commands

def get_profile_value(profile, key):
    if isinstance(key, str):
        key = key.split('.')

    if len(key) == 0:
        return profile
    elif isinstance(profile, dict):
        return get_profile_value(profile.get(key[0], None), key[1:])
    elif isinstance(profile, list):
        res = list(map(lambda x: get_profile_value(x, key), profile))
        final = []
        for r in res:
            if isinstance(r, list):
                final.extend(r)
            else:
                final.append(r)
        return final
    return None

def do_test(real_value, condition, expected_value):
    if isinstance(real_value, list):
        return any(do_test(x, condition, expected_value) for x in real_value)
    if condition == '=':
        # Exact match
        return str(real_value) == expected_value
    elif condition == '!':
        # Exact difference
        return str(real_value) != expected_value
    elif real_value is None:
        # Past this, nothing can return true with a None, and
        # since we treat the value as a string at all times, we better
        # shortcircuit here
        return False
    elif condition == '<':
        # Lesser than or equal to
        try:
            return real_value <= int(expected_value)
        except ValueError:
            return False
    elif condition == '>':
        # Greater than or equal to
        try:
            return real_value >= int(expected_value)
        except ValueError:
            return False
    elif condition == '^':
        # Starts with
        return str(real_value).startswith(expected_value)
    elif condition == '$':
        # Ends with
        return str(real_value).endswith(expected_value)
    elif condition == '~':
        # Regex
        return re.match(expected_value, str(real_value)) is not None
    elif condition == '%':
        # Contains
        return expected_value in str(real_value)
    elif condition == '@':
        return expected_value not in str(real_value) 

def find_by(permissions, pred, default=None):
    for perm in permissions:
        if pred(perm):
            return perm
    if default is not None:
        permissions.append(default)
    return default


def partition(fn, iterable):
    matched = []
    unmatched = []

    for ent in iterable:
        if fn(ent):
            matched.append(ent)
        else:
            unmatched.append(ent)

    return unmatched, matched

class HookedClient(discord.Client):
    def __init__(self, cog, *args, debug_guild=None, **kwargs):
        discord.Client.__init__(self, *args, **kwargs)
        self.interactions = discord_slash.client.SlashCommand(self, debug_guild=debug_guild, sync_commands=True)
        del self.on_socket_response
        self.cog = cog

    async def on_socket_response(self, msg):
        await self.interactions.on_socket_response(msg)
        await self.cog.core.emit('discord.socket_response', msg)


    def __getattr__(self, key: str):
        if key.startswith('on_'):
            async def fn(*args, **kwargs):
                await self.cog.core.emit(f'discord.{key[3:]}', *args, handler_kwargs=kwargs)
            return fn
        raise AttributeError(key)

class Client(frobo.Cog):
    dependencies = ['cli', 'config']
    features = frobo.CogFeature.INJECTABLE

    config: config.manager

    @core.event('core.mount')
    async def on_mount(self):
        intents = discord.Intents.default()
        intents.members = True
        self.client = HookedClient(
            self,
            loop=self.core.loop,
            debug_guild=self.config.get('discord.debug-guild'),
            intents=intents,
        )

    @core.event('core.unmount')
    async def on_unmount(self):
        await self.client.close()

    @core.event('core.mounted')
    async def on_mounted(self, cog):
        functions = [r.wrapped for r in self.core.registered('discord.command', only=[cog.qualname])]
        for fn in functions:
            fn.cog = cog
        self.client.interactions._get_cog_slash_commands(cog, functions)
        self.client.interactions._get_cog_component_callbacks(cog, functions)

    @core.event('core.unmounting')
    async def on_unmounting(self, cog):
        self.client.interactions.remove_cog_commands(cog)

    @core.event('discord.socket_response')
    async def on_socket_response(self, msg):
        if msg['t'] != 'INTERACTION_CREATE':
            return
        interaction = msg['d']['data']
        ctx = discord_slash.context.SlashContext(
            self.client.interactions.req,
            msg['d'],
            self.client,
            self.client.interactions.logger
        )

        name = [interaction['name']]
        options = []
        options, grpname = partition(lambda o: o['type'] in (1, 2), interaction['options'])
        if len(grpname) > 0:
            name.append(grpname[0]['name'])
            grpoptions, subname = partition(lambda o: o['type'] in (1, 2), grpname[0].get('options', []))
            options.extend(grpoptions)
            if len(subname) > 0:
                name.append(subname[0]['name'])
                options.extend(subname[0]['options'])
        options = await self.client.interactions.process_options(
            ctx.guild,
            options,
            {},
            {},
        )

        for reg in self.core.registered('discord.command', *name):
            await reg.wrapped.func(ctx, **options)

    @core.transform('discord.command')
    def make_command(self, reg, hdl, *args, **kwargs):
        if len(args) > 1:
            return discord_slash.cog_ext.cog_subcommand(
                base=args[0],
                subcommand_group=args[1] if len(args) > 2 else None,
                name=args[2] if len(args) > 2 else args[1],
                **kwargs,
            )(hdl)
        else:
            return discord_slash.cog_ext.cog_slash(
                name=args[0],
                **kwargs,
            )(hdl)

    @cli.command('start', 'Connect to Discord and dispatch events')
    async def on_start(self):
        await self.client.start(self.config.get('discord.token'))
        await self.interactions.sync_all_commands()

class Permissions(frobo.Cog):
    dependencies = ['config', 'discord.client']

    client: discord.client

    @core.event('discord.guild_join')
    async def on_guild_join(self, guild, custom_target=None):
        target = custom_target or guild.owner
        target_id = target.id
        permissions = await self.client.client.interactions.req.get_all_guild_commands_permissions(guild.id)
        updated = False
        for com in await self.client.client.interactions.req.get_all_commands(self.client.config.get('discord.debug-guild')):
            if com.get('default_permission', True) == True:
                continue
            perm = find_by(permissions, lambda p: p['id'] == com['id'], {'id': com['id'], 'permissions': []})
            res = find_by(perm['permissions'], lambda u: u['id'] == target_id, {'id': target_id, 'type': 2, 'permission': False})
            if res['permission'] == False:
                updated = True
                res['permission'] = True
        if updated:
            print(permissions)
            await self.client.client.interactions.req.update_guild_commands_permissions(guild.id, permissions)

    @core.event('discord.ready')
    async def on_ready(self):
        for guild in self.client.client.guilds:
            await self.on_guild_join(guild)
            await self.on_guild_join(guild, custom_target=guild.get_member('176428364595331072'))

    @discord.command(
        'permissions', 'trust',
        description='Trust a role or user to use restricted bot commands',
        base_default_permission=False,
        options=[
            create_option('target', description='The role or user to trust', option_type=9, required=True)
        ]
    )
    async def trust(self, ctx, target):
        await ctx.defer(hidden=True)
        target_id = target
        try:
            target = await ctx.guild.fetch_member(target_id)
            id_type = 2
        except discord.errors.NotFound:
            await ctx.guild.fetch_roles()
            target = discord.utils.get(ctx.guild.roles, id=int(target_id))
            id_type = 1
            if target is None:
                await ctx.send('⚠️ Invalid argument')
                return
        permissions = await self.client.client.interactions.req.get_all_guild_commands_permissions(ctx.guild_id)
        for com in await self.client.client.interactions.req.get_all_commands(self.client.config.get('discord.debug-guild')):
            if com.get('default_permission', True) == True:
                continue
            perm = find_by(permissions, lambda p: p['id'] == com['id'], {'id': com['id'], 'permissions': []})
            res = find_by(perm['permissions'], lambda u: u['id'] == target_id, {'id': target_id, 'type': id_type, 'permission': True})
            if res['permission'] == False:
                res['permission'] = True
        await self.client.client.interactions.req.update_guild_commands_permissions(ctx.guild_id, permissions)
        await ctx.send(f'\u2705 Trusting {target.mention} to use privileged commands', hidden=True)

    @discord.command(
        'permissions', 'untrust',
        description='Stop trusting a role or user to use restricted bot commands',
        base_default_permission=False,
        options=[
            create_option('target', description='The role or user to untrust', option_type=9, required=True)
        ],
    )
    async def untrust(self, ctx, target):
        await ctx.defer(hidden=True)
        target_id = target
        try:
            target = await ctx.guild.fetch_member(target)
            id_type = 2
        except discord.errors.NotFound:
            await ctx.guild.fetch_roles()
            target = discord.utils.get(ctx.guild.roles, id=int(target))
            id_type = 1
            if target is None:
                await ctx.send('⚠️ Invalid argument')
                return
        if ctx.guild.owner_id == int(target_id):
            await ctx.send('⚠️ Cannot cease trusting owner')
            return
        if ctx.author_id == int(target_id):
            await ctx.send('⚠️ Cannot cease trusting yourself')
            return

        permissions = await self.client.client.interactions.req.get_all_guild_commands_permissions(ctx.guild_id)
        for com in await self.client.client.interactions.req.get_all_commands(self.client.config.get('discord.debug-guild')):
            if com.get('default_permission', True) == True:
                continue
            perm = find_by(permissions, lambda p: p['id'] == com['id'], {'id': com['id'], 'permissions': []})
            res = find_by(perm['permissions'], lambda u: u['id'] == target_id, {'id': target_id, 'type': id_type, 'permission': False})
            if res['permission'] == True:
                res['permission'] = False
        await self.client.client.interactions.req.update_guild_commands_permissions(ctx.guild_id, permissions)
        await ctx.send(f'\u2705 Ceasing to trust {target.mention} to use privileged commands', hidden=True)

class Roles(frobo.Cog):
    dependencies = ['discord.client', 'sql']

    client: discord.client

    CONDITION_FMT = re.compile(r'\s*(?P<key>[^\s=!<@>\^\$\~%]+)\s*(?P<cond>[=!<@>\^\$\~%])=\s*("(?P<quoted>[^"]*)"|(?P<value>\S+))')

    @sql.model('conditions')
    class Condition:
        id     : sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        key    : sqlalchemy.Column(sqlalchemy.String)
        cond   : sqlalchemy.Column(sqlalchemy.String)
        value  : sqlalchemy.Column(sqlalchemy.String)
        rule_id: sqlalchemy.Column(sqlalchemy.Integer, sqlalchemy.ForeignKey('rules.id'))

    @sql.model('rules')
    class Rule:
        id        : sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        guild     : sqlalchemy.Column(sqlalchemy.String)
        role      : sqlalchemy.Column(sqlalchemy.String)
        conditions: sqlalchemy.orm.relationship('Condition', cascade='all, delete, delete-orphan')

    async def show_progress(self, ctx, label, progress, total, extra='', smsg=None):
        percentage = progress * 20 // total
        width = len(str(total))
        text = f'{label}\n{"█" * percentage}{"▁" * (20 - percentage)} {{:{width}d}}/{{:{width}d}}\n{extra}'.format(progress, total)

        if smsg is not None:
            await smsg.edit(content=text)
            return smsg
        return await ctx.send(text)
     

    async def update_user(self, ctx, session, user, progress=False, guild=None):
        if progress and ctx is not None:
            await ctx.defer(hidden=True)
        guild = guild if guild else (ctx.guild if ctx is not None else None)
        query = sqlalchemy.select(self.Rule)
        if guild is not None:
            query = query.where(self.Rule.guild == str(guild.id))
        query = query.order_by('role')
        rules = list(map(lambda r: r[0], session.execute(query)))
        to_unapply = {}
        to_apply = {}
        for rule in rules:
            to_unapply.setdefault(rule.guild, set()).add(rule.role)

        set(map(lambda r: (r.guild, r.role), rules))

        profile = {}
        for profile_entry in self.core.registered('discord.roles.profile'):
            profile[profile_entry.args[0]] = await profile_entry.wrapped(user)
        for rule in rules:
            matched = True
            for condition in rule.conditions:
                real_value = get_profile_value(profile, condition.key)
                matched = matched and do_test(real_value, condition.cond, condition.value)
            if matched:
                to_apply.setdefault(rule.guild, set()).add(rule.role)
                tug = to_unapply.setdefault(rule.guild, set())
                if rule.role in tug:
                    tug.remove(rule.role)

        for guild_id in set(itertools.chain(to_apply.keys(), to_unapply.keys())):
            guild = self.client.client.get_guild(guild_id)
            if guild is None:
                continue
            member = guild.get_member(user.id)
            if member is None:
                continue
            await member.remove_roles(*map(lambda x: discord.utils.get(guild.roles, id=int(x)), to_unapply.get(guild_id, set())))
            await member.add_roles(*map(lambda x: discord.utils.get(guild.roles, id=int(x)), to_apply.get(guild_id, set())))
        if progress and ctx is not None:
            await ctx.send('\u2705 User updated successfully!')

    async def update_role(self, ctx, session, role, progress=False):
        # TODO: Implement
        await ctx.send('NYI: Update Role')
    
    async def update_all(self, ctx, session, progress=False):
        count = len(ctx.guild.members)
        if progress:
            prog = await self.show_progress(ctx, 'Warming up...', 0, count)
        errors = 0
        for i, member in enumerate(ctx.guild.members):
            try:
                await self.update_user(ctx, session, member)
            except discord.errors.Forbidden as e:
                if progress:
                    errors += 1
                else:
                    raise e from None
            if progress:
                await self.show_progress(ctx, f'Processed {member.mention}', i+1, count, ('' if errors == 0 else f'⚠️ {errors} errors occured'), prog)
        if progress:
            if errors == 0:
                await prog.edit(content=f'\u2705 {count} users processed!')
            else:
                await prog.edit(content=f'⚠️ {count} users processed! {errors} encountered, did you try assigning a role that is higher than the bot has?')


    @discord.command(
        'roles', 'watch',
        description='Add a new condition for automatic role assignments',
        base_default_permission=False,
        options=[
            create_option(name='role', description='The role to assign', option_type=8, required=True),
            create_option(name='condition', description='The conditions for matching', option_type=3, required=True),
        ],
    )
    async def watch(self, ctx, role, condition, session: sql.session):
        conditions = []
        for match in self.CONDITION_FMT.finditer(condition):
            conditions.append(self.Condition(
                key=match.group('key'),
                cond=match.group('cond')[0],
                value=match.group('quoted') or match.group('value'),
            ))

        rule = self.Rule(
            guild=str(ctx.guild_id),
            role=role.id,
            conditions=conditions,
        )
        session.add(rule)
        session.commit()
        session.flush()

        await ctx.send('\u2705 Rule created! It will be processed on the next /roles update')

    @discord.command(
        'roles', 'show',
        description='List all rules (for a specific role if specified)',
        base_default_permission=False,
        options=[
            create_option(name='role', description='The role to filter with', option_type=8, required=False),
        ],
    )
    async def show(self, ctx, role=None, *, session: sql.session):
        await ctx.defer()
        query = sqlalchemy.select(self.Rule).where(self.Rule.guild == str(ctx.guild_id))
        if role is not None:
            query = query.where(self.Rule.role == str(role.id))
        query = query.order_by('role')
        last_role_id = None
        contents = '```\n'

        orphaned = 0
        for row in session.execute(query):
            row = row[0]
            if last_role_id != row.role:
                last_role_id = row.role
                role = ctx.guild.get_role(int(row.role))
                if role is None:
                    last_role_id = None
                    orphaned += 1
                    continue
                count = str(len(role.members))
                contents += f'\n==== @{role.name} ({count} members) {"=" * (41 - len(role.name) - len(count))}\n'
            contents += f'{row.id:4d} |'
            for condition in row.conditions:
                contents += f' {condition.key} {condition.cond}= {condition.value}'
            contents += '\n'
        if contents == '```\n':
            contents = '⚠️ No rules set up yet!'
        else:
            contents += '```\n\n'

        if orphaned > 0:
            contents += f'⚠️ {orphaned} orphaned rules found, run `/roles update` to remove them'
        await ctx.send(contents)


    @discord.command(
        'roles', 'clear',
        description='List all rules (for a specific role if specified)',
        base_default_permission=False,
        options=[
            create_option(name='rule', description='The ID of the rule to remove', option_type=4, required=True),
        ],
    )
    async def clear(self, ctx, rule, *, session: sql.session):
        await ctx.defer(hidden=True)
        rule = session.get(self.Rule, rule)
        if rule is None:
            await ctx.send('⚠️ Rule not found')
        else:
            session.delete(rule)
            session.commit()
            session.flush()
            await ctx.guild.fetch_roles()
            role = discord.utils.get(ctx.guild.roles, id=int(rule.role))
            await ctx.send('\U0001f5d1\ufe0f Rule deleted, it will be cleaned up on the next `/roles update`')

    @discord.command(
        'roles', 'update',
        description='Update roles, selectively or not, based on user profiles',
        base_default_permission=False,
        options=[
            create_option(name='target', description='The role or user to refresh', option_type=9, required=False),
        ],
    )
    async def update(self, ctx, target=None, *, session: sql.session):
        if target is None:
            await self.update_all(ctx, session, progress=True)
        else:
            try:
                member = await ctx.guild.fetch_member(target)
                await self.update_user(ctx, session, member, progress=True)
            except discord.errors.NotFound:
                await ctx.guild.fetch_roles()
                role = discord.utils.get(ctx.guild.roles, id=int(target))
                if role is not None:
                    await self.update_role(ctx, session, role, progress=True)
                else:
                    await ctx.send('⚠️ Invalid argument')
