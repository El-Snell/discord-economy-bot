import os
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands
from discord import app_commands

DB_PATH = "bot.db"
TOKEN = os.getenv("DISCORD_TOKEN", "YOUR_BOT_TOKEN_HERE")
DAILY_COOLDOWN_HOURS = 24
WORK_COOLDOWN_MINUTES = 30

PLUGIN_NAME = "MineCraftify"
FONT_PATH = "assets/fonts/minecraft.ttf"
NPC_SHOPKEEPERS = {
    "general": {"name": "Villager Greg", "title": "General Trader"},
    "rare": {"name": "Ender Elric", "title": "Rare Goods Dealer"},
}
SHOP_CATEGORIES = {
    "general": ["potion", "lucky_ticket"],
    "rare": ["gem", "crate"],
}
RAID_BOSSES = {
    "warden": {"hp": 1200, "reward": 2600, "xp": 260, "rarity": "legendary"},
    "dragon": {"hp": 1800, "reward": 4200, "xp": 420, "rarity": "legendary"},
}
STARTING_XP = 0
XP_PER_WORK = (8, 20)
XP_PER_DAILY = (12, 28)
XP_PER_GAMBLE_WIN = (6, 16)
XP_PER_BUY = (3, 8)
QUEST_REFRESH_HOURS = 24
RARITY_COLORS = {
    "common": 0xAAAAAA,
    "uncommon": 0x55FF55,
    "rare": 0x5555FF,
    "epic": 0xAA00AA,
    "legendary": 0xFFAA00,
}
BOSS_EVENTS = {
    "zombie_king": {"hp": 250, "reward": 500, "xp": 80, "rarity": "epic"},
    "wither": {"hp": 600, "reward": 1500, "xp": 180, "rarity": "legendary"},
}
QUEST_TEMPLATES = [
    {"key": "work", "name": "Village Labor", "target": 3, "reward": 180, "xp": 35},
    {"key": "buy", "name": "Market Run", "target": 2, "reward": 140, "xp": 25},
    {"key": "gamble", "name": "Risky Business", "target": 1, "reward": 220, "xp": 40},
]
ASSET_ICON_PATH = "assets/icons"  # optional per-achievement icons

# Minecraft-style color codes usable in messages (&a, &6, etc.)
MC_COLOR_MAP = {
    "&0": "[30m","&1": "[34m","&2": "[32m","&3": "[36m","&4": "[31m","&5": "[35m","&6": "[33m","&7": "[37m",
    "&8": "[90m","&9": "[94m","&a": "[92m","&b": "[96m","&c": "[91m","&d": "[95m","&e": "[93m","&f": "[97m"
}


def mc_format(text: str) -> str:
    for code in MC_COLOR_MAP:
        text = text.replace(code, "")
    return text


def level_requirement(level: int) -> int:
    return 100 + ((level - 1) * 50)


def grant_xp(guild_id: int, user_id: int, amount: int) -> tuple[int, int, bool]:
    stats = get_user_stats(guild_id, user_id)
    xp = int(stats["xp"]) + amount
    level = int(stats["level"])
    leveled_up = False
    while xp >= level_requirement(level):
        xp -= level_requirement(level)
        level += 1
        leveled_up = True
    update_user_fields(guild_id, user_id, xp=xp, level=level)
    return xp, level, leveled_up


def ensure_daily_quest(guild_id: int, user_id: int) -> sqlite3.Row:
    stats = get_user_stats(guild_id, user_id)
    last_refresh = parse_dt(stats["quest_last_refresh"])
    refresh_needed = last_refresh is None or (utc_now() - last_refresh) >= timedelta(hours=QUEST_REFRESH_HOURS) or not stats["quest_key"]
    if refresh_needed:
        template = random.choice(QUEST_TEMPLATES)
        update_user_fields(
            guild_id,
            user_id,
            quest_key=template["key"],
            quest_progress=0,
            quest_target=template["target"],
            quest_reward=template["reward"],
            quest_xp_reward=template["xp"],
            quest_last_refresh=utc_now().isoformat(),
        )
        stats = get_user_stats(guild_id, user_id)
    return stats


def advance_quest(guild_id: int, user_id: int, key: str, amount: int = 1) -> Optional[str]:
    stats = ensure_daily_quest(guild_id, user_id)
    if stats["quest_key"] != key:
        return None
    progress = min(int(stats["quest_progress"]) + amount, int(stats["quest_target"]))
    update_user_fields(guild_id, user_id, quest_progress=progress)
    if progress >= int(stats["quest_target"]):
        add_wallet(guild_id, user_id, int(stats["quest_reward"]))
        _, level, leveled = grant_xp(guild_id, user_id, int(stats["quest_xp_reward"]))
        msg = f"Quest complete: **{stats['quest_key']}**. Rewards: **{stats['quest_reward']}** coins and **{stats['quest_xp_reward']} XP**."
        template = random.choice(QUEST_TEMPLATES)
        update_user_fields(
            guild_id,
            user_id,
            quest_key=template["key"],
            quest_progress=0,
            quest_target=template["target"],
            quest_reward=template["reward"],
            quest_xp_reward=template["xp"],
            quest_last_refresh=utc_now().isoformat(),
        )
        if leveled:
            msg += f" Level up! You are now level **{level}**."
        return msg
    return None
PLUGIN_TAG = f"[{PLUGIN_NAME}]"
USE_TOAST_IMAGES = True
USE_EVENT_SOUNDS = True

# Configure achievement -> role rewards here.
ACHIEVEMENT_ROLE_REWARDS = {
    # "first_steps": 123456789012345678,
}

AUTO_ACHIEVEMENTS = {
    "first_coin": 1,
    "starter": 100,
    "wealthy": 1000,
    "tycoon": 10000,
}

PROGRESS_ACHIEVEMENTS = {
    "worker_i": {"metric": "work_count", "target": 10, "description": "Use /work 10 times"},
    "collector_i": {"metric": "items_owned", "target": 5, "description": "Own 5 items"},
    "spender_i": {"metric": "spent_total", "target": 1000, "description": "Spend 1,000 coins in the shop"},
}

