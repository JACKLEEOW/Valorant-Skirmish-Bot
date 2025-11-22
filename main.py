import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import random

load_dotenv()

# --- CONFIGURATION ---
GUILD_ID = 1441629762543026260 # paste your own id here if you are using the bot for a personal server!!

# --- DATA STRUCTURES ---
panel_queues = {} 
player_status = {} 
#originally had a huge global queue, but had to change to allow multiple instances of panels

class Bot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        print("Bot is ready!")

bot = Bot()

# --- HELPER FUNCTIONS ---

def get_queue_embed(message_id):
    if message_id not in panel_queues:
        return discord.Embed(title="Session Expired", description="Please run /setup again.")

    data = panel_queues[message_id]

    embed = discord.Embed(
        title="⚔️ Valorant Skirmish Hub", 
        description="Click a button to join a queue.", 
        color=discord.Color.from_rgb(255, 70, 85)
    )
    
    for mode in ["1v1", "2v2", "3v3"]:
        players = data.get(mode, [])
        player_list = "\n".join([f"> 👤 {p.display_name}" for p in players]) if players else "*Waiting for players...*"
        embed.add_field(name=f"**{mode} Queue** ({len(players)})", value=player_list, inline=False)

    active_matches = data.get("matches", {})
    if active_matches:
        match_text = ""
        for m_id, m_data in active_matches.items():
            blue_team = ", ".join([p.display_name for p in m_data['blue']])
            red_team = ", ".join([p.display_name for p in m_data['red']])
            match_text += f"**#{m_id}**: 🔵 `{blue_team}` **VS** 🔴 `{red_team}`\n"
        
        embed.add_field(name="🔥 Matches in Progress", value=match_text, inline=False)

    embed.set_footer(text=f"Instance ID: {message_id}")
    return embed

async def refresh_panel(message_id):
    if message_id not in panel_queues: return

    channel_id = panel_queues[message_id]["channel_id"]
    try:
        channel = bot.get_channel(channel_id)
        if channel:
            msg = channel.get_partial_message(message_id)
            await msg.edit(embed=get_queue_embed(message_id), view=QueueView())
    except Exception as e:
        print(f"Failed to refresh panel {message_id}: {e}")

# --- VIEWS ---

class QueueView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def handle_join(self, interaction, mode):
        user = interaction.user
        msg_id = interaction.message.id

        if user.id in player_status:
            status = player_status[user.id]
            if status.startswith("MATCH"):
                await interaction.response.send_message(f"❌ You are already playing in a match!", ephemeral=True)
            elif status != f"QUEUE:{msg_id}":
                await interaction.response.send_message("❌ You are queued in a different lobby! Leave that one first.", ephemeral=True)
            else:
                 await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        if msg_id not in panel_queues:
            panel_queues[msg_id] = {"channel_id": interaction.channel_id, "1v1": [], "2v2": [], "3v3": [], "matches": {}}

        if user in panel_queues[msg_id][mode]:
            await interaction.response.send_message("You are already in this queue.", ephemeral=True)
            return

        for q_mode in ["1v1", "2v2", "3v3"]:
            if user in panel_queues[msg_id][q_mode]:
                panel_queues[msg_id][q_mode].remove(user)

        panel_queues[msg_id][mode].append(user)
        player_status[user.id] = f"QUEUE:{msg_id}"
        
        required = {"1v1": 2, "2v2": 4, "3v3": 6}
        
        if len(panel_queues[msg_id][mode]) >= required[mode]:
            players = panel_queues[msg_id][mode][:required[mode]]
            panel_queues[msg_id][mode] = panel_queues[msg_id][mode][required[mode]:] 
            
            await interaction.response.edit_message(embed=get_queue_embed(msg_id), view=self)
            await start_lobby_process(interaction.guild, players, mode, msg_id)
        else:
            await interaction.response.edit_message(embed=get_queue_embed(msg_id), view=self)

    async def handle_leave(self, interaction):
        user = interaction.user
        msg_id = interaction.message.id
        
        if msg_id not in panel_queues:
            await interaction.response.send_message("Instance not found.", ephemeral=True)
            return

        removed = False
        for mode in ["1v1", "2v2", "3v3"]:
            if user in panel_queues[msg_id][mode]:
                panel_queues[msg_id][mode].remove(user)
                removed = True
        
        if removed:
            if user.id in player_status: del player_status[user.id]
            await interaction.response.edit_message(embed=get_queue_embed(msg_id), view=self)
        else:
            await interaction.response.send_message("You are not in this queue.", ephemeral=True)

    @discord.ui.button(label="Join 1v1", style=discord.ButtonStyle.primary, custom_id="q_1v1")
    async def join_1v1(self, interaction, button):
        await self.handle_join(interaction, "1v1")

    @discord.ui.button(label="Join 2v2", style=discord.ButtonStyle.success, custom_id="q_2v2")
    async def join_2v2(self, interaction, button):
        await self.handle_join(interaction, "2v2")

    @discord.ui.button(label="Join 3v3", style=discord.ButtonStyle.secondary, custom_id="q_3v3")
    async def join_3v3(self, interaction, button):
        await self.handle_join(interaction, "3v3")
    
    @discord.ui.button(label="Leave Queue", style=discord.ButtonStyle.danger, custom_id="q_leave")
    async def leave_queue(self, interaction, button):
        await self.handle_leave(interaction)

