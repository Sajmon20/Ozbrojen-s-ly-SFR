import discord
from discord.ext import commands
import sqlite3
import os
from datetime import datetime
import time 

# =================================================================
# === KONFIGURACE BOTA (NAČTENÍ Z PROSTŘEDÍ RENDERU) ===
# =================================================================

# Načítání proměnných z prostředí (Render Env Vars)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
try:
    # ID Z Renderu (Musí být správně zadaná!)
    BLACKLIST_ROLE_ID = int(os.environ.get('BLACKLIST_ROLE_ID'))
    LOG_CHANNEL_ID = int(os.environ.get('LOG_CHANNEL_ID'))
    MODERATOR_ROLE_ID = int(os.environ.get('MODERATOR_ROLE_ID'))
    
    # ID pro Activity Check (tyto jsou natvrdo v kódu, z tvého nastavení)
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
    """Vytvoří databázi a tabulky, pokud neexistují (blacklist a last_activity_check)."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    
    # 1. Tabulka pro Blacklist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            reason TEXT,
            blacklisted_by TEXT,
            timestamp TEXT
        )
    """)
    
    # 2. Tabulka pro poslední Activity Check (Uloží jen jednu zprávu pro ruční kontrolu)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS last_activity_check (
            id INTEGER PRIMARY KEY,
            message_id INTEGER,
            guild_id INTEGER,
            role_to_check_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

# --- Funkce Blacklist (Zůstávají stejné) ---

def add_to_blacklist_db(user_id, username, reason, blacklisted_by):
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
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_blacklisted(user_id):
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM blacklist WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

# --- Funkce Activity Check (Ruční) ---

def save_last_check(message_id, guild_id, role_to_check_id):
    """Uloží ID poslední zprávy s Activity Checkem."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    # Maže starý záznam a ukládá nový
    cursor.execute("DELETE FROM last_activity_check")
    cursor.execute("""
        INSERT INTO last_activity_check 
        (id, message_id, guild_id, role_to_check_id) 
        VALUES (1, ?, ?, ?)
    """, (message_id, guild_id, role_to_check_id))
    
    conn.commit()
    conn.close()

def get_last_check():
    """Načte ID poslední zprávy s Activity Checkem."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("SELECT message_id, guild_id, role_to_check_id FROM last_activity_check WHERE id = 1")
    result = cursor.fetchone()
    conn.close()
    return result