SHOP_ITEMS = {
    "potion": {"price": 100, "description": "A basic potion", "sell_price": 50},
    "gem": {"price": 500, "description": "A shiny gem", "sell_price": 250},
    "lucky_ticket": {"price": 250, "description": "A lucky ticket for flexing", "sell_price": 100},
    "crate": {"price": 750, "description": "A mysterious crate", "sell_price": 350},
}

MC_SOUNDS = {

    "levelup": "sounds/levelup.ogg",
    "coin": "sounds/experience_orb.ogg",
    "villager": "sounds/villager_yes.ogg",
    "anvil": "sounds/anvil_use.ogg",
    "oof": "sounds/player_hurt.ogg",
    "note": "sounds/note_block.ogg",
}

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = False
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                wallet INTEGER NOT NULL DEFAULT 0,
                bank INTEGER NOT NULL DEFAULT 0,
                daily_last_claim TEXT,
                work_last_claim TEXT,
                work_count INTEGER NOT NULL DEFAULT 0,
                gamble_wins INTEGER NOT NULL DEFAULT 0,
                gamble_losses INTEGER NOT NULL DEFAULT 0,
                spent_total INTEGER NOT NULL DEFAULT 0,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 1,
                quest_key TEXT,
                quest_progress INTEGER NOT NULL DEFAULT 0,
                quest_target INTEGER NOT NULL DEFAULT 0,
                quest_reward INTEGER NOT NULL DEFAULT 0,
                quest_xp_reward INTEGER NOT NULL DEFAULT 0,
                quest_last_refresh TEXT,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS achievements (
                guild_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                reward_amount INTEGER NOT NULL DEFAULT 0,
                role_id INTEGER,
                PRIMARY KEY (guild_id, name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventories (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                item_name TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, item_name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_achievement_progress (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                achievement_name TEXT NOT NULL,
                progress_value INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id, achievement_name)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_achievements (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                achievement_name TEXT NOT NULL,
                awarded_by INTEGER,
                awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, user_id, achievement_name)
            )
            """
        )
        conn.commit()


def ensure_user(guild_id: int, user_id: int) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO users (
                guild_id, user_id, wallet, bank, daily_last_claim, work_last_claim,
                work_count, gamble_wins, gamble_losses, spent_total, xp, level,
                quest_key, quest_progress, quest_target, quest_reward, quest_xp_reward, quest_last_refresh
            ) VALUES (?, ?, 0, 0, NULL, NULL, 0, 0, 0, 0, 0, 1, NULL, 0, 0, 0, 0, NULL)
            """,
            (guild_id, user_id),
        )
        conn.commit()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def format_remaining(delta: timedelta) -> str:
    seconds = max(0, int(delta.total_seconds()))
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def check_cooldown(last_claim: Optional[str], cooldown: timedelta) -> tuple[bool, Optional[timedelta]]:
    parsed = parse_dt(last_claim)
    if parsed is None:
        return True, None
    remaining = (parsed + cooldown) - utc_now()
    if remaining.total_seconds() <= 0:
        return True, None
    return False, remaining


def get_balance(guild_id: int, user_id: int) -> tuple[int, int]:
    ensure_user(guild_id, user_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT wallet, bank FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return int(row["wallet"]), int(row["bank"])


def get_user_stats(guild_id: int, user_id: int) -> sqlite3.Row:
    ensure_user(guild_id, user_id)
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()


def update_user_fields(guild_id: int, user_id: int, **fields: object) -> None:
    if not fields:
        return
    ensure_user(guild_id, user_id)
    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [guild_id, user_id]
    with get_db() as conn:
        conn.execute(f"UPDATE users SET {columns} WHERE guild_id = ? AND user_id = ?", values)
        conn.commit()


def add_wallet(guild_id: int, user_id: int, amount: int) -> None:
    ensure_user(guild_id, user_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET wallet = wallet + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        conn.commit()


def move_money(guild_id: int, user_id: int, amount: int, to_bank: bool) -> bool:
    wallet, bank = get_balance(guild_id, user_id)
    if amount <= 0:
        return False
    if to_bank and wallet < amount:
        return False
    if (not to_bank) and bank < amount:
        return False
    new_wallet = wallet - amount if to_bank else wallet + amount
    new_bank = bank + amount if to_bank else bank - amount
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET wallet = ?, bank = ? WHERE guild_id = ? AND user_id = ?",
            (new_wallet, new_bank, guild_id, user_id),
        )
        conn.commit()
    return True


def achievement_exists(guild_id: int, name: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM achievements WHERE guild_id = ? AND name = ?",
            (guild_id, name.lower()),
        ).fetchone()
        return row is not None


def user_has_achievement(guild_id: int, user_id: int, name: str) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM user_achievements WHERE guild_id = ? AND user_id = ? AND achievement_name = ?",
            (guild_id, user_id, name.lower()),
        ).fetchone()
        return row is not None


def add_inventory_item(guild_id: int, user_id: int, item_name: str, quantity: int) -> None:
    ensure_user(guild_id, user_id)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO inventories (guild_id, user_id, item_name, quantity)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, item_name)
            DO UPDATE SET quantity = quantity + excluded.quantity
            """,
            (guild_id, user_id, item_name, quantity),
        )
        conn.execute(
            "DELETE FROM inventories WHERE guild_id = ? AND user_id = ? AND item_name = ? AND quantity <= 0",
            (guild_id, user_id, item_name),
        )
        conn.commit()


def get_inventory(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT item_name, quantity FROM inventories WHERE guild_id = ? AND user_id = ? ORDER BY item_name ASC",
            (guild_id, user_id),
        ).fetchall()


def total_items_owned(guild_id: int, user_id: int) -> int:
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS total FROM inventories WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return int(row["total"])


def set_progress(guild_id: int, user_id: int, achievement_name: str, progress_value: int) -> None:
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO user_achievement_progress (guild_id, user_id, achievement_name, progress_value)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, achievement_name)
            DO UPDATE SET progress_value = excluded.progress_value
            """,
            (guild_id, user_id, achievement_name, progress_value),
        )
        conn.commit()


def get_progress_rows(guild_id: int, user_id: int) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT achievement_name, progress_value FROM user_achievement_progress WHERE guild_id = ? AND user_id = ? ORDER BY achievement_name ASC",
            (guild_id, user_id),
        ).fetchall()