# --- MATCHMAKING ---

async def start_lobby_process(guild, players, mode, origin_msg_id):
    channel = guild.system_channel or guild.text_channels[0]
    await channel.send(f"🚨 **MATCH FOUND** for {mode}! Prepared: {', '.join([p.mention for p in players])}")

    if mode == "1v1":
        random.shuffle(players)
        await setup_match_channels(guild, [players[0]], [players[1]], mode, origin_msg_id)
    else:
        await start_draft(guild, players, origin_msg_id)

# --- DRAFT ---

class DraftSelect(discord.ui.Select):
    def __init__(self, players, current_captain):
        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in players]
        super().__init__(placeholder="Pick a player...", options=options)
        self.current_captain = current_captain

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self.current_captain:
            await interaction.response.send_message("Not your turn!", ephemeral=True)
            return
        picked_id = int(self.values[0])
        await self.view.handle_pick(interaction, picked_id)

class DraftView(discord.ui.View):
    def __init__(self, captain_a, captain_b, pool, origin_msg_id):
        super().__init__(timeout=600)
        self.captain_a = captain_a
        self.captain_b = captain_b
        self.pool = pool
        self.team_a = [captain_a]
        self.team_b = [captain_b]
        self.turn = "A" 
        self.origin_msg_id = origin_msg_id
        self.update_components()

    def update_components(self):
        self.clear_items()
        if self.pool:
            active_cap = self.captain_a if self.turn == "A" else self.captain_b
            self.add_item(DraftSelect(self.pool, active_cap))

    async def handle_pick(self, interaction, picked_id):
        picked_player = next(p for p in self.pool if p.id == picked_id)
        self.pool.remove(picked_player)
        
        if self.turn == "A":
            self.team_a.append(picked_player)
            self.turn = "B"
        else:
            self.team_b.append(picked_player)
            self.turn = "A"
        
        if len(self.pool) == 1:
            last_player = self.pool.pop()
            if self.turn == "A": self.team_a.append(last_player)
            else: self.team_b.append(last_player)

        embed = interaction.message.embeds[0]
        if not self.pool:
            embed.title = "Draft Complete!"
            embed.color = discord.Color.green()
            embed.description = self.get_roster_text()
            await interaction.response.edit_message(embed=embed, view=None)
            await setup_match_channels(interaction.guild, self.team_a, self.team_b, "Ranked", self.origin_msg_id)
        else:
            self.update_components()
            embed.description = self.get_roster_text()
            await interaction.response.edit_message(embed=embed, view=self)

    def get_roster_text(self):
        return (f"**🔵 Team Blue (A):** {', '.join([p.display_name for p in self.team_a])}\n"
                f"**🔴 Team Red (B):** {', '.join([p.display_name for p in self.team_b])}\n\n"
                f"**Available:** {', '.join([p.display_name for p in self.pool])}")

async def start_draft(guild, players, origin_msg_id):
    random.shuffle(players)
    cap_a = players.pop(0)
    cap_b = players.pop(0)
    
    for p in players + [cap_a, cap_b]:
        player_status[p.id] = f"MATCH:{origin_msg_id}" 

    embed = discord.Embed(title="👨‍✈️ Captain Draft", description="Blue Team Captain picks first.", color=discord.Color.gold())
    embed.add_field(name="Blue Captain", value=cap_a.mention)
    embed.add_field(name="Red Captain", value=cap_b.mention)
    view = DraftView(cap_a, cap_b, players, origin_msg_id)
    view.embed_description = view.get_roster_text() 
    channel = guild.system_channel or guild.text_channels[0]
    await channel.send(embed=embed, view=view)

# --- RESULTS & CLEANUP ---

