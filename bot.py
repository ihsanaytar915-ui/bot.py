import os
import json
import socket
import datetime
import discord
from discord import app_commands
from discord.ext import tasks

TOKEN = os.getenv("DISCORD_TOKEN")
RESET_UTC_OFFSET = int(os.getenv("RESET_UTC_OFFSET", "2"))
RESET_HOUR = int(os.getenv("RESET_HOUR", "2"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "gw_data.json")

COLOR_PANEL = 0x5865F2
ORDER = ["3", "2", "1", "0"]
LABELS = {
    "3": "3/3 Gangwar",
    "2": "2/3 Gangwar",
    "1": "1/3 Gangwar",
    "0": "0/3 Gangwar",
}
DIVIDER = "━━━━━━━━━━━━━━━━━━━━━━━━━━━"


def _now_local() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=RESET_UTC_OFFSET
    )


def day_key() -> str:
    d = _now_local() - datetime.timedelta(hours=RESET_HOUR)
    return f"{d.year}-{d.month}-{d.day}"


def reset_unix() -> int:
    now = _now_local()
    nxt = now.replace(hour=RESET_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += datetime.timedelta(days=1)
    utc_dt = nxt - datetime.timedelta(hours=RESET_UTC_OFFSET)
    return int(utc_dt.timestamp())


def _fresh() -> dict:
    return {
        "lists": {"3": [], "2": [], "1": [], "0": []},
        "dayKey": day_key(),
        "panel": None,
    }


def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _fresh()

    if "lists" not in d:
        d = {
            "lists": {
                "3": d.get("completed", []),
                "2": [],
                "1": [],
                "0": d.get("notCompleted", []),
            },
            "dayKey": d.get("dayKey", day_key()),
            "panel": d.get("panel"),
        }
    for lvl in ORDER:
        d["lists"].setdefault(lvl, [])
    d.setdefault("dayKey", day_key())
    d.setdefault("panel", None)
    return d


def save_data(d: dict) -> None:
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, DATA_FILE)


def check_reset(d: dict) -> bool:
    today = day_key()
    if d.get("dayKey") != today:
        d["lists"] = {"3": [], "2": [], "1": [], "0": []}
        d["dayKey"] = today
        save_data(d)
        return True
    return False


def format_list(arr: list) -> str:
    if not arr:
        return "*— niemand —*"
    out, shown = "", 0
    for i, u in enumerate(arr):
        line = f"**{i + 1:02d}**  <@{u['id']}>\n"
        if len(out) + len(line) > 950:
            break
        out += line
        shown += 1
    if shown < len(arr):
        out += f"*… und {len(arr) - shown} weitere*"
    return out.strip()


def build_panel(d: dict) -> discord.Embed:
    lists = d["lists"]
    total = sum(len(v) for v in lists.values())

    embed = discord.Embed(color=COLOR_PANEL)
    embed.set_author(name="GW HOST")
    embed.title = "⚔️  Gangwars  —  Tagestracker"
    embed.description = f"⏰  **Nächster Reset:** <t:{reset_unix()}:R>"

    for idx, lvl in enumerate(ORDER):
        arr = lists[lvl]
        embed.add_field(
            name=f"{LABELS[lvl]}  ·  {len(arr)}",
            value=format_list(arr),
            inline=False,
        )
        if idx < len(ORDER) - 1:
            embed.add_field(name="\u200b", value=DIVIDER, inline=False)

    embed.set_footer(
        text=f"{total} Spieler heute  ·  Reset täglich um {RESET_HOUR:02d}:00 Uhr"
    )
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    return embed


async def handle_join(interaction: discord.Interaction, level: str) -> None:
    d = load_data()
    uid = str(interaction.user.id)
    for lst in d["lists"].values():
        lst[:] = [u for u in lst if u["id"] != uid]
    if level != "leave":
        d["lists"][level].append({"id": uid, "tag": interaction.user.name})
    save_data(d)
    await interaction.response.edit_message(embed=build_panel(d), view=GWView())


class GWView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="3/3", style=discord.ButtonStyle.success, emoji="✅", custom_id="gw_3"
    )
    async def b3(self, interaction: discord.Interaction, _btn):
        await handle_join(interaction, "3")

    @discord.ui.button(
        label="2/3", style=discord.ButtonStyle.secondary, emoji="🟡", custom_id="gw_2"
    )
    async def b2(self, interaction: discord.Interaction, _btn):
        await handle_join(interaction, "2")

    @discord.ui.button(
        label="1/3", style=discord.ButtonStyle.primary, emoji="🟠", custom_id="gw_1"
    )
    async def b1(self, interaction: discord.Interaction, _btn):
        await handle_join(interaction, "1")

    @discord.ui.button(
        label="0/3", style=discord.ButtonStyle.danger, emoji="❌", custom_id="gw_0"
    )
    async def b0(self, interaction: discord.Interaction, _btn):
        await handle_join(interaction, "0")

    @discord.ui.button(
        label="Verlassen", style=discord.ButtonStyle.secondary, emoji="🚪", custom_id="gw_leave"
    )
    async def leave(self, interaction: discord.Interaction, _btn):
        await handle_join(interaction, "leave")


class GWBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(GWView())
        daily_task.start()


bot = GWBot()


async def sync_guild(guild: discord.Guild) -> None:
    try:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
        print(f"Befehle synchronisiert fuer: {guild.name}")
    except discord.HTTPException as e:
        print(f"Sync-Fehler fuer {guild.name}: {e}")


async def _get_channel(channel_id: int):
    return bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)


async def delete_old_panel(d: dict) -> None:
    panel = d.get("panel")
    if not panel:
        return
    try:
        channel = await _get_channel(panel["channelId"])
        await channel.get_partial_message(panel["messageId"]).delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass
    d["panel"] = None


async def post_panel(channel, d: dict) -> None:
    await delete_old_panel(d)
    message = await channel.send(embed=build_panel(d), view=GWView())
    d["panel"] = {"channelId": message.channel.id, "messageId": message.id}
    save_data(d)


async def refresh_panel(d: dict) -> None:
    panel = d.get("panel")
    if not panel:
        return
    try:
        channel = await _get_channel(panel["channelId"])
        await channel.get_partial_message(panel["messageId"]).edit(
            embed=build_panel(d), view=GWView()
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        print(f"Panel konnte nicht aktualisiert werden: {e}")


@tasks.loop(minutes=1)
async def daily_task():
    d = load_data()
    if check_reset(d):
        await refresh_panel(d)
        print("Taeglicher Reset - Listen geleert.")


@daily_task.before_loop
async def _before():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    for guild in bot.guilds:
        await sync_guild(guild)
    try:
        await bot.http.bulk_upsert_global_commands(bot.application_id, [])
        print("Alte globale Befehle entfernt.")
    except Exception as e:
        print(f"Globale Befehle konnten nicht geraeumt werden: {e}")
    print(f"Eingeloggt als {bot.user} ({bot.user.id})")


@bot.event
async def on_guild_join(guild: discord.Guild):
    await sync_guild(guild)


@bot.tree.command(name="gwpanel", description="GW HOST Tracker-Panel hier posten")
@app_commands.default_permissions(manage_guild=True)
async def gwpanel(interaction: discord.Interaction):
    d = load_data()
    await post_panel(interaction.channel, d)
    await interaction.response.send_message(
        "Tracker-Panel wurde neu gepostet.", ephemeral=True
    )


@bot.tree.command(name="gwadd", description="Spieler manuell zu einer Liste hinzufuegen")
@app_commands.describe(user="Der Spieler", liste="In welche Liste")
@app_commands.choices(
    liste=[
        app_commands.Choice(name="3/3", value="3"),
        app_commands.Choice(name="2/3", value="2"),
        app_commands.Choice(name="1/3", value="1"),
        app_commands.Choice(name="0/3", value="0"),
    ]
)
@app_commands.default_permissions(manage_guild=True)
async def gwadd(
    interaction: discord.Interaction,
    user: discord.Member,
    liste: app_commands.Choice[str],
):
    d = load_data()
    uid = str(user.id)
    for lst in d["lists"].values():
        lst[:] = [u for u in lst if u["id"] != uid]
    d["lists"][liste.value].append({"id": uid, "tag": user.name})
    save_data(d)
    await refresh_panel(d)
    await interaction.response.send_message(
        f"{user.mention} wurde zu {liste.name} Gangwar hinzugefuegt.", ephemeral=True
    )


@bot.tree.command(name="gwreset", description="Alle Gangwars-Listen manuell leeren")
@app_commands.default_permissions(manage_guild=True)
async def gwreset(interaction: discord.Interaction):
    d = load_data()
    d["lists"] = {"3": [], "2": [], "1": [], "0": []}
    d["dayKey"] = day_key()
    save_data(d)
    await refresh_panel(d)
    await interaction.response.send_message(
        "Alle Listen wurden zurueckgesetzt.", ephemeral=True
    )


if __name__ == "__main__":
    if TOKEN == "PASTE-YOUR-BOT-TOKEN-HERE":
        raise SystemExit("Kein Token gesetzt. TOKEN oben in der Datei eintragen.")

    _lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _lock.bind(("127.0.0.1", 47921))
    except OSError:
        raise SystemExit(
            "GW HOST laeuft bereits in einem anderen Fenster. "
            "Bitte zuerst das andere Fenster schliessen, dann erneut starten."
        )

    bot.run(TOKEN)
