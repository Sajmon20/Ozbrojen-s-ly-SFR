import discord
from discord.ext import commands, tasks
import sqlite3
import os
from datetime import datetime, timedelta
import time 

# =================================================================
# === KONFIGURACE BOTA (NAČTENÍ Z PROSTŘEDÍ RENDERU) ===
# =================================================================

# Načítání proměnných z prostředí (Render Env Vars)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
try:
    # Tyto ID musí být nastaveny v nastavení Renderu!
    BLACKLIST_ROLE_ID = int(os.environ.get('BLACKLIST_ROLE_ID'))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID'))
    MODERATOR_ROLE_ID = int(os.environ.get('MODERATOR_ROLE_ID'))
    
    # NOVÉ ID pro Activity Check (tyto jsou natvrdo v kódu)
    ACTIVITY_CHANNEL_ID = 1363606117355229184
    ACTIVITY_ROLE_ID = 1363605271846322296

except (TypeError, ValueError) as e:
    print(f"CHYBA: Zkontroluj, zda jsou ID role/kanálu správně nastaveny v proměnných prostředí Renderu! Chyba: {e}")
    exit()

# Inicializace Bota
intents = discord.Intents.default()
intents.members = True 
intents.message_content = True 
intents.reactions = True 

bot = commands.Bot(command_prefix='!', intents=intents)

# =================================================================
# === FUNKCE PRO DATABÁZI (SQLITE) ===
# =================================================================

def setup_db():
    """Vytvoří databázi a tabulky, pokud neexistují."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    
    # Tabulka pro Blacklist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            reason TEXT,
            blacklisted_by TEXT,
            timestamp TEXT
        )
    """)
    
    # Tabulka pro Activity Check (uložení čekajících kontrol)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_checks (
            message_id INTEGER PRIMARY KEY,
            guild_id INTEGER,
            start_timestamp INTEGER, 
            role_to_check_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

# --- Funkce Blacklist ---

def add_to_blacklist_db(user_id, username, reason, blacklisted_by):
    """Přidá uživatele do databáze."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute("""
        INSERT OR REPLACE INTO blacklist 
        (user_id, username, reason, blacklisted_by, timestamp) 
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, username, reason, blacklisted_by, timestamp))
    conn.commit()
    conn.close()

def remove_from_blacklist_db(user_id):
    """Odstraní uživatele z databáze."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_blacklisted(user_id):
    """Zkontroluje, zda je uživatel v databázi."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM blacklist WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

# --- Funkce Activity Check ---

def save_activity_check(message_id, guild_id, role_to_check_id):
    """Uloží informaci o nové kontrole do databáze."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    start_timestamp = int(time.time()) # Uloží aktuální čas v sekundách
    
    cursor.execute("""
        INSERT INTO activity_checks 
        (message_id, guild_id, start_timestamp, role_to_check_id) 
        VALUES (?, ?, ?, ?)
    """, (message_id, guild_id, start_timestamp, role_to_check_id))
    
    conn.commit()
    conn.close()

def get_overdue_checks():
    """Najde všechny kontroly, které jsou starší než 24 hodin."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    # Čas před 24 hodinami
    cutoff_time = int(time.time()) - (24 * 60 * 60)
    
    cursor.execute("SELECT message_id, guild_id, role_to_check_id FROM activity_checks WHERE start_timestamp < ?", (cutoff_time,))
    results = cursor.fetchall()
    conn.close()
    return results