class MatchResultView(discord.ui.View):
    def __init__(self, team_a, team_b, channel, category, match_id, origin_msg_id):
        super().__init__(timeout=None)
        self.team_a_ids = [m.id for m in team_a] 
        self.team_b_ids = [m.id for m in team_b]
        self.cap_a_name = team_a[0].display_name
        self.cap_b_name = team_b[0].display_name
        self.channel = channel
        self.category = category
        self.match_id = match_id
        self.origin_msg_id = origin_msg_id
        self.votes = {}

    async def handle_vote(self, interaction, winner):
        if interaction.user.id not in self.team_a_ids + self.team_b_ids:
            await interaction.response.send_message("You are not in this match.", ephemeral=True)
            return
        
        self.votes[interaction.user.id] = winner
        await interaction.response.send_message(f"You voted for Team {winner}", ephemeral=True)
        
        votes_a = [v for k,v in self.votes.items() if k in self.team_a_ids]
        votes_b = [v for k,v in self.votes.items() if k in self.team_b_ids]
        
        # Check if both teams have cast at least one vote
        if votes_a and votes_b:
            # DISPUTE LOGIC: They disagree
            if votes_a[0] != votes_b[0]:
                await self.end_match("DISPUTE")
            # CONSENSUS LOGIC: They agree
            elif votes_a[0] == votes_b[0]: 
                await self.end_match(votes_a[0])

    @discord.ui.button(label="Blue Team Won", style=discord.ButtonStyle.primary)
    async def blue_win(self, interaction, button):
        await self.handle_vote(interaction, "Blue")

    @discord.ui.button(label="Red Team Won", style=discord.ButtonStyle.danger)
    async def red_win(self, interaction, button):
        await self.handle_vote(interaction, "Red")

    async def end_match(self, winner_team):
        self.stop() # Disable buttons
        
        # 1. ANNOUNCEMENT LOGIC
        if self.origin_msg_id in panel_queues:
            # Remove match from active list
            if self.match_id in panel_queues[self.origin_msg_id]["matches"]:
                del panel_queues[self.origin_msg_id]["matches"][self.match_id]

            # Logic for Public Announcement
            channel_id = panel_queues[self.origin_msg_id]["channel_id"]
            public_channel = bot.get_channel(channel_id)
            
            if winner_team == "DISPUTE":
                # Warn the lobby only
                await self.channel.send("⚠️ **There is a dispute in votes.** Please alert an admin if issues persist.\nChannels closing...")
                # No public announcement
            else:
                # Publicly announce the winner
                winning_captain = self.cap_a_name if winner_team == "Blue" else self.cap_b_name
                if public_channel:
                    await public_channel.send(f"🏆 **Winner of Match: ⚔️ {self.match_id} is Team 👑 {winning_captain}!**")
                
                # Confirm closure in lobby
                await self.channel.send(f"✅ Match Verified! Deleting channels in 10 seconds...")

        # 2. Free Players
        for uid in self.team_a_ids + self.team_b_ids:
            if uid in player_status: del player_status[uid]

        # 3. Refresh Dashboard
        await refresh_panel(self.origin_msg_id)

        # 4. Cleanup Channels
        await asyncio.sleep(10)
        for channel in self.category.channels: 
            try:
                await channel.delete()
            except:
                pass # Ignore errors if already deleted
        try:
            await self.category.delete()
        except:
            pass

async def setup_match_channels(guild, team_a, team_b, mode, origin_msg_id):
    match_id = random.randint(1000, 9999)
    
    if origin_msg_id in panel_queues:
        panel_queues[origin_msg_id]["matches"][match_id] = {'blue': team_a, 'red': team_b}
    
    for p in team_a + team_b: player_status[p.id] = f"MATCH:{origin_msg_id}"
    
    await refresh_panel(origin_msg_id)

    overwrites_a = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    overwrites_b = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    for m in team_a: overwrites_a[m] = discord.PermissionOverwrite(view_channel=True, connect=True)
    for m in team_b: overwrites_b[m] = discord.PermissionOverwrite(view_channel=True, connect=True)

    category = await guild.create_category(f"Match #{match_id}")
    await guild.create_voice_channel("Team Blue", category=category, overwrites=overwrites_a)
    await guild.create_voice_channel("Team Red", category=category, overwrites=overwrites_b)
    shared_perms = {**overwrites_a, **overwrites_b}
    text_chan = await guild.create_text_channel("lobby-chat", category=category, overwrites=shared_perms)
    
    blue_list = "\n".join([f"- {p.mention}" for p in team_a])
    red_list = "\n".join([f"- {p.mention}" for p in team_b])

    embed = discord.Embed(title=f"Match #{match_id} - Lobby Info", description=f"**Host:** {team_a[0].mention}\nCreate the Custom Game and paste invite here.", color=discord.Color.gold())
    embed.add_field(name="🔵 Team Blue", value=blue_list, inline=True)
    embed.add_field(name="🔴 Team Red", value=red_list, inline=True)
    
    view = MatchResultView(team_a, team_b, text_chan, category, match_id, origin_msg_id)
    await text_chan.send(embed=embed, view=view)

# --- COMMANDS ---

@bot.tree.command(name="setup", description="Spawns the queue interface")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    await interaction.response.send_message("Creating queue instance...", ephemeral=True)
    
    msg = await interaction.channel.send(embed=discord.Embed(title="Initializing..."), view=QueueView())
    
    panel_queues[msg.id] = {
        "channel_id": interaction.channel_id, 
        "1v1": [], "2v2": [], "3v3": [], 
        "matches": {}
    }
    
    await msg.edit(embed=get_queue_embed(msg.id))

# RUN
if os.getenv("DISCORD_TOKEN"):
    bot.run(os.getenv("DISCORD_TOKEN"))
else:
    print("ERROR: DISCORD_TOKEN not found in .env file")