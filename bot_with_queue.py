import discord
from discord.ext import commands
import yt_dlp
import asyncio
from urllib.parse import urlparse, quote_plus
import urllib.request
import re
import os

# env
from dotenv import load_dotenv
load_dotenv()

def uri_validator(x):
    try:
        result = urlparse(x)
        return all([result.scheme, result.netloc])
    except AttributeError:
        return False


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
bot.remove_command('help')

# Global queue and playback control
queue = []
is_playing = False
is_paused = False
current_timeout_task = None
current_video_title = None  # To store the currently playing video title

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')


# Server restriction
SERVER_ID = int(os.getenv('SERVER_ID'))

@bot.check
async def globally_allowed_guild_check(ctx):
    if ctx.guild is None:
        raise commands.CheckFailure("This bot cannot be used in DMs.")
    
    if ctx.guild.id != SERVER_ID:
        raise commands.CheckFailure(f"This bot can only be used in the specified server.")
    
    return True

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send(f"Command cannot be used: {error}")
    else:
        raise error  # Re-raise other errors

# Command to leave the voice channel
@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        queue.clear()  # Clear the queue when the bot leaves
        await ctx.send("Disconnected and cleared the queue.")
    else:
        await ctx.send("I'm not in a voice channel!")

# Command to add a song to the queue and play if not already playing
@bot.command(aliases=["p"])
async def play(ctx, *, query):
    global is_playing, is_paused

    voice_client = ctx.voice_client

    if not voice_client:
        if ctx.author.voice:
            channel = ctx.author.voice.channel
            voice_client = await channel.connect()
        else:
            await ctx.send("You're not in a voice channel!")
            return


    if not is_playing and not is_paused:
        if uri_validator(query):
            queue.append(query)
            await play_next(ctx)  # Start playing immediately
        else:
            url = search_video(query)
            if uri_validator(url):
                queue.append(url)
                await play_next(ctx)  # Start playing immediately
            else:
                await ctx.send("Could not find a video matching the query.")
    else:
        # If something is playing or paused, add to queue and notify the user
        if uri_validator(query):
            queue.append(url)
            await ctx.send(f"Added to queue: {url}")
        else:
            url = search_video(query)
            if uri_validator(url):
                queue.append(url)
                await ctx.send(f"Added to queue: {url}")
            else:
                await ctx.send("Could not find a video matching the query.")

def search_video(query):
    try:
        # Prepare the query
        # Encode the query string
        encoded_query = quote_plus(query)

        # Fetch the HTML page
        url = f"https://www.youtube.com/results?search_query={encoded_query}"
        with urllib.request.urlopen(url) as response:
            html = response.read().decode()
        
        # Extract video IDs from the HTML
        video_ids = re.findall(r"watch\?v=(\S{11})", html)
        
        # Check if video IDs were found
        if video_ids:
            return "https://www.youtube.com/watch?v=" + video_ids[0]
        else:
            return None
        
    except urllib.error.URLError as e:
        print(f"URL error: {e}")
        return None
    except urllib.error.HTTPError as e:
        print(f"HTTP error: {e}")
        return None
    except Exception as e:
        print(f"An error occurred: {e}")
        return None

# Command to skip the current song
@bot.command()
async def skip(ctx):
    voice_client = ctx.voice_client

    if voice_client and voice_client.is_playing():
        voice_client.stop()  # This will automatically trigger playing the next song
        await ctx.send("Skipped the current song.")
    else:
        await ctx.send("No audio is currently playing.")

# Play the next song in the queue
async def play_next(ctx):
    global is_playing, is_paused, current_timeout_task, current_video_title

    if len(queue) == 0:
        await ctx.send("Queue is empty, nothing to play.")
        is_playing = False
        return

    is_playing = True
    is_paused = False
    voice_client = ctx.voice_client
    if not voice_client:
        await ctx.send("Voice client is not connected.")
        return

    url = queue.pop(0)  # Get the first URL from the queue

    await ctx.send(f"Fetching video stream: {url}")

    # yt-dlp options to get audio stream URL
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,  # Extract metadata only
    }

    # Get the stream URL from YouTube
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)
        audio_url = info_dict['url']
        title = info_dict.get('title', 'Unknown Title')
        current_video_title = title  # Store the current video title

    # Use FFmpeg to stream the audio directly to Discord
    ffmpeg_options = {
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        'options': '-vn'
    }

    voice_client.play(discord.FFmpegPCMAudio(audio_url, **ffmpeg_options), after=lambda e: bot.loop.create_task(play_next(ctx)))

    await ctx.send(f"Now playing: {title}")

    # Start a 5-minute timeout to stop after inactivity
    if current_timeout_task:
        current_timeout_task.cancel()  # Cancel any existing timeout task
    current_timeout_task = bot.loop.create_task(timeout_for_song(ctx, title))

# Timeout function for 5-minute limit
async def timeout_for_song(ctx, title):
    await asyncio.sleep(5 * 60)  # Wait for 5 minutes (300 seconds)
    voice_client = ctx.voice_client

    if voice_client and voice_client.is_playing():
        voice_client.stop()
    await ctx.send(f"Playback timed out after 5 minutes and stopped: {title}")
    await ctx.send("Queue finished.")

# Command to pause the current song
@bot.command()
async def pause(ctx):
    global is_paused
    voice_client = ctx.voice_client

    if voice_client and voice_client.is_playing():
        voice_client.pause()
        is_paused = True
        await ctx.send("Paused the audio.")
    else:
        await ctx.send("No audio is currently playing.")

# Command to resume the paused song
@bot.command()
async def resume(ctx):
    global is_paused
    voice_client = ctx.voice_client

    if voice_client and is_paused:
        voice_client.resume()
        is_paused = False
        await ctx.send("Resumed the audio.")
    else:
        await ctx.send("The audio is not paused or no audio is playing.")

# Command to stop the current song
@bot.command()
async def stop(ctx):
    global is_playing, is_paused, current_video_title
    voice_client = ctx.voice_client

    if voice_client and voice_client.is_playing():
        voice_client.stop()
        is_playing = False
        is_paused = False
        current_video_title = None  # Clear current video title
        await ctx.send("Stopped the audio.")
    else:
        await ctx.send("No audio is currently playing.")

# Command to show the currently playing video
@bot.command(aliases=["np"])
async def now_playing(ctx):
    global current_video_title
    if current_video_title:
        await ctx.send(f"Now playing: {current_video_title}")
    else:
        await ctx.send("No audio is currently playing.")

# Command to show the current queue
@bot.command(aliases=["q"])
async def queue_list(ctx):
    if len(queue) == 0:
        await ctx.send("The queue is empty.")
    else:
        message = "Current queue:\n"
        for i, url in enumerate(queue, start=1):
            message += f"{i}. {url}\n"
        await ctx.send(message)

# Custom help command
@bot.command(aliases=["h"])
async def help(ctx):
    help_message = (
        "**Available Commands:**\n"
        "`!leave` - Leaves the voice channel and clears the queue.\n"
        "`!play <query>` or `!p <query>` - Adds a song to the queue and plays it if not already playing.\n"
        "`!skip` - Skips the current song.\n"
        "`!pause` - Pauses the current song.\n"
        "`!resume` - Resumes the paused song.\n"
        "`!stop` - Stops the current song.\n"
        "`!now_playing` or `!np` - Shows the currently playing song.\n"
        "`!queue` or `!q` - Shows the current queue.\n"
    )
    await ctx.send(help_message)

DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
# Run the bot with your token
bot.run(DISCORD_BOT_TOKEN)