def remove_activity_check(message_id):
    """Odstraní dokončenou kontrolu z databáze."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM activity_checks WHERE message_id = ?", (message_id,))
    conn.commit()
    conn.close()


# =================================================================
# === BOT TASKS A UDÁLOSTI ===
# =================================================================

@tasks.loop(hours=1)
async def check_activity_status():
    """Spouští se každou hodinu, kontroluje databázi a vyhodnocuje 24h staré kontroly."""
    overdue_checks = get_overdue_checks()
    
    if not overdue_checks:
        return

    for message_id, guild_id, role_to_check_id in overdue_checks:
        try:
            guild = bot.get_guild(guild_id)
            if not guild:
                continue

            channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
            if not channel:
                print(f"Chyba při vyhodnocení: Activity kanál {ACTIVITY_CHANNEL_ID} nenalezen.")
                remove_activity_check(message_id)
                continue
            
            message = await channel.fetch_message(message_id)

            # Získej uživatele, kteří zareagovali (✅)
            reacted_users = set()
            for reaction in message.reactions:
                if str(reaction.emoji) == '✅':
                    async for user in reaction.users():
                        if not user.bot:
                            reacted_users.add(user.id)
                    break
            
            # Získej všechny uživatele s danou rolí
            role_to_check = guild.get_role(role_to_check_id)
            if not role_to_check:
                print(f"Chyba při vyhodnocení: Activity role {role_to_check_id} nenalezena.")
                remove_activity_check(message_id)
                continue

            users_with_role = {member.id for member in role_to_check.members}
            
            # Najdi uživatele, kteří NEZAREAGOVALI
            non_reacting_users_ids = users_with_role - reacted_users
            
            # Sestav výsledek
            if non_reacting_users_ids:
                mention_list = [guild.get_member(uid).mention for uid in non_reacting_users_ids if guild.get_member(uid)]
                
                result_message = (
                    f"**Vyhodnocení ACTIVITY CHECKU (ID zprávy: {message_id}):**\n"
                    f"Tito uživatelé s rolí {role_to_check.mention} NEZAREAGOVALI na ✅ během 24 hodin:\n\n"
                    + "\n".join(mention_list)
                )
                
                await channel.send(result_message)
            else:
                await channel.send(f"**Vyhodnocení ACTIVITY CHECKU (ID zprávy: {message_id}):**\nVšichni uživatelé s rolí {role_to_check.mention} ZAREAGOVALI ✅. Skvělá práce!")

            # Odstraň kontrolu z DB
            remove_activity_check(message_id)

        except discord.NotFound:
            print(f"Chyba: Zpráva {message_id} nebyla nalezena (pravděpodobně smazána).")
            remove_activity_check(message_id)
        except Exception as e:
            print(f"Neočekávaná chyba při Activity Checku {message_id}: {e}")
            
@bot.event
async def on_ready():
    """Spustí se po připojení bota. Nastaví databázi a spustí Task Loop."""
    setup_db() 
    print(f'Bot je připojen jako {bot.user}')
    if not check_activity_status.is_running():
        check_activity_status.start()
        print('Task loop pro Activity Check spuštěn.')
    print('--------------------')

@bot.event
async def on_member_join(member):
    """Kontroluje, zda je nově připojený člen na blacklistu."""
    user_data = is_blacklisted(member.id)
    
    if user_data:
        reason = user_data[2] if user_data[2] else 'Není uveden'
        try:
            guild = member.guild
            blacklist_role = guild.get_role(BLACKLIST_ROLE_ID)
            
            if blacklist_role:
                await member.add_roles(blacklist_role)
                
                channel = guild.get_channel(LOG_CHANNEL_ID)
                if channel:
                    embed = discord.Embed(
                        title="🔴 Uživateli byla udělena Blacklist role (Návrat)",
                        description=f"Uživatel **{member.mention}** se *znovu připojil* na server a byla mu automaticky udělena Blacklist role.",
                        color=discord.Color.red()
                    )
                    embed.add_field(name="Důvod blacklistu", value=reason, inline=False)
                    embed.set_footer(text=f"ID uživatele: {member.id}")
                    await channel.send(embed=embed)
        except Exception as e:
            print(f"Nastala chyba při on_member_join: {e}")


# =================================================================
# === BOT PŘÍKAZY ===
# =================================================================

# --- 1. !blacklist (Přidání) ---

@commands.has_role(MODERATOR_ROLE_ID) 
@bot.command(name='blacklist', aliases=['blist'])
async def add_to_blacklist(ctx, member: discord.Member, *, reason: str = "Není uveden"):
    """Přidá uživatele na blacklist a udělí roli, pokud je online."""
    
    add_to_blacklist_db(member.id, member.name, reason, ctx.author.name)
    
    blacklist_role = ctx.guild.get_role(BLACKLIST_ROLE_ID)
    action_message = ""
    
    if blacklist_role:
        if member in ctx.guild.members:
            try:
                await member.add_roles(blacklist_role)
                action_message = f"Udělená Blacklist role uživateli **{member.name}**."
            except discord.Forbidden:
                action_message = "Chyba: Nemám oprávnění k udělení role. Uloženo do databáze."
        else:
            action_message = f"Uživatel **{member.name}** není na serveru. Uloženo do databáze. Role bude udělena při jeho návratu."
    else:
        action_message = f"Upozornění: Role s ID {BLACKLIST_ROLE_ID} nebyla nalezena! Uloženo do databáze."

    await ctx.send(f"✅ Uživatel **{member.name}** přidán na serverový blacklist. \n> *{action_message}*")

    channel = ctx.guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="🛑 Uživatel byl Blacklistován",
            description=f"Uživatel **{member.mention}** byl ručně přidán na Blacklist.",
            color=discord.Color.dark_red()
        )
        embed.add_field(name="Důvod", value=reason, inline=False)
        await channel.send(embed=embed)


# --- 2. !unblacklist (Odebrání) ---

@commands.has_role(MODERATOR_ROLE_ID) 
@bot.command(name='unblacklist', aliases=['unblist'])
async def remove_from_blacklist_command(ctx, member: discord.Member):
    """Odstraní uživatele z blacklistu a odebere mu Blacklist roli."""
    
    if not is_blacklisted(member.id):
        return await ctx.send(f"❌ Uživatel **{member.name}** není na blacklistu v databázi.")

    remove_from_blacklist_db(member.id)
    
    blacklist_role = ctx.guild.get_role(BLACKLIST_ROLE_ID)
    action_message = f"Uživatel **{member.name}** byl odebrán z databáze."
    
    if blacklist_role and member in ctx.guild.members:
        if blacklist_role in member.roles:
            try:
                await member.remove_roles(blacklist_role)
                action_message += "\nOdebrána Blacklist role."
            except discord.Forbidden:
                action_message += "\nChyba: Nemám oprávnění odebrat roli."

    await ctx.send(f"✅ Blacklist pro uživatele **{member.name}** zrušen. \n> *{action_message}*")
    
    channel = ctx.guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title="🟢 Blacklist ZRUŠEN",
            description=f"Uživatel **{member.mention}** byl odebrán z Blacklistu.",
            color=discord.Color.green()
        )
        embed.add_field(name="Moderátor", value=ctx.author.name, inline=True)
        await channel.send(embed=embed)


# --- 3. !activitycheck (Spuštění kontroly) ---

@commands.has_role(MODERATOR_ROLE_ID)
@bot.command(name='activitycheck', aliases=['ac'])
async def start_activity_check(ctx):
    """Spustí Activity Check v přednastaveném kanálu a nastaví 24h timer."""
    
    if not ctx.guild:
        return

    channel = ctx.guild.get_channel(ACTIVITY_CHANNEL_ID)
    role = ctx.guild.get_role(ACTIVITY_ROLE_ID)
    
    if not channel or not role:
        return await ctx.send("❌ Chyba konfigurace: Zkontroluj ID kanálu/role pro Activity Check. (Přednastavené ID jsou v kódu natvrdo)")

    message_content = (
        f"{role.mention}\n"
        f"**# ACTIVITY CHECK**\n\n"
        f"Zareagujte ✅"
    )
    
    try:
        sent_message = await channel.send(message_content)
        await sent_message.add_reaction('✅')
        
        save_activity_check(sent_message.id, ctx.guild.id, ACTIVITY_ROLE_ID)
        
        await ctx.send(f"✅ Activity Check spuštěn a naplánován k vyhodnocení za 24 hodin.")

    except discord.Forbidden:
        await ctx.send("❌ Nemám oprávnění posílat zprávy/reagovat v Activity kanálu.")


# --- Zpracování chyb pro příkazy ---

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ **Chyba syntaxe:** Chybí argument. Zkontroluj použití příkazu.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("❌ **Chyba:** Uživatel nebyl nalezen. Ujistěte se, že jste ho zmínili (@).")
    elif isinstance(error, commands.MissingRole):
        role = ctx.guild.get_role(MODERATOR_ROLE_ID)
        role_name = role.name if role else f"ID: {MODERATOR_ROLE_ID}"
        await ctx.send(f"❌ **Odmítnuto:** Pro použití tohoto příkazu musíš mít roli: **{role_name}**.")
    else:
        print(f"Neznámá chyba: {error}")
        
        
# --- Spuštění bota ---
if __name__ == '__main__':
    if not BOT_TOKEN:
        print("CHYBA: BOT_TOKEN není nastaven v proměnných prostředí Renderu!")
        exit()
    try:
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("\n\n!!! CHYBA PŘIHLÁŠENÍ !!!")
        print("Zkontroluj, zda je tvůj BOT_TOKEN správný a platný.")
