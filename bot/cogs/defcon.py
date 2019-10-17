import logging
from datetime import datetime, timedelta
from typing import Awaitable, Dict, Tuple

from discord import Colour, Embed, Member
from discord.ext.commands import Bot, Cog, Context, group

from bot.cogs.moderation import ModLog
from bot.constants import Channels, Colours, Emojis, Event, Icons, Roles
from bot.decorators import with_role

log = logging.getLogger(__name__)

REJECTION_MESSAGE = """
Hi, {user} - Thanks for your interest in our server!

Due to a current (or detected) cyberattack on our community, we've limited access to the server for new accounts. Since
your account is relatively new, we're unable to provide access to the server at this time.

Even so, thanks for joining! We're very excited at the possibility of having you here, and we hope that this situation
will be resolved soon. In the meantime, please feel free to peruse the resources on our site at
<https://pythondiscord.com/>, and have a nice day!
"""

BASE_CHANNEL_TOPIC = "Python Discord Defense Mechanism"


class Defcon(Cog):
    """Time-sensitive server defense mechanisms."""

    days = None  # type: timedelta
    enabled = False  # type: bool

    _defcon_log_info: Dict[str, Tuple] = {
        'enabled': (Icons.defcon_enabled, Colours.soft_green, "**Days:** {days}\n\n"),
        'disabled': (Icons.defcon_disabled, Colours.soft_red, ""),
        'updated': (Icons.defcon_updated, Colour.blurple(), "**Days:** {days}\n\n"),
    }

    def __init__(self, bot: Bot):
        self.bot = bot
        self.channel = None
        self.days = timedelta(days=0)

        self.bot.loop.create_task(self.sync_settings())

    @property
    def mod_log(self) -> ModLog:
        """Get currently loaded ModLog cog instance."""
        return self.bot.get_cog("ModLog")

    async def sync_settings(self) -> None:
        """On cog load, try to synchronize DEFCON settings to the API."""
        await self.bot.wait_until_ready()
        self.channel = await self.bot.fetch_channel(Channels.defcon)

        try:
            response = await self.bot.api_client.get('bot/bot-settings/defcon')
            data = response['data']

        except Exception:  # Yikes!
            log.exception("Unable to get DEFCON settings!")
            await self.bot.get_channel(Channels.devlog).send(
                f"<@&{Roles.admin}> **WARNING**: Unable to get DEFCON settings!"
            )

        else:
            if data["enabled"]:
                self.enabled = True
                self.days = timedelta(days=data["days"])
                log.warning(f"DEFCON enabled: {self.days.days} days")

            else:
                self.enabled = False
                self.days = timedelta(days=0)
                log.warning(f"DEFCON disabled")

            await self.update_channel_topic()

    @Cog.listener()
    async def on_member_join(self, member: Member) -> None:
        """If DEFCON is enabled, check newly joining users to see if they meet the account age threshold."""
        if self.enabled and self.days.days > 0:
            now = datetime.utcnow()

            if now - member.created_at < self.days:
                log.info(f"Rejecting user {member}: Account is too new and DEFCON is enabled")

                message_sent = False

                try:
                    await member.send(REJECTION_MESSAGE.format(user=member.mention))

                    message_sent = True
                except Exception:
                    log.exception(f"Unable to send rejection message to user: {member}")

                await member.kick(reason="DEFCON active, user is too new")

                message = (
                    f"{member} (`{member.id}`) was denied entry because their account is too new."
                )

                if not message_sent:
                    message = f"{message}\n\nUnable to send rejection message via DM; they probably have DMs disabled."

                await self.mod_log.send_log_message(
                    Icons.defcon_denied, Colours.soft_red, "Entry denied",
                    message, member.avatar_url_as(static_format="png")
                )

    @group(name='defcon', aliases=('dc',), invoke_without_command=True)
    @with_role(Roles.admin, Roles.owner)
    async def defcon_group(self, ctx: Context) -> None:
        """Check the DEFCON status or run a subcommand."""
        await ctx.invoke(self.bot.get_command("help"), "defcon")

    async def _defcon_action(self, ctx: Context, action: Awaitable[None], action_type: str) -> None:
        """Providing a structured way to do an defcon actions - enabled / disabled / updated."""
        error = None
        try:
            await action
        except Exception as err:
            log.exception("Unable to update DEFCON settings.")
            error = err
        finally:
            await ctx.send(self.build_defcon_msg(action_type, error))
            await self.send_defcon_log(action_type, ctx.author, error)

    @defcon_group.command(name='enable', aliases=('on', 'e'))
    @with_role(Roles.admin, Roles.owner)
    async def enable_command(self, ctx: Context) -> None:
        """
        Enable DEFCON mode. Useful in a pinch, but be sure you know what you're doing!

        Currently, this just adds an account age requirement. Use !defcon days <int> to set how old an account must be,
        in days.
        """
        self.enabled = True

        await self._defcon_action(
            ctx,
            action=self.bot.api_client.put(
                'bot/bot-settings/defcon',
                json={
                    'name': 'defcon',
                    'data': {
                        'enabled': True,
                        # TODO: retrieve old days count
                        'days': 0
                    }
                }
            ),
            action_type='enabled'
        )

        await self.update_channel_topic()

    @defcon_group.command(name='disable', aliases=('off', 'd'))
    @with_role(Roles.admin, Roles.owner)
    async def disable_command(self, ctx: Context) -> None:
        """Disable DEFCON mode. Useful in a pinch, but be sure you know what you're doing!"""
        self.enabled = False

        await self._defcon_action(
            ctx,
            action=self.bot.api_client.put(
                'bot/bot-settings/defcon',
                json={
                    'data': {
                        'days': 0,
                        'enabled': False
                    },
                    'name': 'defcon'
                }
            ),
            action_type='disabled')

        await self.update_channel_topic()

    @defcon_group.command(name='status', aliases=('s',))
    @with_role(Roles.admin, Roles.owner)
    async def status_command(self, ctx: Context) -> None:
        """Check the current status of DEFCON mode."""
        embed = Embed(
            colour=Colour.blurple(), title="DEFCON Status",
            description=f"**Enabled:** {self.enabled}\n"
                        f"**Days:** {self.days.days}"
        )

        await ctx.send(embed=embed)

    @defcon_group.command(name='days')
    @with_role(Roles.admin, Roles.owner)
    async def days_command(self, ctx: Context, days: int) -> None:
        """Set how old an account must be to join the server, in days, with DEFCON mode enabled."""
        self.days = timedelta(days=days)

        await self._defcon_action(
            ctx,
            action=self.bot.api_client.put(
                'bot/bot-settings/defcon',
                json={
                    'data': {
                        'days': days,
                        'enabled': True
                    },
                    'name': 'defcon'
                }
            ),
            action_type="updated"
        )

        # Enable DEFCON if it's not already
        # seems redundant to check if False, can directly set to True
        # if not self.enabled:
        self.enabled = True

        await self.update_channel_topic()

    async def update_channel_topic(self) -> None:
        """Update the #defcon channel topic with the current DEFCON status."""
        if self.enabled:
            day_str = "days" if self.days.days > 1 else "day"
            new_topic = f"{BASE_CHANNEL_TOPIC}\n(Status: Enabled, Threshold: {self.days.days} {day_str})"
        else:
            new_topic = f"{BASE_CHANNEL_TOPIC}\n(Status: Disabled)"

        self.mod_log.ignore(Event.guild_channel_update, Channels.defcon)
        await self.channel.edit(topic=new_topic)

    def build_defcon_msg(self, change: str, e: Exception = None) -> str:
        """
        Build in-channel response string for DEFCON action.

        `change` string may be one of the following: ('enabled', 'disabled', 'updated')
        """
        if change.lower() == "enabled":
            msg = f"{Emojis.defcon_enabled} DEFCON enabled.\n\n"
        elif change.lower() == "disabled":
            msg = f"{Emojis.defcon_disabled} DEFCON disabled.\n\n"
        elif change.lower() == "updated":
            msg = (
                f"{Emojis.defcon_updated} DEFCON days updated; accounts must be {self.days} "
                "days old to join the server.\n\n"
            )

        if e:
            msg += (
                "**There was a problem updating the site** - This setting may be reverted when the bot restarts.\n\n"
                f"```py\n{e}\n```"
            )

        return msg

    async def send_defcon_log(self, change: str, actor: Member, e: Exception = None) -> None:
        """
        Send log message for DEFCON action.

        `change` string may be one of the following: ('enabled', 'disabled', 'updated')
        """
        icon, color, info_template = self._defcon_log_info[change.lower()]
        log_msg: str = (
            f"**Staffer:** {actor.mention} {actor} (`{actor.id}`)\n"
            f"{info_template.format(days=self.days.days)}"
        )
        status_msg = f"DEFCON {change.lower()}"

        if e:
            log_msg += (
                "**There was a problem updating the site** - This setting may be reverted when the bot restarts.\n\n"
                f"```py\n{e}\n```"
            )

        await self.mod_log.send_log_message(icon, color, status_msg, log_msg)


def setup(bot: Bot) -> None:
    """DEFCON cog load."""
    bot.add_cog(Defcon(bot))
    log.info("Cog loaded: Defcon")