def mc_text(message: str) -> str:
    return f"{PLUGIN_TAG} {message}"


def sanitize_filename(value: str) -> str:
    return "".join(ch for ch in value.lower().replace(" ", "_") if ch.isalnum() or ch in "_-")[:50]


def toast_image_path(achievement_name: str) -> str:
    return os.path.join("generated_toasts", f"toast_{sanitize_filename(achievement_name)}.png")


def create_toast_image(achievement_name: str, description: str) -> Optional[str]:
    if not USE_TOAST_IMAGES:
        return None

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    os.makedirs("generated_toasts", exist_ok=True)

    scale = 3
    width, height = 160 * scale, 32 * scale
    img = Image.new("RGBA", (width, height), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    bg = (28,28,28,255)
    border_dark = (18,18,18,255)
    border_light = (90,90,90,255)
    gold = (255,221,87,255)
    white = (255,255,255,255)

    draw.rectangle((0,0,width-1,height-1), fill=border_dark)
    draw.rectangle((3,3,width-4,height-4), fill=border_light)
    draw.rectangle((6,6,width-7,height-7), fill=bg)

    icon_x, icon_y = 10*scale, 7*scale
    icon_size = 18*scale

    icon_path = os.path.join(ASSET_ICON_PATH, sanitize_filename(achievement_name)+".png")

    if os.path.exists(icon_path):
        icon = Image.open(icon_path).convert("RGBA").resize((icon_size,icon_size), Image.NEAREST)
        img.paste(icon,(icon_x,icon_y),icon)
    else:
        draw.rectangle((icon_x,icon_y,icon_x+icon_size,icon_y+icon_size), fill=(110,78,44))

    try:
        title_font = ImageFont.truetype(FONT_PATH if os.path.exists(FONT_PATH) else "DejaVuSans-Bold.ttf", 7 * scale)
        body_font = ImageFont.truetype(FONT_PATH if os.path.exists(FONT_PATH) else "DejaVuSans-Bold.ttf", 8 * scale)
    except Exception:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()

    title="Achievement Get!"
    body=achievement_name[:24]

    tx = 36*scale
    draw.text((tx,6*scale),title,font=title_font,fill=gold)
    draw.text((tx,14*scale),body,font=body_font,fill=white)

    output = toast_image_path(achievement_name)
    img.save(output)
    return output


async def send_plugin_message(interaction: discord.Interaction, content: str, *, embed: Optional[discord.Embed] = None, file: Optional[discord.File] = None, ephemeral: bool = False) -> None:
    content = mc_text(content)
    if interaction.response.is_done():
        await interaction.followup.send(content, embed=embed, file=file, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(content, embed=embed, file=file, ephemeral=ephemeral)


async def play_event_sound_for_member(member: discord.Member, sound_key: str) -> None:
    if not USE_EVENT_SOUNDS or not member.voice or not member.voice.channel:
        return
    file_path = MC_SOUNDS.get(sound_key)
    if not file_path or not os.path.exists(file_path):
        return
    try:
        vc = member.guild.voice_client
        if not vc:
            vc = await member.voice.channel.connect()
        elif vc.channel != member.voice.channel:
            await vc.move_to(member.voice.channel)
        if vc.is_playing():
            vc.stop()
        vc.play(discord.FFmpegPCMAudio(file_path))
    except Exception:
        return


async def grant_role_if_configured(member: discord.Member, role_id: Optional[int]) -> Optional[str]:
    if not role_id:
        return None
    role = member.guild.get_role(role_id)
    if not role:
        return f"Configured role `{role_id}` was not found."
    if role in member.roles:
        return f"{member.display_name} already has the role **{role.name}**."
    try:
        await member.add_roles(role, reason="Achievement reward")
        return f"Granted role **{role.name}** to {member.mention}."
    except discord.Forbidden:
        return "I do not have permission to assign that role."


async def award_achievement(guild: discord.Guild, member: discord.Member, achievement_name: str, awarded_by: Optional[int]) -> tuple[bool, str, Optional[str], Optional[str]]:
    normalized = achievement_name.lower().strip()
    ensure_user(guild.id, member.id)
    with get_db() as conn:
        ach = conn.execute(
            "SELECT name, description, reward_amount, role_id FROM achievements WHERE guild_id = ? AND name = ?",
            (guild.id, normalized),
        ).fetchone()
        if not ach:
            return False, f"Achievement `{normalized}` does not exist.", None, None
        existing = conn.execute(
            "SELECT 1 FROM user_achievements WHERE guild_id = ? AND user_id = ? AND achievement_name = ?",
            (guild.id, member.id, normalized),
        ).fetchone()
        if existing:
            return False, f"{member.mention} already has achievement **{ach['name']}**.", None, None
        conn.execute(
            "INSERT INTO user_achievements (guild_id, user_id, achievement_name, awarded_by) VALUES (?, ?, ?, ?)",
            (guild.id, member.id, normalized, awarded_by),
        )
        if ach["reward_amount"] > 0:
            conn.execute(
                "UPDATE users SET wallet = wallet + ? WHERE guild_id = ? AND user_id = ?",
                (int(ach["reward_amount"]), guild.id, member.id),
            )
        conn.commit()
    role_result = await grant_role_if_configured(member, ach["role_id"])
    toast_path = create_toast_image(ach["name"], ach["description"])
    message = (
        f"Achievement unlocked for {member.mention}: **{ach['name']}**/n"
        f"Description: {ach['description']}/n"
        f"Coin reward: **{ach['reward_amount']}**"
    )
    if role_result:
        message += f"/n{role_result}"
    return True, message, toast_path, "levelup"


async def check_auto_achievements(member: discord.Member) -> list[str]:
    wallet, bank = get_balance(member.guild.id, member.id)
    total = wallet + bank
    granted = []
    for achievement_name, threshold in AUTO_ACHIEVEMENTS.items():
        if total >= threshold and achievement_exists(member.guild.id, achievement_name) and not user_has_achievement(member.guild.id, member.id, achievement_name):
            ok, msg, _, _ = await award_achievement(member.guild, member, achievement_name, None)
            if ok:
                granted.append(msg)
    stats = get_user_stats(member.guild.id, member.id)
    derived_progress = {
        "work_count": int(stats["work_count"]),
        "spent_total": int(stats["spent_total"]),
        "items_owned": total_items_owned(member.guild.id, member.id),
    }
    for achievement_name, config in PROGRESS_ACHIEVEMENTS.items():
        value = derived_progress.get(config["metric"], 0)
        set_progress(member.guild.id, member.id, achievement_name, value)
        if value >= int(config["target"]) and achievement_exists(member.guild.id, achievement_name) and not user_has_achievement(member.guild.id, member.id, achievement_name):
            ok, msg, _, _ = await award_achievement(member.guild, member, achievement_name, None)
            if ok:
                granted.append(msg)
    return granted


@bot.event
async def on_ready() -> None:
    init_db()
    os.makedirs("generated_toasts", exist_ok=True)
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application commands.")
    except Exception as exc:
        print(f"Command sync failed: {exc}")
    print(f"Logged in as {bot.user}")


@bot.tree.command(name="create_achievement", description="Create a new achievement")
@app_commands.checks.has_permissions(manage_guild=True)
async def create_achievement(interaction: discord.Interaction, name: str, description: str, reward_amount: app_commands.Range[int, 0, 1_000_000] = 0, role: Optional[discord.Role] = None) -> None:
    normalized = name.lower().strip()
    with get_db() as conn:
        existing = conn.execute("SELECT 1 FROM achievements WHERE guild_id = ? AND name = ?", (interaction.guild_id, normalized)).fetchone()
        if existing:
            await send_plugin_message(interaction, f"Achievement `{normalized}` already exists.", ephemeral=True)
            return
        role_id = role.id if role else ACHIEVEMENT_ROLE_REWARDS.get(normalized)
        conn.execute(
            "INSERT INTO achievements (guild_id, name, description, reward_amount, role_id) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, normalized, description, reward_amount, role_id),
        )
        conn.commit()
        extra = f"Role reward: {role.mention}" if role else ""
    await send_plugin_message(
        interaction,
        f"Created achievement **{normalized}**./nReward: **{reward_amount}** coins{extra}",)


@bot.tree.command(name="list_achievements", description="List all achievements in this server")
async def list_achievements(interaction: discord.Interaction) -> None:
    with get_db() as conn:
        rows = conn.execute("SELECT name, description, reward_amount, role_id FROM achievements WHERE guild_id = ? ORDER BY name ASC", (interaction.guild_id,)).fetchall()
    if not rows:
        await send_plugin_message(interaction, "No achievements have been created yet.")
        return
    embed = discord.Embed(title="Achievement Registry")
    for row in rows[:25]:
        role_text = f" | Role: <@&{row['role_id']}>" if row["role_id"] else ""
        embed.add_field(
            name=row["name"],
            value=f"{row['description']}/nReward: {row['reward_amount']} coins{role_text}",
            inline=False,
        )
    await send_plugin_message(interaction, "Opening achievement registry.", embed=embed)


@bot.tree.command(name="award_achievement", description="Award an achievement to a member")
@app_commands.checks.has_permissions(manage_roles=True)
async def award_achievement_cmd(interaction: discord.Interaction, member: discord.Member, achievement_name: str) -> None:
    ok, msg, toast_path, sound_key = await award_achievement(interaction.guild, member, achievement_name, interaction.user.id)
    file = discord.File(toast_path, filename=os.path.basename(toast_path)) if ok and toast_path and os.path.exists(toast_path) else None
    if ok and sound_key:
        await play_event_sound_for_member(member, sound_key)
    await send_plugin_message(interaction, msg, file=file, ephemeral=not ok)


@bot.tree.command(name="my_achievements", description="View your achievements")
async def my_achievements(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT ua.achievement_name, a.description, a.reward_amount, ua.awarded_at
            FROM user_achievements ua
            JOIN achievements a ON a.guild_id = ua.guild_id AND a.name = ua.achievement_name
            WHERE ua.guild_id = ? AND ua.user_id = ?
            ORDER BY ua.awarded_at DESC
            """,
            (interaction.guild_id, target.id),
        ).fetchall()
    if not rows:
        await send_plugin_message(interaction, f"{target.mention} has no achievements yet.")
        return
    embed = discord.Embed(title=f"Achievements for {target.display_name}")
    for row in rows[:25]:
        embed.add_field(
            name=row["achievement_name"],
            value=f"{row['description']}/nReward: {row['reward_amount']} coins/nAwarded: {row['awarded_at']}",
            inline=False,
        )
    await send_plugin_message(interaction, f"Opening player achievement log for {target.display_name}.", embed=embed)


@bot.tree.command(name="balance", description="Check economy balance")
async def balance(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    stats = get_user_stats(interaction.guild_id, target.id)
    wallet, bank = int(stats['wallet']), int(stats['bank'])
    embed = discord.Embed(title=f"Balance for {target.display_name}")
    embed.add_field(name="Wallet", value=str(wallet))
    embed.add_field(name="Bank", value=str(bank))
    embed.add_field(name="Total", value=str(wallet + bank), inline=False)
    embed.add_field(name="Level", value=str(int(stats['level'])))
    embed.add_field(name="XP", value=f"{int(stats['xp'])}/{level_requirement(int(stats['level']))}")
    await send_plugin_message(interaction, "Opening economy display.", embed=embed)


@bot.tree.command(name="daily", description="Claim a daily reward")
async def daily(interaction: discord.Interaction) -> None:
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    available, remaining = check_cooldown(stats["daily_last_claim"], timedelta(hours=DAILY_COOLDOWN_HOURS))
    if not available:
        await send_plugin_message(interaction, f"Your daily reward is on cooldown. Try again in **{format_remaining(remaining)}**.", ephemeral=True)
        return
    reward = random.randint(90, 150)
    xp_gain = random.randint(*XP_PER_DAILY)
    add_wallet(interaction.guild_id, interaction.user.id, reward)
    xp, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, xp_gain)
    update_user_fields(interaction.guild_id, interaction.user.id, daily_last_claim=utc_now().isoformat())
    auto_msgs = await check_auto_achievements(interaction.user)
    quest_msg = advance_quest(interaction.guild_id, interaction.user.id, "daily")
    response = f"You claimed **{reward}** coins and **{xp_gain} XP**."
    if leveled_up:
        response += f"/nLevel up! You are now level **{level}**."
    if quest_msg:
        response += f"/n{quest_msg}"
    if auto_msgs:
        response += "/n /n New achievements unlocked:" + "/n/n".join(auto_msgs)
    await send_plugin_message(interaction, response)
    await play_event_sound_for_member(interaction.user, "coin")


@bot.tree.command(name="work", description="Earn some coins")
async def work(interaction: discord.Interaction) -> None:
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    available, remaining = check_cooldown(stats["work_last_claim"], timedelta(minutes=WORK_COOLDOWN_MINUTES))
    if not available:
        await send_plugin_message(interaction, f"Work is on cooldown. Try again in **{format_remaining(remaining)}**.", ephemeral=True)
        return
    reward = random.randint(25, 90)
    xp_gain = random.randint(*XP_PER_WORK)
    add_wallet(interaction.guild_id, interaction.user.id, reward)
    xp, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, xp_gain)
    update_user_fields(interaction.guild_id, interaction.user.id, work_last_claim=utc_now().isoformat(), work_count=int(stats["work_count"]) + 1)
    auto_msgs = await check_auto_achievements(interaction.user)
    quest_msg = advance_quest(interaction.guild_id, interaction.user.id, "work")
    response = f"You worked and earned **{reward}** coins and **{xp_gain} XP**."
    if leveled_up:
        response += f"/nLevel up! You are now level **{level}**."
    if quest_msg:
        response += f"/n{quest_msg}"
    if auto_msgs:
        response += "/n/nNew achievements unlocked:" + "/n/n".join(auto_msgs)
    await send_plugin_message(interaction, response)
    await play_event_sound_for_member(interaction.user, "note")


@bot.tree.command(name="deposit", description="Deposit wallet coins into your bank")
async def deposit(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    if not move_money(interaction.guild_id, interaction.user.id, amount, to_bank=True):
        await send_plugin_message(interaction, "Deposit failed. Check your wallet balance.", ephemeral=True)
        return
    await send_plugin_message(interaction, f"Deposited **{amount}** coins into your bank.")
    await play_event_sound_for_member(interaction.user, "note")


@bot.tree.command(name="withdraw", description="Withdraw bank coins into your wallet")
async def withdraw(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    if not move_money(interaction.guild_id, interaction.user.id, amount, to_bank=False):
        await send_plugin_message(interaction, "Withdrawal failed. Check your bank balance.", ephemeral=True)
        return
    await send_plugin_message(interaction, f"Withdrew **{amount}** coins into your wallet.")
    await play_event_sound_for_member(interaction.user, "note")


@bot.tree.command(name="pay", description="Pay another member")
async def pay(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    if member.id == interaction.user.id:
        await send_plugin_message(interaction, "You cannot pay yourself.", ephemeral=True)
        return
    wallet, _ = get_balance(interaction.guild_id, interaction.user.id)
    if wallet < amount:
        await send_plugin_message(interaction, "You do not have enough wallet coins.", ephemeral=True)
        return
    add_wallet(interaction.guild_id, interaction.user.id, -amount)
    add_wallet(interaction.guild_id, member.id, amount)
    auto_msgs = await check_auto_achievements(interaction.user) + await check_auto_achievements(member)
    response = f"Sent **{amount}** coins to {member.mention}."
    if auto_msgs:
        response += "/n/nNew achievements unlocked:" + "/n/n".join(auto_msgs)
    await send_plugin_message(interaction, response)
    await play_event_sound_for_member(interaction.user, "coin")


@bot.tree.command(name="gamble", description="Bet coins for a chance to win")
async def gamble(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    wallet, _ = get_balance(interaction.guild_id, interaction.user.id)
    if wallet < amount:
        await send_plugin_message(interaction, "You do not have enough wallet coins.", ephemeral=True)
        return
    roll = random.random()
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    xp_gain = 0
    if roll < 0.45:
        add_wallet(interaction.guild_id, interaction.user.id, amount)
        update_user_fields(interaction.guild_id, interaction.user.id, gamble_wins=int(stats["gamble_wins"]) + 1)
        response = f"You won **{amount}** coins."
        sound = "coin"
        xp_gain = random.randint(*XP_PER_GAMBLE_WIN)
    elif roll < 0.50:
        jackpot = amount * 2
        add_wallet(interaction.guild_id, interaction.user.id, jackpot)
        update_user_fields(interaction.guild_id, interaction.user.id, gamble_wins=int(stats["gamble_wins"]) + 1)
        response = f"Jackpot! You won **{jackpot}** coins."
        sound = "villager"
        xp_gain = random.randint(XP_PER_GAMBLE_WIN[1], XP_PER_GAMBLE_WIN[1] + 12)
    else:
        add_wallet(interaction.guild_id, interaction.user.id, -amount)
        update_user_fields(interaction.guild_id, interaction.user.id, gamble_losses=int(stats["gamble_losses"]) + 1)
        response = f"You lost **{amount}** coins."
        sound = "oof"
    if xp_gain:
        _, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, xp_gain)
        response += f"/nYou gained **{xp_gain} XP**."
        if leveled_up:
            response += f"/nLevel up! You are now level **{level}**."
    quest_msg = advance_quest(interaction.guild_id, interaction.user.id, "gamble")
    if quest_msg:
        response += f"/n{quest_msg}"
    auto_msgs = await check_auto_achievements(interaction.user)
    if auto_msgs:
        response += "/n/nNew achievements unlocked:" + "/n/n".join(auto_msgs)
    await send_plugin_message(interaction, response)
    await play_event_sound_for_member(interaction.user, sound)


@bot.tree.command(name="shop", description="View the item shop")
async def shop(interaction: discord.Interaction, category: Optional[str] = None) -> None:
    cat = (category or "general").lower().strip()
    items = SHOP_CATEGORIES.get(cat)
    if not items:
        await send_plugin_message(interaction, f"Unknown shop category. Available: {', '.join(SHOP_CATEGORIES.keys())}", ephemeral=True)
        return
    keeper = NPC_SHOPKEEPERS.get(cat, {"name": "Trader", "title": "Merchant"})
    embed = discord.Embed(title=f"{keeper['name']} — {keeper['title']}")
    for item_name in items:
        item = SHOP_ITEMS[item_name]
        embed.add_field(name=item_name, value=f"{item['description']}/nBuy: {item['price']} | Sell: {item['sell_price']}", inline=False)
    await send_plugin_message(interaction, f"Opening {cat} shop.", embed=embed)


@bot.tree.command(name="buy", description="Buy an item from the shop")
async def buy(interaction: discord.Interaction, item_name: str, quantity: app_commands.Range[int, 1, 100] = 1) -> None:
    item_key = item_name.lower().strip()
    item = SHOP_ITEMS.get(item_key)
    if not item:
        await send_plugin_message(interaction, "That item does not exist in the shop.", ephemeral=True)
        return
    total_cost = int(item["price"]) * quantity
    wallet, _ = get_balance(interaction.guild_id, interaction.user.id)
    if wallet < total_cost:
        await send_plugin_message(interaction, "You do not have enough wallet coins.", ephemeral=True)
        return
    add_wallet(interaction.guild_id, interaction.user.id, -total_cost)
    add_inventory_item(interaction.guild_id, interaction.user.id, item_key, quantity)
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    update_user_fields(interaction.guild_id, interaction.user.id, spent_total=int(stats["spent_total"]) + total_cost)
    xp_gain = random.randint(*XP_PER_BUY)
    _, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, xp_gain)
    auto_msgs = await check_auto_achievements(interaction.user)
    quest_msg = advance_quest(interaction.guild_id, interaction.user.id, "buy")
    response = f"Bought **{quantity}x {item_key}** for **{total_cost}** coins and earned **{xp_gain} XP**."
    if leveled_up:
        response += f"/nLevel up! You are now level **{level}**."
    if quest_msg:
        response += f"/n{quest_msg}"
    if auto_msgs:
        response += "/n/nNew achievements unlocked:" + "/n/n".join(auto_msgs)
    await send_plugin_message(interaction, response)
    await play_event_sound_for_member(interaction.user, "anvil")


@bot.tree.command(name="sell", description="Sell an item from your inventory")
async def sell(interaction: discord.Interaction, item_name: str, quantity: app_commands.Range[int, 1, 100] = 1) -> None:
    item_key = item_name.lower().strip()
    item = SHOP_ITEMS.get(item_key)
    if not item:
        await send_plugin_message(interaction, "That item cannot be sold here.", ephemeral=True)
        return
    owned = {row['item_name']: int(row['quantity']) for row in get_inventory(interaction.guild_id, interaction.user.id)}.get(item_key, 0)
    if owned < quantity:
        await send_plugin_message(interaction, "You do not own enough of that item.", ephemeral=True)
        return
    payout = int(item["sell_price"]) * quantity
    add_inventory_item(interaction.guild_id, interaction.user.id, item_key, -quantity)
    add_wallet(interaction.guild_id, interaction.user.id, payout)
    auto_msgs = await check_auto_achievements(interaction.user)
    response = f"Sold **{quantity}x {item_key}** for **{payout}** coins."
    if auto_msgs:
        response += "/n/nNew achievements unlocked:" + "/n/n".join(auto_msgs)
    await send_plugin_message(interaction, response)
    await play_event_sound_for_member(interaction.user, "coin")


@bot.tree.command(name="inventory", description="View inventory")
async def inventory(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    rows = get_inventory(interaction.guild_id, target.id)
    if not rows:
        await send_plugin_message(interaction, f"{target.mention} has no items.")
        return
    embed = discord.Embed(title=f"Inventory for {target.display_name}")
    for row in rows[:25]:
        embed.add_field(name=row['item_name'], value=f"Quantity: {row['quantity']}", inline=False)
    await send_plugin_message(interaction, f"Opening inventory for {target.display_name}.", embed=embed)


@bot.tree.command(name="progress", description="View achievement progress")
async def progress(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    await check_auto_achievements(target)
    rows = get_progress_rows(interaction.guild_id, target.id)
    if not rows:
        await send_plugin_message(interaction, "No tracked progress yet.")
        return
    embed = discord.Embed(title=f"Achievement Progress for {target.display_name}")
    for row in rows[:25]:
        config = PROGRESS_ACHIEVEMENTS.get(row['achievement_name'])
        if not config:
            continue
        embed.add_field(name=row['achievement_name'], value=f"{config['description']}/nProgress: {int(row['progress_value'])}/{int(config['target'])}", inline=False)
    await send_plugin_message(interaction, f"Opening achievement progress for {target.display_name}.", embed=embed)


@bot.tree.command(name="leaderboard", description="View the richest members")
async def leaderboard(interaction: discord.Interaction) -> None:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT user_id, wallet, bank, level, (wallet + bank) AS total FROM users WHERE guild_id = ? ORDER BY total DESC, level DESC LIMIT 10",
            (interaction.guild_id,),
        ).fetchall()
    if not rows:
        await send_plugin_message(interaction, "No economy data yet.")
        return
    lines = []
    for i, row in enumerate(rows, start=1):
        user = interaction.guild.get_member(int(row['user_id']))
        name = user.mention if user else f"User {row['user_id']}"
        lines.append(f"**{i}.** {name} — {row['total']} coins | Lv.{row['level']}")
    embed = discord.Embed(title="Economy Leaderboard", description="/n".join(lines))
    await send_plugin_message(interaction, "Opening economy leaderboard.", embed=embed)


@bot.tree.command(name="xp", description="View your Minecraft-style level data")
async def xp(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    stats = ensure_daily_quest(interaction.guild_id, target.id)
    embed = discord.Embed(title=f"XP Profile: {target.display_name}")
    embed.add_field(name="Level", value=str(int(stats['level'])))
    embed.add_field(name="XP", value=f"{int(stats['xp'])}/{level_requirement(int(stats['level']))}")
    embed.add_field(name="Quest", value=f"{stats['quest_key'] or 'none'} ({int(stats['quest_progress'])}/{int(stats['quest_target'])})", inline=False)
    embed.add_field(name="Class", value="Adventurer", inline=False)
    await send_plugin_message(interaction, f"Opening XP profile for {target.display_name}.", embed=embed)


@bot.tree.command(name="menu", description="Open the Minecraft plugin menu")
async def menu(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="MineCraftify Menu")
    embed.add_field(name="Economy", value="/balance, /daily, /work, /pay, /leaderboard", inline=False)
    embed.add_field(name="RPG", value="/xp, /quest, /crate, /boss, /raid", inline=False)
    embed.add_field(name="Shop", value="/shop, /buy, /sell, /inventory", inline=False)
    embed.add_field(name="Achievements", value="/list_achievements, /my_achievements, /progress, /advancements", inline=False)
    embed.add_field(name="Music", value="/disc, /stopdisc, /mcsfx", inline=False)
    await send_plugin_message(interaction, "Opening plugin menu.", embed=embed)


@bot.tree.command(name="advancements", description="Open the Minecraft-style advancement tabs")
async def advancements(interaction: discord.Interaction, member: Optional[discord.Member] = None, tab: Optional[str] = None) -> None:
    target = member or interaction.user
    selected = (tab or "economy").lower().strip()
    tabs = {
        "economy": ["first_coin", "starter", "wealthy", "tycoon", "spender_i"],
        "utility": ["worker_i", "collector_i"],
        "combat": ["boss_slayer", "raider"],
    }
    if selected not in tabs:
        await send_plugin_message(interaction, f"Unknown tab. Available: {', '.join(tabs.keys())}", ephemeral=True)
        return

    with get_db() as conn:
        owned_rows = conn.execute(
            "SELECT achievement_name FROM user_achievements WHERE guild_id = ? AND user_id = ?",
            (interaction.guild_id, target.id),
        ).fetchall()
        desc_rows = conn.execute(
            "SELECT name, description FROM achievements WHERE guild_id = ?",
            (interaction.guild_id,),
        ).fetchall()

    owned = {row['achievement_name'] for row in owned_rows}
    descriptions = {row['name']: row['description'] for row in desc_rows}

    embed = discord.Embed(title=f"Advancement Tab: {selected.title()} — {target.display_name}")
    for ach in tabs[selected]:
        status = "✅ Unlocked" if ach in owned else "⬜ Locked"
        desc = descriptions.get(ach, "No server achievement created yet.")
        embed.add_field(name=ach, value=f"{status}/n{desc}", inline=False)
    embed.set_footer(text="Tabs: economy, utility, combat")
    await send_plugin_message(interaction, f"Opening advancement tab `{selected}` for {target.display_name}.", embed=embed)


@bot.tree.command(name="crate", description="Open a rarity crate")
async def crate(interaction: discord.Interaction) -> None:
    roll = random.random()
    if roll < 0.60:
        rarity = "common"; coins = random.randint(40, 90); xp_gain = 8
    elif roll < 0.85:
        rarity = "uncommon"; coins = random.randint(90, 160); xp_gain = 14
    elif roll < 0.96:
        rarity = "rare"; coins = random.randint(160, 280); xp_gain = 24
    elif roll < 0.995:
        rarity = "epic"; coins = random.randint(280, 500); xp_gain = 45
    else:
        rarity = "legendary"; coins = random.randint(700, 1200); xp_gain = 90
    add_wallet(interaction.guild_id, interaction.user.id, coins)
    _, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, xp_gain)
    embed = discord.Embed(title="Loot Crate Opened", color=RARITY_COLORS[rarity])
    embed.add_field(name="Rarity", value=rarity.title())
    embed.add_field(name="Coins", value=str(coins))
    embed.add_field(name="XP", value=str(xp_gain))
    msg = "Crate opened successfully."
    if leveled_up:
        msg += f" Level up! You are now level **{level}**."
    await send_plugin_message(interaction, msg, embed=embed)
    await play_event_sound_for_member(interaction.user, "villager" if rarity in {"epic", "legendary"} else "coin")


@bot.tree.command(name="quest", description="View your active daily quest")
async def quest(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    stats = ensure_daily_quest(interaction.guild_id, target.id)
    embed = discord.Embed(title=f"Daily Quest: {target.display_name}")
    embed.add_field(name="Objective", value=str(stats['quest_key'] or 'none'))
    embed.add_field(name="Progress", value=f"{int(stats['quest_progress'])}/{int(stats['quest_target'])}")
    embed.add_field(name="Rewards", value=f"{int(stats['quest_reward'])} coins + {int(stats['quest_xp_reward'])} XP", inline=False)
    await send_plugin_message(interaction, f"Opening daily quest for {target.display_name}.", embed=embed)


@bot.tree.command(name="boss", description="Fight a boss event")
async def boss(interaction: discord.Interaction, boss_name: str = "zombie_king") -> None:
    boss_key = boss_name.lower().strip()
    boss = BOSS_EVENTS.get(boss_key)
    if not boss:
        await send_plugin_message(interaction, f"Unknown boss. Available: {', '.join(BOSS_EVENTS.keys())}", ephemeral=True)
        return
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    damage = random.randint(25, 60) + (int(stats['level']) * 4)
    threshold = boss['hp'] * 0.22
    if damage >= threshold:
        add_wallet(interaction.guild_id, interaction.user.id, int(boss['reward']))
        _, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, int(boss['xp']))
        embed = discord.Embed(title=f"Boss Defeated: {boss_key}", color=RARITY_COLORS[boss['rarity']])
        embed.add_field(name="Damage", value=str(damage))
        embed.add_field(name="Reward", value=f"{boss['reward']} coins")
        embed.add_field(name="XP", value=str(boss['xp']))
        msg = f"You defeated **{boss_key}**."
        if leveled_up:
            msg += f" Level up! You are now level **{level}**."
        await send_plugin_message(interaction, msg, embed=embed)
        await play_event_sound_for_member(interaction.user, "villager")
    else:
        embed = discord.Embed(title=f"Boss Escaped: {boss_key}", color=RARITY_COLORS[boss['rarity']])
        embed.add_field(name="Damage Dealt", value=str(damage))
        embed.add_field(name="Needed", value=f"{int(threshold)}+")
        await send_plugin_message(interaction, f"You damaged **{boss_key}**, but it escaped.", embed=embed)
        await play_event_sound_for_member(interaction.user, "oof")


@bot.tree.command(name="mcsfx", description="Play a Minecraft sound effect in your voice channel")
async def mcsfx(interaction: discord.Interaction, sound: str) -> None:
    if not interaction.user.voice or not interaction.user.voice.channel:
        await send_plugin_message(interaction, "You must be in a voice channel.", ephemeral=True)
        return
    sound_key = sound.lower().strip()
    file_path = MC_SOUNDS.get(sound_key)
    if not file_path:
        await send_plugin_message(interaction, f"Unknown sound. Available: {', '.join(MC_SOUNDS.keys())}", ephemeral=True)
        return
    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await vc.move_to(interaction.user.voice.channel)
    if vc.is_playing():
        vc.stop()
    vc.play(discord.FFmpegPCMAudio(file_path))
    await send_plugin_message(interaction, f"Playing **{sound_key}** Minecraft sound.")


def load_disc_sounds() -> dict:
    discs = {}
    base = os.path.join("sounds", "discs")
    if not os.path.exists(base):
        return discs
    for file in os.listdir(base):
        if file.lower().endswith(".ogg"):
            name = os.path.splitext(file)[0].lower()
            discs[name] = os.path.join(base, file)
    return discs

DISC_SOUNDS = load_disc_sounds()
JUKEBOX_SPLASH_TEXTS = [
    "Now that's a tune!",
    "Certified banger!",
    "Straight from the overworld!",
    "C418 would approve.",
    "Insert disc... vibes activated.",
    "Turning Discord into a jukebox!",
    "Villagers are vibing.",
    "Now playing: absolute heat.",
    "Groove level: MAXIMUM.",
    "This disc slaps.",
    "Nether-approved soundtrack.",
    "Diamond-tier music!",
    "Redstone-powered rhythm.",
]
JUKEBOX_QUEUES = []
JUKEBOX_ICONS = {
    "13": "💿",
    "cat": "🐈",
    "blocks": "🧱",
    "chirp": "🐤",
    "far": "🌌",
    "mall": "🛍️",
    "mellohi": "🎵",
    "stal": "🦴",
    "strad": "🎻",
    "ward": "🌿",
    "11": "💽",
    "wait": "⏳",
    "pigstep": "🐖",
    "otherside": "🌠",
    "relic": "🏺",
    "5": "🕳️",
    "creator": "🛠️",
    "creator_music_box": "📦",
    "precipice": "⛰️",
    "tears": "💧",
}


@bot.tree.command(name="disc", description="Play a Minecraft music disc in your voice channel")
async def disc(interaction: discord.Interaction, name: str) -> None:
    if not interaction.user.voice or not interaction.user.voice.channel:
        await send_plugin_message(interaction, "You must be in a voice channel.", ephemeral=True)
        return

    disc_name = name.lower().strip()
    file_path = DISC_SOUNDS.get(disc_name)
    if not file_path:
        await send_plugin_message(interaction, f"Unknown disc. Available: {', '.join(DISC_SOUNDS.keys())}", ephemeral=True)
        return
    if not os.path.exists(file_path):
        await send_plugin_message(interaction, f"Disc file not found: `{file_path}`", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await vc.move_to(interaction.user.voice.channel)

    if vc.is_playing():
        vc.stop()

    vc.play(discord.FFmpegPCMAudio(file_path))
    splash = random.choice(JUKEBOX_SPLASH_TEXTS)
    icon = JUKEBOX_ICONS.get(disc_name, "💿")
    queue_preview = ", ".join(f"{JUKEBOX_ICONS.get(name, '💿')} {name}" for name in JUKEBOX_QUEUES.get(interaction.guild_id, [disc_name])[:5])
    await send_plugin_message(
        interaction,
        f"{icon} **Now Playing:** `{disc_name}`/n✨ *{splash}*/n📜 **Queue:** {queue_preview}"
    )


@bot.tree.command(name="stopdisc", description="Stop the currently playing music disc")
async def stopdisc(interaction: discord.Interaction) -> None:
    vc = interaction.guild.voice_client
    if not vc or not vc.is_connected():
        await send_plugin_message(interaction, "No jukebox is active right now.", ephemeral=True)
        return
    if vc.is_playing():
        vc.stop()
        await send_plugin_message(interaction, "⏹️ Jukebox stopped. Disc ejected.")
        return
    await send_plugin_message(interaction, "Nothing is currently playing.", ephemeral=True)


@create_achievement.error
@award_achievement_cmd.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.errors.MissingPermissions):
        await send_plugin_message(interaction, "You do not have permission to use this command.", ephemeral=True)
        return
    raise error


@bot.tree.command(name="raid", description="Start a coop-style raid encounter")
async def raid(interaction: discord.Interaction, boss_name: str = "warden") -> None:
    boss_key = boss_name.lower().strip()
    boss = RAID_BOSSES.get(boss_key)
    if not boss:
        await send_plugin_message(interaction, f"Unknown raid boss. Available: {', '.join(RAID_BOSSES.keys())}", ephemeral=True)
        return
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    base = int(stats['level']) * 8
    damage = random.randint(80, 180) + base
    threshold = boss['hp'] * 0.18
    embed = discord.Embed(title=f"Raid Encounter: {boss_key.title()}", color=RARITY_COLORS[boss['rarity']])
    embed.add_field(name="Your Damage", value=str(damage))
    embed.add_field(name="Boss HP", value=str(boss['hp']))
    if damage >= threshold:
        add_wallet(interaction.guild_id, interaction.user.id, int(boss['reward']))
        _, level, leveled_up = grant_xp(interaction.guild_id, interaction.user.id, int(boss['xp']))
        embed.add_field(name="Reward", value=f"{boss['reward']} coins + {boss['xp']} XP", inline=False)
        msg = f"Raid clear against **{boss_key}**."
        if leveled_up:
            msg += f" Level up! You are now level **{level}**."
        await send_plugin_message(interaction, msg, embed=embed)
        await play_event_sound_for_member(interaction.user, "villager")
    else:
        embed.add_field(name="Required Damage", value=f"{int(threshold)}+", inline=False)
        await send_plugin_message(interaction, f"The raid boss **{boss_key}** survived your attack.", embed=embed)
        await play_event_sound_for_member(interaction.user, "oof")


if __name__ == "__main__":
    init_db()
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Set your bot token in the DISCORD_TOKEN environment variable.")
    bot.run(TOKEN)
