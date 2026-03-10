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

# Configure achievement -> role rewards here.
# Use role IDs from your server.
ACHIEVEMENT_ROLE_REWARDS = {
    # "first_steps": 123456789012345678,
    # "rich": 234567890123456789,
}

# Optional achievement auto-award rules based on total wealth.
AUTO_ACHIEVEMENTS = {
    "first_coin": 1,
    "starter": 100,
    "wealthy": 1000,
    "tycoon": 10000,
}

# Optional achievement progress targets based on activity.
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

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = False

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
                PRIMARY KEY (guild_id, user_id, achievement_name),
                FOREIGN KEY (guild_id, achievement_name)
                    REFERENCES achievements (guild_id, name)
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
                work_count, gamble_wins, gamble_losses, spent_total
            )
            VALUES (?, ?, 0, 0, NULL, NULL, 0, 0, 0, 0)
            """,
            (guild_id, user_id),
        )
        conn.commit()


def get_balance(guild_id: int, user_id: int) -> tuple[int, int]:
    ensure_user(guild_id, user_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT wallet, bank FROM users WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
        return int(row["wallet"]), int(row["bank"])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value)


def format_remaining(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


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
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [guild_id, user_id]
    with get_db() as conn:
        conn.execute(
            f"UPDATE users SET {columns} WHERE guild_id = ? AND user_id = ?",
            values,
        )
        conn.commit()


def check_cooldown(last_claim: Optional[str], cooldown: timedelta) -> tuple[bool, Optional[timedelta]]:
    parsed = parse_dt(last_claim)
    if parsed is None:
        return True, None
    remaining = (parsed + cooldown) - utc_now()
    if remaining.total_seconds() <= 0:
        return True, None
    return False, remaining


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
    ensure_user(guild_id, user_id)
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


def add_wallet(guild_id: int, user_id: int, amount: int) -> None:
    ensure_user(guild_id, user_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET wallet = wallet + ? WHERE guild_id = ? AND user_id = ?",
            (amount, guild_id, user_id),
        )
        conn.commit()


def move_money(guild_id: int, user_id: int, amount: int, to_bank: bool) -> bool:
    ensure_user(guild_id, user_id)
    wallet, bank = get_balance(guild_id, user_id)
    if amount <= 0:
        return False
    if to_bank:
        if wallet < amount:
            return False
        new_wallet = wallet - amount
        new_bank = bank + amount
    else:
        if bank < amount:
            return False
        new_wallet = wallet + amount
        new_bank = bank - amount

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
            """
            SELECT 1 FROM user_achievements
            WHERE guild_id = ? AND user_id = ? AND achievement_name = ?
            """,
            (guild_id, user_id, name.lower()),
        ).fetchone()
        return row is not None


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
        return "I don't have permission to assign that role."


async def award_achievement(
    guild: discord.Guild,
    member: discord.Member,
    achievement_name: str,
    awarded_by: Optional[int],
) -> tuple[bool, str]:
    normalized = achievement_name.lower().strip()
    ensure_user(guild.id, member.id)

    with get_db() as conn:
        ach = conn.execute(
            """
            SELECT name, description, reward_amount, role_id
            FROM achievements
            WHERE guild_id = ? AND name = ?
            """,
            (guild.id, normalized),
        ).fetchone()

        if not ach:
            return False, f"Achievement `{normalized}` does not exist."

        existing = conn.execute(
            """
            SELECT 1 FROM user_achievements
            WHERE guild_id = ? AND user_id = ? AND achievement_name = ?
            """,
            (guild.id, member.id, normalized),
        ).fetchone()
        if existing:
            return False, f"{member.mention} already has achievement **{ach['name']}**."

        conn.execute(
            """
            INSERT INTO user_achievements (guild_id, user_id, achievement_name, awarded_by)
            VALUES (?, ?, ?, ?)
            """,
            (guild.id, member.id, normalized, awarded_by),
        )
        if ach["reward_amount"] > 0:
            conn.execute(
                "UPDATE users SET wallet = wallet + ? WHERE guild_id = ? AND user_id = ?",
                (int(ach["reward_amount"]), guild.id, member.id),
            )
        conn.commit()

    role_result = await grant_role_if_configured(member, ach["role_id"])

    message = (
        f"Awarded **{ach['name']}** to {member.mention}.\n"
        f"Description: {ach['description']}\n"
        f"Coin reward: **{ach['reward_amount']}**"
    )
    if role_result:
        message += f"\n{role_result}"
    return True, message


async def check_auto_achievements(member: discord.Member) -> list[str]:
    wallet, bank = get_balance(member.guild.id, member.id)
    total = wallet + bank
    granted = []

    for achievement_name, threshold in AUTO_ACHIEVEMENTS.items():
        if total >= threshold and achievement_exists(member.guild.id, achievement_name):
            if not user_has_achievement(member.guild.id, member.id, achievement_name):
                ok, msg = await award_achievement(member.guild, member, achievement_name, None)
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
        if value >= int(config["target"]) and achievement_exists(member.guild.id, achievement_name):
            if not user_has_achievement(member.guild.id, member.id, achievement_name):
                ok, msg = await award_achievement(member.guild, member, achievement_name, None)
                if ok:
                    granted.append(msg)
    return granted


@bot.event
async def on_ready() -> None:
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} application commands.")
    except Exception as exc:
        print(f"Command sync failed: {exc}")
    print(f"Logged in as {bot.user}")


@bot.tree.command(name="create_achievement", description="Create a new achievement")
@app_commands.describe(
    name="Unique achievement name",
    description="What the achievement means",
    reward_amount="Coins awarded when earned",
    role="Optional role granted when earned",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def create_achievement(
    interaction: discord.Interaction,
    name: str,
    description: str,
    reward_amount: app_commands.Range[int, 0, 1_000_000] = 0,
    role: Optional[discord.Role] = None,
) -> None:
    normalized = name.lower().strip()
    with get_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM achievements WHERE guild_id = ? AND name = ?",
            (interaction.guild_id, normalized),
        ).fetchone()
        if existing:
            await interaction.response.send_message(
                f"Achievement `{normalized}` already exists.", ephemeral=True
            )
            return

        role_id = role.id if role else ACHIEVEMENT_ROLE_REWARDS.get(normalized)
        conn.execute(
            """
            INSERT INTO achievements (guild_id, name, description, reward_amount, role_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (interaction.guild_id, normalized, description, reward_amount, role_id),
        )
        conn.commit()

    extra = f"\nRole reward: {role.mention}" if role else ""
    await interaction.response.send_message(
        f"Created achievement **{normalized}**.\nReward: **{reward_amount}** coins{extra}"
    )


