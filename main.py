import requests, datetime, discord, asyncio, sqlite3, random, string, emoji, math, os, re, io
from discord.ext import commands
from discord import app_commands
from collections import defaultdict, Counter
from PIL import Image

os.makedirs('dbs', exist_ok=True)
conn = sqlite3.connect('dbs/sacuma.db')
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS servers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    isUseMainFilter BOOLEAN DEFAULT TRUE,
    isUseChokiFilter BOOLEAN DEFAULT FALSE,
    isUseEmojiFilter BOOLEAN DEFAULT FALSE,
    isUseUrlFilter BOOLEAN DEFAULT FALSE,
    isUseCrashGifFilter BOOLEAN DEFAULT FALSE,
    emojiLimit INTEGER DEFAULT 5,
    timeoutDuration INTEGER DEFAULT 10
)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS members (
    userIdAndServerId TEXT PRIMARY KEY,
    timeoutCount INTEGER DEFAULT 0
)''')
conn.commit()

bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
if not os.path.isfile("TOKEN"):
    with open("TOKEN", encoding="utf-8", mode="w") as f:
        f.write(input("input token:"))
with open("TOKEN", encoding="utf-8") as f:
    TOKEN = f.read()

class Utils:
    def calcEntropy(s:str) -> int:
        length = len(s)
        if length == 0:
            return 0
        freq = Counter(s)
        probabilities = [freq[char] / length for char in freq]
        entropy = -sum(p * math.log2(p) for p in probabilities)
        return entropy
    def convertOrPassGifUrl(url:str) -> str:
        url = url.replace("i.imgur.com", "imgur.com")+".gif" if re.match(r"https://imgur\.com/([a-zA-Z0-9]+)$", url) else url
        return url
    def getUrls(s:str) -> list[str]:
        return re.findall("\b(?:https?:\/\/)?(?:www\.)?[^\s]+\b", s)
    def exclusionMemtion(s:str) -> str:
        return re.compile(r"<@!?(\d+)>|<@&(\d+)>").sub("", s)
    def containsEmoji(text):
        if re.search(r"<:\w+:\d+>", text):
            return True
        for char in text:
            if char in emoji.EMOJI_DATA:
                return True
        return False
    def containsStamp(message: discord.Message) -> bool:
        if re.search(r"<:.*?:\d+>", message.content):
            return True
        if message.stickers:
            return True
        return False

class Filter:
    def choki(message:discord.Message) -> bool:
        """
        脈絡がないテキストを判別する
        """
        return False, None
    def emoji(bot:commands.Bot, message:discord.Message, limit:int) -> bool:
        if not bot.emojis:
            return False, None
        emojiCount = sum(1 for char in message.content if char in discord.utils.get(bot.emojis))
        if emojiCount >= limit:
            return True, [message]
        return False, None
    def crashGif(message:discord.Message) -> bool:
        urls = Utils.getUrls(message.content)
        print(urls)
        for url in urls:
            url = Utils.convertOrPassGifUrl(url)
            if url[-4:] == ".gif":
                res = requests.get(url)
                if not str(res.status_code)[0] == "2":
                    continue
                try:
                    fileSize = int(res.headers["Content-Length"])
                    if fileSize > 5 * 1024 * 1024:
                        return True, [message]
                    img = Image.open(io.BytesIO(res.content))
                    if img.format != "GIF":
                        return False, None
                    frameCount = 0
                    while True:
                        try:
                            img.seek(frameCount)
                            frameCount += 1
                        except EOFError:
                            break
                    if frameCount > 100:
                        return True, [message]
                except:
                    continue
        return False, None
    def isSequentialUrl(messages:list[discord.Message]) -> bool:
        urls = []
        flagMessages = []
        for message in messages:
            url = Utils.getUrls(message.content)
            urls.append(Utils.getUrls(message.content)) if not len(url) == 0 else None
        length = len(urls)
        elementCount = Counter(urls)
        if elementCount.most_common(1) == []:
            return False, None
        mostCommonElement, mostCommonCount = elementCount.most_common(1)[0]
        isMajority = mostCommonCount >= (5 * length // 6)
        for message in messages:
            flagMessages.append(message) if mostCommonElement in message.content else None
        return isMajority, flagMessages
    def isSequentialMessage(messages:list[discord.Message], entropyThreshold:float=2.0) -> bool:
        contents = []
        flagMessages = []
        for message in messages:
            content:str = Utils.exclusionMemtion(message.content)
            content = "\n".join([line for line in content.split('\n') if Utils.calcEntropy(line) <= entropyThreshold])
            if not (content.strip() == "" or Utils.containsEmoji(content) or Utils.containsStamp(message)):
                contents.append(content)
        length = len(contents)
        elementCount = Counter(contents)
        mostCommonElement, mostCommonCount = elementCount.most_common(1)[0]
        isMajority = mostCommonCount >= (5 * length // 6)
        for message in messages:
            flagMessages.append(message) if mostCommonElement in message.content else None
        return isMajority, flagMessages
    def checkRapidMessages(messages: list[discord.Message], targetMember:discord.Member, timeWindowSeconds=60, messageThreshold=10) -> bool:
        if len(messages) < 15 or targetMember is None:
            return False
        targetMessages = [msg for msg in messages if msg.author == targetMember]
        if len(targetMessages) < messageThreshold:
            return False
        targetMessages.sort(key=lambda m: m.created_at)
        startTime = targetMessages[0].created_at
        endTime = startTime + datetime.timedelta(seconds=timeWindowSeconds)
        messageCount = 0
        for message in targetMessages:
            if message.created_at <= endTime:
                messageCount += 1
            else:
                while message.created_at > endTime:
                    startTime += datetime.timedelta(seconds=1)
                    endTime += datetime.timedelta(seconds=1)
                    messageCount -= 1
                messageCount += 1
            if messageCount >= messageThreshold:
                return True
        return False

class Sacuma(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot:commands = bot
        self.messages:dict = {}
    async def deleteChannelName(self, channel:discord.abc.GuildChannel, deleteName:str, id:str):
        if deleteName in channel.name:
            try:
                await channel.delete()
                self.deleteCount[id][0] += 1
            except:
                self.deleteCount[id][1] += 1
    def getServerSettings(self, id:int) -> tuple:
        global cursor
        cursor.execute("""
            SELECT * FROM servers
            WHERE id = ?
        """, (id,))
        return cursor.fetchone()
    def getUser(self, userIdAndServerId:str) -> tuple:
        global cursor
        cursor.execute("""
            SELECT * FROM members
            WHERE userIdAndServerId = ?
        """, (userIdAndServerId,))
        return cursor.fetchone()
    @app_commands.command(name="help", description="Show help")
    async def help(self, interaction: discord.Interaction):
        await interaction.response.send_message("""## Commands
```
/help: Show this message
/showsettings: Show now settings
/deletechannels channelName(string): Deletes all channels including the specified channel name.
/switchmainfilter: Switch Main filter
/switchchokifilter: Switch Choki Filter
/switchemojifilter: Switch Emoji Filter
/switchurlfilter: Switch Url Filter
/switchcrashgiffilter: Switch Crash GIF Filter
/changeemojilimit limit(integer): Change emoji filter limit
/changetimeoutduration timeoutDuration(integer minute): Change timeout duration(minimum 1)
```""", ephemeral=True)
    @app_commands.command(name="showsettings", description="Show now settings")
    async def showSettings(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            settings = self.getServerSettings(interaction.guild.id)
            tSettings = []
            for setting in settings:
                setting = True if setting else False if settings == False else setting
                tSettings.append(setting)
            await interaction.response.send_message(f"# Settings\n- Use main filter: {settings[1]}\n- Use choki filter: {settings[2]}\n- Use emoji filter: {settings[3]}\n- Use url filter: {settings[4]}\n- Use Crash GIF filter: {settings[5]}\n- Emoji limit: {settings[6]}\n- Timeout duration:{settings[7]}", ephemeral=True)
        except:
            await interaction.response.send_message("Error", ephemeral=True)
    @app_commands.command(name="deletechannel", description="Deletes all channels including the specified channel name.")
    async def deleteChannel(self, interaction: discord.Interaction, channelname:str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        id = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
        self.deleteCount[id] = [0,0]
        await asyncio.gather(*(self.deleteChannelName(channel, channelname, id) for channel in interaction.guild.channels))
        await interaction.response.send_message(f"```\n{self.deleteCount[id][0]} Success.\n{self.deleteCount[id][1]} Failed.\n```", ephemeral=True)
    @app_commands.command(name="switchmainfilter", description="Switch Main Filter")
    async def switchMainFilter(self, interaction: discord.Interaction):
        global cursor, conn
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            newValue = not self.getServerSettings(interaction.guild.id)[1]
            cursor.execute("UPDATE servers SET isUseMainFilter = ? WHERE id = ?", (newValue, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success switch", ephemeral=True)
        except:
            await interaction.response.send_message("Failed switch", ephemeral=True)
    @app_commands.command(name="switchchokifilter", description="Switch Choki Filter")
    async def switchChokiFilter(self, interaction: discord.Interaction):
        global cursor, conn
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            newValue = not self.getServerSettings(interaction.guild.id)[2]
            cursor.execute("UPDATE servers SET isUseChokiFilter = ? WHERE id = ?", (newValue, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success switch", ephemeral=True)
        except:
            await interaction.response.send_message("Failed switch", ephemeral=True)
    @app_commands.command(name="switchemojifilter", description="Switch Emoji Filter")
    async def switchEmojiFilter(self, interaction: discord.Interaction):
        global cursor, conn
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            newValue = not self.getServerSettings(interaction.guild.id)[3]
            cursor.execute("UPDATE servers SET isUseEmojiFilter = ? WHERE id = ?", (newValue, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success switch", ephemeral=True)
        except:
            await interaction.response.send_message("Failed switch", ephemeral=True)
    @app_commands.command(name="switchurlfilter", description="Switch URL Filter")
    async def switchUrlFilter(self, interaction: discord.Interaction):
        global cursor, conn
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            newValue = not self.getServerSettings(interaction.guild.id)[4]
            cursor.execute("UPDATE servers SET isUseUrlFilter = ? WHERE id = ?", (newValue, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success switch", ephemeral=True)
        except:
            await interaction.response.send_message("Failed switch", ephemeral=True)
    @app_commands.command(name="switchcrashgiffilter", description="Switch Crash GIF Filter")
    async def switchCrashGifFilter(self, interaction: discord.Interaction):
        global cursor, conn
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            newValue = not self.getServerSettings(interaction.guild.id)[5]
            cursor.execute("UPDATE servers SET isUseCrashGifFilter = ? WHERE id = ?", (newValue, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success switch", ephemeral=True)
        except:
            await interaction.response.send_message("Failed switch", ephemeral=True)
    @app_commands.command(name="changeemojilimit", description="Change Emoji limit")
    async def changeEmojiLimit(self, interaction: discord.Interaction, emojilimit:int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        try:
            newValue = not self.getServerSettings(interaction.guild.id)[5]
            cursor.execute("UPDATE servers SET emojiLimit = ? WHERE id = ?", (emojilimit, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success change", ephemeral=True)
        except:
            await interaction.response.send_message("Success change", ephemeral=True)
    @app_commands.command(name="changetimeoutduration", description="Change timeout duration(minimum 1)")
    async def changeTimeoutDuration(self, interaction: discord.Interaction, timeoutduration:int):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Error: You aren't Administrator", ephemeral=True)
            return
        elif timeoutduration < 1:
            await interaction.response.send_message("Error: duration minimum is 1", ephemeral=True)
            return
        try:
            cursor.execute("UPDATE servers SET timeoutDuration = ? WHERE id = ?", (timeoutduration, interaction.guild.id))
            conn.commit()
            await interaction.response.send_message("Success change", ephemeral=True)
        except:
            await interaction.response.send_message("Success change", ephemeral=True)
    @commands.Cog.listener()
    async def on_guild_join(self, guild:discord.Guild):
        global cursor, conn
        for channel in guild.text_channels:
            await channel.send(f"# Hi im Sacuma\nI can delete trolling Message/Channel.\nShow help: /help")
            break
        cursor.execute("INSERT OR IGNORE INTO servers (id) VALUES (?)", (guild.id,))
        conn.commit
    @commands.Cog.listener()
    async def on_message(self, message:discord.Message):
        global cursor, conn
        if message.author == self.bot.user:
            return
        elif not str(message.guild.id) in self.messages:
            self.messages[str(message.guild.id)] = []
            return
        flaged = False
        self.messages[str(message.guild.id)].append(message)
        tempMessages = self.messages[str(message.guild.id)]
        if len(tempMessages) > 5:
            tempMessages = tempMessages[-6:]
        else:
            return
        settings = self.getServerSettings(message.guild.id)
        if not self.getUser(f"{message.guild.id}-{message.author.id}"):
            cursor.execute("INSERT OR IGNORE INTO members (userIdAndServerId) VALUES (?)", (f"{message.guild.id}-{message.author.id}",))
            conn.commit()
        if Filter.checkRapidMessages(self.messages[str(message.guild.id)], message.author):
            try:
                print("aa")
                await message.author.timeout(datetime.datetime.now().astimezone() + datetime.timedelta(minutes=settings[7]))
                cursor.execute("UPDATE members SET timeoutCount = timeoutCount + 1 WHERE userIdAndServerId = ?", (f"{message.author.id}-{message.author.id}",))
                conn.commit()
            except Exception as e:
                print(e)
        checkResults = [
            Filter.isSequentialMessage(tempMessages),
            Filter.choki(message),
            Filter.emoji(self.bot, message, settings[6]),
            Filter.isSequentialUrl(tempMessages),
            Filter.crashGif(message)
        ]
        flagMessagesFinal = []
        timeoutedMembers = []
        for i in range(len(checkResults)):
            if not settings[i+1]:
                continue
            elif type(checkResults[i]) == list:
                flag, flagMessages = True if checkResults[i] != [False, False] else False
            else:
                flag, flagMessages = checkResults[i]
            if flag:
                flagMessagesFinal += flagMessages
                flaged = True
        if flaged:
            for message in flagMessagesFinal:
                member = message.author
                if member in timeoutedMembers:
                    continue
                try:
                    await member.timeout(datetime.datetime.now() + datetime.timedelta(min=settings[7]))
                except:
                    continue
                cursor.execute("UPDATE members SET timeoutCount = timeoutCount + 1 WHERE userIdAndServerId = ?", (f"{member.id}-{member.id}",))
                conn.commit()

@bot.event
async def on_ready():
    await bot.tree.sync()
asyncio.run(bot.add_cog(Sacuma(bot)))
bot.run(TOKEN) if __name__ == "__main__" else quit()