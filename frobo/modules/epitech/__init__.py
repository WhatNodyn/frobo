import os, os.path
import aiohttp
import frobo
import random
import sqlalchemy, sqlalchemy.orm
import urllib.parse
from aiohttp.web import Response
from discord_slash.model import ButtonStyle
from discord_slash.utils.manage_commands import create_option, get_all_commands
from discord_slash.utils.manage_components import create_actionrow, create_button
from jwt import decode, PyJWKClient

class Registrations(frobo.Cog):
    dependencies = ['config', 'discord', 'sql', 'web']

    config: config.manager
    roles: discord.roles
    discord: discord.client

    def render(self, status, message, subtitle):
        template_path = os.path.join(os.path.dirname(__file__), 'authorized_template.html')
        is_error = status < 200 or status >= 400
        with open(template_path) as f:
            html = f.read() \
                .replace('%TITLE%', message) \
                .replace('%SUBTITLE%', subtitle) \
                .replace('%CLASS%', 'error' if is_error else 'ok') \
                .replace('%ICON%', 'link_off' if is_error else 'link')
            return Response(
                status=status,
                text=html,
                content_type='text/html',
            )

    @sql.model('epitech_users')
    class EpitechUser:
        id:      sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        discord: sqlalchemy.Column(sqlalchemy.String)
        azure:   sqlalchemy.Column(sqlalchemy.String, unique=True)

    @sql.model('epitech_interactions')
    class Interaction:
        id:        sqlalchemy.Column(sqlalchemy.Integer, primary_key=True)
        snowflake: sqlalchemy.Column(sqlalchemy.String)
        guild:     sqlalchemy.Column(sqlalchemy.String)
        user:      sqlalchemy.Column(sqlalchemy.String, unique=True)
        nonce:     sqlalchemy.Column(sqlalchemy.Integer)

    @web.route('GET', '/epitech/verify/{interaction}')
    async def verify(self, request, sql: sql.session):
        query = sqlalchemy.select(self.Interaction).where(self.Interaction.snowflake == request.match_info['interaction'])
        interaction = sql.execute(query).first()
        if interaction is None:
            return self.render(404, 'Invalid interaction', 'Please try again, starting with the /epitech login command')
        interaction = interaction[0]
        interaction.nonce = random.randint(-2**31, 2**31)
        sql.add(interaction)
        sql.commit()
        sql.flush()

        base_uri = self.config.get('web.root-uri')
        tenant = self.config.get('epitech.azure.tenant')
        auth_uri = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?'

        return Response(status=302, headers={
            'Location': auth_uri + urllib.parse.urlencode({
                'client_id':     self.config.get('epitech.azure.client-id'),
                'response_type': 'id_token',
                'redirect_uri':  f'{base_uri}/epitech/authorize',
                'response_mode': 'form_post',
                'scope':         'openid email',
                'nonce':         str(interaction.nonce),
                'state':         interaction.snowflake,
            }),
        })

    @web.route('POST', '/epitech/authorize')
    async def authorize(self, request, sql: sql.session):
        data = await request.post()
        if 'id_token' in data:
            jwks = PyJWKClient(f'https://login.microsoftonline.com/common/discovery/keys')
            key = jwks.get_signing_key_from_jwt(data['id_token'])
            claims = decode(data['id_token'], key.key, algorithms=['RS256'], audience=self.config.get('epitech.azure.client-id'))
            interaction = sql.execute(sqlalchemy.select(self.Interaction).where(self.Interaction.snowflake == data['state'])).first()
            if interaction is None: 
                return self.render(404, 'Invalid interaction', 'Please try again, starting with the /epitech login command')
            if interaction[0].nonce != int(claims['nonce']):
                return self.render(404, 'Invalid nonce', 'This request is not safe, please try again')
            interaction = interaction[0]
            user_id = interaction.user

            query = sqlalchemy.select(self.EpitechUser).where(
                self.EpitechUser.discord == user_id,
                self.EpitechUser.azure == claims['email']
            )
            user = sql.execute(query).first()
            if user is not None:
                return self.render(403, 'Already linked', "There was nothing to do, so we've done nothing")
            sql.add(self.EpitechUser(
                discord=user_id,
                azure=claims['email']
            ))
            sql.commit()
            sql.flush()
            guild = self.discord.client.get_guild(int(interaction.guild))
            if guild is None:
                return self.render(400, 'Guild not found', "Either the bot left the guild or you're up to shenanigans")
            member = guild.get_member(int(user_id))
            if member is None:
                return self.render(400, 'Member not found', "Were you just banned?")
            await self.roles.update_user(None, sql, member, guild=guild)
            
        if 'state' in data:
            query = sqlalchemy.delete(self.Interaction).where(self.Interaction.snowflake == data['state'])
            sql.execute(query)
            sql.commit()
            sql.flush()
        if 'id_token' not in data:
            return self.render(401, 'Authorization failed', 'Please try again, starting with the /epitech login command')
        return self.render(200, 'Accounts linked!', 'You can now close this tab')

    @discord.roles.profile('epitech')
    async def get_profile(self, member, sql: sql.session):
        query = sqlalchemy.select(self.EpitechUser).where(self.EpitechUser.discord == str(member.id))
        profiles = []
        for user in sql.execute(query):
            if user is None:
                return None
            user = user[0]

            token = self.config.get('epitech.intra.token')
            data = None
            async with aiohttp.ClientSession() as session:
                async with session.get(f'https://intra.epitech.eu/auth-{token}/user/{user.azure}?format=json') as resp:
                    data = await resp.json()
                    if 'error' not in data:
                        profiles.append(data)
        
        if len(profiles) == 0:
            return None
        return profiles

    @discord.command(
        'epitech', 'login',
        description='Authenticate with your Epitech account to access extra roles',
        base_default_permission=True,
        options=[],
    )
    async def login(self, ctx, sql: sql.session):
        await ctx.defer(hidden=True)
        sql.execute(sqlalchemy.delete(self.Interaction).where(self.Interaction.user == str(ctx.author_id)))
        sql.add(self.Interaction(
            snowflake=ctx.interaction_id,
            guild=ctx.guild_id,
            user=ctx.author_id,
        ))

        sql.commit()
        sql.flush()
        base_uri = self.config.get('web.root-uri')
        await ctx.send(
            "Alright, please sign in to Azure with your Epitech account by clicking on the button:",
            hidden=True,
            components=[create_actionrow(
                create_button(ButtonStyle.URL, "Sign in with Microsoft", url=f'{base_uri}/epitech/verify/{ctx.interaction_id}'),
            )],
        )

    @discord.command(
        'epitech', 'whois',
        description="Find someone's Epitech login from their user account",
        base_default_permission=True,
        options=[
            create_option('user', description='The user you want to know the login of', option_type=6, required=True),
        ],
    )
    async def whois(self, ctx):
        # TODO: Implement
        ...

    