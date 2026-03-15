import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import aiohttp
import re
import base64
import asyncio
from flask import Flask
from threading import Thread

# ── Keep-alive server ────────────────────────────────────────────────────────
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive! 🤖"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run_flask, daemon=True).start()

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openrouter/auto"

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers ──────────────────────────────────────────────────────────────────

def contains_arabic(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))

async def ask_llama(prompt: str, retries: int = 3) -> str:
    """Send request to OpenRouter Llama 3.3 70B."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://discord-translation-bot.com",
        "X-Title": "Arabic Translation Bot"
    }

    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional Arabic translator. You translate Arabic text to Urdu and English accurately."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 2000
    }

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    OPENROUTER_URL,
                    headers=headers,
                    json=payload
                ) as resp:
                    data = await resp.json()

            print(f"OpenRouter response: {data}")

            # Check for errors
            if "error" in data:
                error_msg = data["error"].get("message", "Unknown error")
                print(f"Error (attempt {attempt+1}): {error_msg}")
                if "rate" in error_msg.lower():
                    await asyncio.sleep(10)
                    continue
                return f"ERROR: {error_msg}"

            return data["choices"][0]["message"]["content"]

        except Exception as e:
            print(f"Exception (attempt {attempt+1}): {e}")
            await asyncio.sleep(5)
            continue

    return "FAILED"

async def translate_text(arabic_text: str) -> dict:
    """Translate Arabic text to Urdu and English."""
    prompt = f"""Translate this Arabic text to both Urdu and English.

Arabic text: {arabic_text}

Respond in EXACTLY this format:
URDU: [urdu translation here]
ENGLISH: [english translation here]"""

    response = await ask_llama(prompt)
    return parse_response(response)

def parse_response(text: str) -> dict:
    """Parse response into structured dict."""
    result = {"urdu": "", "english": ""}

    if text in ["FAILED"] or text.startswith("ERROR:"):
        result["urdu"] = text
        result["english"] = text
        return result

    for line in text.strip().split('\n'):
        line = line.strip()
        if line.upper().startswith("URDU:"):
            result["urdu"] = line[5:].strip()
        elif line.upper().startswith("ENGLISH:"):
            result["english"] = line[8:].strip()

    # Fallback if parsing fails
    if not result["urdu"] and not result["english"]:
        result["english"] = text[:500]
        result["urdu"] = "Could not parse response"

    return result

def build_embed(original: str, translations: dict) -> discord.Embed:
    embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
    embed.add_field(name="📝 Original Arabic", value=original[:1024] or "—", inline=False)
    embed.add_field(name="🇵🇰 Urdu",    value=translations["urdu"][:1024]    or "—", inline=False)
    embed.add_field(name="🇬🇧 English", value=translations["english"][:1024] or "—", inline=False)
    embed.set_footer(text="Powered by Llama 3.3 70B via OpenRouter 🦙")
    return embed

# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"OpenRouter API Key: {'✅ Loaded' if OPENROUTER_API_KEY else '❌ MISSING'}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.content and contains_arabic(message.content):
        try:
            async with message.channel.typing():
                translations = await translate_text(message.content)
            embed = build_embed(message.content, translations)
            await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ Error: {e}")

    # Image handling — ask user to copy text for now
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            await message.reply(
                "🖼️ Image detected! Currently I can only translate text.\n"
                "Please **copy the Arabic text** from the image and send it as a message!"
            )

    await bot.process_commands(message)

# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="translate")
async def translate_command(ctx, *, text: str):
    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text.")
        return
    try:
        async with ctx.typing():
            translations = await translate_text(text)
        embed = build_embed(text, translations)
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"⚠️ Error: {e}")

@bot.command(name="ping")
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    bot.run(TOKEN)