def delete_last_check():
    """Odstraní záznam po vyhodnocení."""
    conn = sqlite3.connect('blacklist.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM last_activity_check WHERE id = 1")
    conn.commit()
    conn.close()


# =================================================================
# === BOT UDÁLOSTI ===
# =================================================================

@bot.event
async def on_ready():
    """Spustí se po připojení bota. Nastaví databázi."""
    setup_db() 
    print(f'Bot je připojen jako {bot.user}')
    print('Databáze SQLite je připravena.')
    print('--------------------')

@bot.event
async def on_member_join(member):
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

# --- 1. !blacklist & !unblacklist (Zůstávají stejné) ---

@commands.has_role(MODERATOR_ROLE_ID) 
@bot.command(name='blacklist', aliases=['blist'])
async def add_to_blacklist(ctx, member: discord.Member, *, reason: str = "Není uveden"):
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


@commands.has_role(MODERATOR_ROLE_ID) 
@bot.command(name='unblacklist', aliases=['unblist'])
async def remove_from_blacklist_command(ctx, member: discord.Member):
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


# --- 2. !activitycheck (Spuštění kontroly) ---

@commands.has_role(MODERATOR_ROLE_ID)
@bot.command(name='activitycheck', aliases=['ac'])
async def start_activity_check(ctx):
    """Spustí Activity Check v přednastaveném kanálu a uloží ID zprávy."""
    
    if not ctx.guild:
        return

    channel = ctx.guild.get_channel(ACTIVITY_CHANNEL_ID)
    role = ctx.guild.get_role(ACTIVITY_ROLE_ID)
    
    if not channel or not role:
        return await ctx.send("❌ Chyba konfigurace: Zkontroluj ID kanálu/role pro Activity Check.")

    message_content = (
        f"{role.mention}\n"
        f"**# ACTIVITY CHECK**\n\n"
        f"Zareagujte ✅"
    )
    
    try:
        sent_message = await channel.send(message_content)
        await sent_message.add_reaction('✅')
        
        # Uložení pro RUČNÍ vyhodnocení
        save_last_check(sent_message.id, ctx.guild.id, ACTIVITY_ROLE_ID)
        
        await ctx.send(f"✅ Activity Check spuštěn. Vyhodnocení proveď pomocí **!vyhodnotitcheck**.")

    except discord.Forbidden:
        await ctx.send("❌ Nemám oprávnění posílat zprávy/reagovat v Activity kanálu.")

# --- 3. !vyhodnotitcheck (NOVÝ PŘÍKAZ) ---

@commands.has_role(MODERATOR_ROLE_ID)
@bot.command(name='vyhodnotitcheck', aliases=['checkac'])
async def evaluate_activity_check(ctx):
    """Ručně vyhodnotí poslední spuštěný Activity Check."""
    
    last_check_data = get_last_check()
    
    if not last_check_data:
        return await ctx.send("❌ Nebyl nalezen žádný aktivní Activity Check k vyhodnocení. Spusť jej pomocí `!activitycheck`.")
        
    message_id, guild_id, role_to_check_id = last_check_data
    
    await ctx.send(f"⌛ Zahajuji vyhodnocení Activity Checku se zprávou ID: `{message_id}`...")

    try:
        guild = bot.get_guild(guild_id)
        channel = guild.get_channel(ACTIVITY_CHANNEL_ID)
        
        if not channel:
            delete_last_check()
            return await ctx.send(f"❌ Chyba: Activity kanál {ACTIVITY_CHANNEL_ID} nenalezen. Kontrola zrušena.")
        
        message = await channel.fetch_message(message_id)

        # 1. Získej uživatele, kteří zareagovali (✅)
        reacted_users = set()
        for reaction in message.reactions:
            if str(reaction.emoji) == '✅':
                # Zde je kritické, aby bot měl intents.members=True a rights
                async for user in reaction.users():
                    if not user.bot:
                        reacted_users.add(user.id)
                break
        
        # 2. Získej všechny uživatele s danou rolí
        role_to_check = guild.get_role(role_to_check_id)
        if not role_to_check:
            delete_last_check()
            return await ctx.send(f"❌ Chyba: Activity role {role_to_check_id} nenalezena. Kontrola zrušena.")

        users_with_role = {member.id for member in role_to_check.members}
        
        # 3. Najdi uživatele, kteří NEZAREAGOVALI
        non_reacting_users_ids = users_with_role - reacted_users
        
        # 4. Sestav výsledek
        if non_reacting_users_ids:
            # Mapování ID zpět na mentiony (pouze pro ty, kteří jsou stále na serveru)
            mention_list = []
            for uid in non_reacting_users_ids:
                member = guild.get_member(uid)
                if member:
                    mention_list.append(member.mention)
            
            if mention_list:
                result_message = (
                    f"**Vyhodnocení Activity Checku (Manuální):**\n"
                    f"Tito uživatelé s rolí {role_to_check.mention} NEZAREAGOVALI na ✅:\n\n"
                    + "\n".join(mention_list)
                )
            else:
                result_message = "Všichni uživatelé s rolí zareagovali, nebo neaktivní uživatelé opustili server."
            
            await ctx.send(result_message)
        else:
            await ctx.send(f"**Vyhodnocení Activity Checku (Manuální):**\nVšichni uživatelé s rolí {role_to_check.mention} ZAREAGOVALI ✅. Skvělá práce!")

        # 5. Odstraň kontrolu z DB
        delete_last_check()
        await ctx.send("✅ Vyhodnocení dokončeno. Záznam kontroly byl vymazán z databáze.")


    except discord.NotFound:
        delete_last_check()
        await ctx.send("❌ Chyba: Původní zpráva Activity Checku nebyla nalezena (pravděpodobně smazána). Záznam byl vymazán.")
    except Exception as e:
        await ctx.send(f"❌ Nastala neočekávaná chyba při vyhodnocení: `{e}`")
        print(f"Neočekávaná chyba při Activity Checku: {e}")


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