@bot.tree.command(name="list_achievements", description="List all achievements in this server")
async def list_achievements(interaction: discord.Interaction) -> None:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT name, description, reward_amount, role_id
            FROM achievements
            WHERE guild_id = ?
            ORDER BY name ASC
            """,
            (interaction.guild_id,),
        ).fetchall()

    if not rows:
        await interaction.response.send_message("No achievements have been created yet.")
        return

    embed = discord.Embed(title="Server Achievements")
    for row in rows[:25]:
        role_text = f" | Role: <@&{row['role_id']}>" if row["role_id"] else ""
        embed.add_field(
            name=row["name"],
            value=f"{row['description']}\nReward: {row['reward_amount']} coins{role_text}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="award_achievement", description="Award an achievement to a member")
@app_commands.checks.has_permissions(manage_roles=True)
@app_commands.describe(member="Member to award", achievement_name="Achievement name")
async def award_achievement_cmd(
    interaction: discord.Interaction,
    member: discord.Member,
    achievement_name: str,
) -> None:
    ok, msg = await award_achievement(
        interaction.guild,
        member,
        achievement_name,
        interaction.user.id,
    )
    await interaction.response.send_message(msg, ephemeral=not ok)


@bot.tree.command(name="my_achievements", description="View your achievements")
@app_commands.describe(member="Optional member to inspect")
async def my_achievements(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
) -> None:
    target = member or interaction.user
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT ua.achievement_name, a.description, a.reward_amount, ua.awarded_at
            FROM user_achievements ua
            JOIN achievements a
              ON a.guild_id = ua.guild_id AND a.name = ua.achievement_name
            WHERE ua.guild_id = ? AND ua.user_id = ?
            ORDER BY ua.awarded_at DESC
            """,
            (interaction.guild_id, target.id),
        ).fetchall()

    if not rows:
        await interaction.response.send_message(
            f"{target.mention} has no achievements yet."
        )
        return

    embed = discord.Embed(title=f"Achievements for {target.display_name}")
    for row in rows[:25]:
        embed.add_field(
            name=row["achievement_name"],
            value=f"{row['description']}\nReward: {row['reward_amount']} coins\nAwarded: {row['awarded_at']}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="balance", description="Check your economy balance")
@app_commands.describe(member="Optional member to inspect")
async def balance(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
) -> None:
    target = member or interaction.user
    wallet, bank = get_balance(interaction.guild_id, target.id)
    embed = discord.Embed(title=f"Balance for {target.display_name}")
    embed.add_field(name="Wallet", value=str(wallet))
    embed.add_field(name="Bank", value=str(bank))
    embed.add_field(name="Total", value=str(wallet + bank), inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="daily", description="Claim a daily reward")
async def daily(interaction: discord.Interaction) -> None:
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    available, remaining = check_cooldown(stats["daily_last_claim"], timedelta(hours=DAILY_COOLDOWN_HOURS))
    if not available:
        await interaction.response.send_message(
            f"Your daily reward is on cooldown. Try again in **{format_remaining(remaining)}**.",
            ephemeral=True,
        )
        return

    reward = random.randint(90, 150)
    add_wallet(interaction.guild_id, interaction.user.id, reward)
    update_user_fields(interaction.guild_id, interaction.user.id, daily_last_claim=utc_now().isoformat())
    auto_msgs = await check_auto_achievements(interaction.user)
    response = f"You claimed **{reward}** coins."
    if auto_msgs:
        response += "

New achievements unlocked:
" + "

".join(auto_msgs)
    await interaction.response.send_message(response)


@bot.tree.command(name="work", description="Earn some coins")
async def work(interaction: discord.Interaction) -> None:
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    available, remaining = check_cooldown(stats["work_last_claim"], timedelta(minutes=WORK_COOLDOWN_MINUTES))
    if not available:
        await interaction.response.send_message(
            f"Work is on cooldown. Try again in **{format_remaining(remaining)}**.",
            ephemeral=True,
        )
        return

    reward = random.randint(25, 90)
    add_wallet(interaction.guild_id, interaction.user.id, reward)
    update_user_fields(
        interaction.guild_id,
        interaction.user.id,
        work_last_claim=utc_now().isoformat(),
        work_count=int(stats["work_count"]) + 1,
    )
    auto_msgs = await check_auto_achievements(interaction.user)
    response = f"You worked and earned **{reward}** coins."
    if auto_msgs:
        response += "

New achievements unlocked:
" + "

".join(auto_msgs)
    await interaction.response.send_message(response)


@bot.tree.command(name="deposit", description="Deposit wallet coins into your bank")
@app_commands.describe(amount="How much to deposit")
async def deposit(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    if not move_money(interaction.guild_id, interaction.user.id, amount, to_bank=True):
        await interaction.response.send_message("Deposit failed. Check your wallet balance.", ephemeral=True)
        return
    await interaction.response.send_message(f"Deposited **{amount}** coins into your bank.")


@bot.tree.command(name="withdraw", description="Withdraw bank coins into your wallet")
@app_commands.describe(amount="How much to withdraw")
async def withdraw(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    if not move_money(interaction.guild_id, interaction.user.id, amount, to_bank=False):
        await interaction.response.send_message("Withdrawal failed. Check your bank balance.", ephemeral=True)
        return
    await interaction.response.send_message(f"Withdrew **{amount}** coins into your wallet.")


@bot.tree.command(name="pay", description="Pay another member")
@app_commands.describe(member="Member to pay", amount="How much to send")
async def pay(
    interaction: discord.Interaction,
    member: discord.Member,
    amount: app_commands.Range[int, 1, 1_000_000],
) -> None:
    if member.id == interaction.user.id:
        await interaction.response.send_message("You cannot pay yourself.", ephemeral=True)
        return

    wallet, _ = get_balance(interaction.guild_id, interaction.user.id)
    if wallet < amount:
        await interaction.response.send_message("You do not have enough wallet coins.", ephemeral=True)
        return

    add_wallet(interaction.guild_id, interaction.user.id, -amount)
    add_wallet(interaction.guild_id, member.id, amount)

    sender_auto = await check_auto_achievements(interaction.user)
    recipient_auto = await check_auto_achievements(member)

    response = f"Sent **{amount}** coins to {member.mention}."
    combined = sender_auto + recipient_auto
    if combined:
        response += "\n\nNew achievements unlocked:\n" + "\n\n".join(combined)
    await interaction.response.send_message(response)


@bot.tree.command(name="gamble", description="Bet coins for a chance to win")
@app_commands.describe(amount="How much to gamble")
async def gamble(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 1_000_000]) -> None:
    wallet, _ = get_balance(interaction.guild_id, interaction.user.id)
    if wallet < amount:
        await interaction.response.send_message("You do not have enough wallet coins.", ephemeral=True)
        return

    roll = random.random()
    if roll < 0.45:
        winnings = amount
        add_wallet(interaction.guild_id, interaction.user.id, winnings)
        stats = get_user_stats(interaction.guild_id, interaction.user.id)
        update_user_fields(interaction.guild_id, interaction.user.id, gamble_wins=int(stats["gamble_wins"]) + 1)
        response = f"You won **{winnings}** coins."
    elif roll < 0.50:
        jackpot = amount * 2
        add_wallet(interaction.guild_id, interaction.user.id, jackpot)
        stats = get_user_stats(interaction.guild_id, interaction.user.id)
        update_user_fields(interaction.guild_id, interaction.user.id, gamble_wins=int(stats["gamble_wins"]) + 1)
        response = f"Jackpot! You won **{jackpot}** coins."
    else:
        add_wallet(interaction.guild_id, interaction.user.id, -amount)
        stats = get_user_stats(interaction.guild_id, interaction.user.id)
        update_user_fields(interaction.guild_id, interaction.user.id, gamble_losses=int(stats["gamble_losses"]) + 1)
        response = f"You lost **{amount}** coins."

    auto_msgs = await check_auto_achievements(interaction.user)
    if auto_msgs:
        response += "

New achievements unlocked:
" + "

".join(auto_msgs)
    await interaction.response.send_message(response)


@bot.tree.command(name="shop", description="View the item shop")
async def shop(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="Shop")
    for item_name, item in SHOP_ITEMS.items():
        embed.add_field(
            name=item_name,
            value=f"{item['description']}
Buy: {item['price']} | Sell: {item['sell_price']}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="buy", description="Buy an item from the shop")
@app_commands.describe(item_name="Item to buy", quantity="How many to buy")
async def buy(
    interaction: discord.Interaction,
    item_name: str,
    quantity: app_commands.Range[int, 1, 100] = 1,
) -> None:
    item_key = item_name.lower().strip()
    item = SHOP_ITEMS.get(item_key)
    if not item:
        await interaction.response.send_message("That item does not exist in the shop.", ephemeral=True)
        return

    total_cost = int(item["price"]) * quantity
    wallet, _ = get_balance(interaction.guild_id, interaction.user.id)
    if wallet < total_cost:
        await interaction.response.send_message("You do not have enough wallet coins.", ephemeral=True)
        return

    add_wallet(interaction.guild_id, interaction.user.id, -total_cost)
    add_inventory_item(interaction.guild_id, interaction.user.id, item_key, quantity)
    stats = get_user_stats(interaction.guild_id, interaction.user.id)
    update_user_fields(interaction.guild_id, interaction.user.id, spent_total=int(stats["spent_total"]) + total_cost)

    auto_msgs = await check_auto_achievements(interaction.user)
    response = f"Bought **{quantity}x {item_key}** for **{total_cost}** coins."
    if auto_msgs:
        response += "

New achievements unlocked:
" + "

".join(auto_msgs)
    await interaction.response.send_message(response)


@bot.tree.command(name="sell", description="Sell an item from your inventory")
@app_commands.describe(item_name="Item to sell", quantity="How many to sell")
async def sell(
    interaction: discord.Interaction,
    item_name: str,
    quantity: app_commands.Range[int, 1, 100] = 1,
) -> None:
    item_key = item_name.lower().strip()
    item = SHOP_ITEMS.get(item_key)
    if not item:
        await interaction.response.send_message("That item cannot be sold here.", ephemeral=True)
        return

    inventory = {row['item_name']: int(row['quantity']) for row in get_inventory(interaction.guild_id, interaction.user.id)}
    owned = inventory.get(item_key, 0)
    if owned < quantity:
        await interaction.response.send_message("You do not own enough of that item.", ephemeral=True)
        return

    payout = int(item["sell_price"]) * quantity
    add_inventory_item(interaction.guild_id, interaction.user.id, item_key, -quantity)
    add_wallet(interaction.guild_id, interaction.user.id, payout)

    auto_msgs = await check_auto_achievements(interaction.user)
    response = f"Sold **{quantity}x {item_key}** for **{payout}** coins."
    if auto_msgs:
        response += "

New achievements unlocked:
" + "

".join(auto_msgs)
    await interaction.response.send_message(response)


@bot.tree.command(name="inventory", description="View your inventory")
@app_commands.describe(member="Optional member to inspect")
async def inventory(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    rows = get_inventory(interaction.guild_id, target.id)
    if not rows:
        await interaction.response.send_message(f"{target.mention} has no items.")
        return

    embed = discord.Embed(title=f"Inventory for {target.display_name}")
    for row in rows[:25]:
        embed.add_field(name=row['item_name'], value=f"Quantity: {row['quantity']}", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="progress", description="View achievement progress")
@app_commands.describe(member="Optional member to inspect")
async def progress(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
    target = member or interaction.user
    await check_auto_achievements(target)
    rows = get_progress_rows(interaction.guild_id, target.id)
    if not rows:
        await interaction.response.send_message("No tracked progress yet.")
        return

    embed = discord.Embed(title=f"Achievement Progress for {target.display_name}")
    for row in rows[:25]:
        config = PROGRESS_ACHIEVEMENTS.get(row['achievement_name'])
        if not config:
            continue
        current = int(row['progress_value'])
        target_value = int(config['target'])
        embed.add_field(
            name=row['achievement_name'],
            value=f"{config['description']}
Progress: {current}/{target_value}",
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leaderboard", description="View the richest members")
async def leaderboard(interaction: discord.Interaction) -> None:
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT user_id, wallet, bank, (wallet + bank) AS total
            FROM users
            WHERE guild_id = ?
            ORDER BY total DESC
            LIMIT 10
            """,
            (interaction.guild_id,),
        ).fetchall()

    if not rows:
        await interaction.response.send_message("No economy data yet.")
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        user = interaction.guild.get_member(int(row["user_id"]))
        name = user.mention if user else f"User {row['user_id']}"
        lines.append(f"**{i}.** {name} — {row['total']} coins")

    embed = discord.Embed(title="Economy Leaderboard", description="
".join(lines))
    await interaction.response.send_message(embed=embed)


@create_achievement.error
@award_achievement_cmd.error
async def admin_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    if isinstance(error, app_commands.errors.MissingPermissions):
        if interaction.response.is_done():
            await interaction.followup.send("You do not have permission to use this command.", ephemeral=True)
        else:
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    raise error


# -----------------------
# Minecraft SFX SYSTEM
# -----------------------

MC_SOUNDS = {
    "levelup": "sounds/levelup.ogg",
    "coin": "sounds/experience_orb.ogg",
    "villager": "sounds/villager_yes.ogg",
    "anvil": "sounds/anvil_use.ogg",
    "oof": "sounds/player_hurt.ogg",
}

@bot.tree.command(name="mcsfx", description="Play a Minecraft sound effect in your voice channel")
@app_commands.describe(sound="Sound name (levelup, coin, villager, anvil, oof)")
async def mcsfx(interaction: discord.Interaction, sound: str):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("You must be in a voice channel.", ephemeral=True)
        return

    sound_key = sound.lower().strip()
    file_path = MC_SOUNDS.get(sound_key)

    if not file_path:
        available = ", ".join(MC_SOUNDS.keys())
        await interaction.response.send_message(f"Unknown sound. Available: {available}", ephemeral=True)
        return

    vc = interaction.guild.voice_client

    if not vc:
        vc = await interaction.user.voice.channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await vc.move_to(interaction.user.voice.channel)

    if vc.is_playing():
        vc.stop()

    source = discord.FFmpegPCMAudio(file_path)
    vc.play(source)

    await interaction.response.send_message(f"Playing **{sound_key}** Minecraft sound.")


if __name__ == "__main__":
    init_db()
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Set your bot token in the DISCORD_TOKEN environment variable.")
    bot.run(TOKEN)
