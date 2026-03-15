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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
MODEL = "openrouter/auto"

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers ──────────────────────────────────────────────────────────────────

def contains_arabic(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))

async def ask_llama(prompt: str, retries: int = 3) -> str:
    """Send request to OpenRouter for translation."""
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
                "content": "You are a professional Arabic translator. Translate Arabic text to Urdu and English accurately and completely."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "max_tokens": 4000
    }

    for attempt in range(retries):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
                    data = await resp.json()

            if "error" in data:
                error_msg = data["error"].get("message", "Unknown error")
                print(f"OpenRouter error (attempt {attempt+1}): {error_msg}")
                if "rate" in error_msg.lower():
                    await asyncio.sleep(10)
                    continue
                return f"ERROR: {error_msg}"

            return data["choices"][0]["message"]["content"]

        except Exception as e:
            print(f"Exception (attempt {attempt+1}): {e}")
            await asyncio.sleep(5)

    return "FAILED"

async def extract_arabic_from_image(image_bytes: bytes, mime_type: str) -> str:
    """Use Gemini ONLY to extract Arabic text from image."""
    image_b64 = base64.b64encode(image_bytes).decode('utf-8')

    prompt = """Extract all Arabic text from this image exactly as written.
Return ONLY the extracted Arabic text, nothing else.
If no Arabic text found, return: NONE"""

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_b64
                        }
                    },
                    {"text": prompt}
                ]
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            json=payload
        ) as resp:
            data = await resp.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError) as e:
        print(f"Gemini error: {e} | Response: {data}")
        return ""

async def translate_text(arabic_text: str) -> dict:
    """Translate Arabic text to Urdu and English using OpenRouter."""
    prompt = f"""Translate the COMPLETE Arabic text below to both Urdu and English.
Do not skip or truncate any part of the text.

Arabic text:
{arabic_text}

Respond in EXACTLY this format:
URDU: [complete urdu translation here]
ENGLISH: [complete english translation here]"""

    response = await ask_llama(prompt)
    return parse_response(response)

def parse_response(text: str) -> dict:
    """Parse translation response into structured dict."""
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

    if not result["urdu"] and not result["english"]:
        result["english"] = text[:500]
        result["urdu"] = "Could not parse response"

    return result

def add_long_field(embed: discord.Embed, name: str, value: str):
    """Add a field splitting into multiple if too long."""
    chunks = [value[i:i+1024] for i in range(0, len(value), 1024)]
    for i, chunk in enumerate(chunks):
        field_name = name if i == 0 else f"{name} (cont.)"
        embed.add_field(name=field_name, value=chunk, inline=False)

def build_embed(original: str, translations: dict, source: str = "text") -> discord.Embed:
    embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
    add_long_field(embed, "📝 Original Arabic", original or "—")
    add_long_field(embed, "🇵🇰 Urdu", translations["urdu"] or "—")
    add_long_field(embed, "🇬🇧 English", translations["english"] or "—")
    embed.set_footer(text=f"Source: {source} • Gemini OCR + OpenRouter Llama 🦙")
    return embed

# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌'} | Gemini: {'✅' if GEMINI_API_KEY else '❌'}")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # ── 1. Arabic text ────────────────────────────────────────────────────────
    if message.content and contains_arabic(message.content):
        try:
            async with message.channel.typing():
                translations = await translate_text(message.content)
            embed = build_embed(message.content, translations, source="text")
            await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ Error: {e}")

    # ── 2. Images → Gemini OCR → OpenRouter translation ───────────────────────
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                await message.add_reaction("⏳")

                # Download image
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        image_bytes = await resp.read()

                # Step 1 — Gemini extracts Arabic text from image
                extracted = await extract_arabic_from_image(image_bytes, attachment.content_type)
                print(f"Extracted text: {extracted[:100]}")

                if not extracted or extracted.upper() == "NONE":
                    await message.remove_reaction("⏳", bot.user)
                    await message.reply("🖼️ No Arabic text found in this image.")
                    continue

                # Step 2 — OpenRouter translates extracted text
                async with message.channel.typing():
                    translations = await translate_text(extracted)

                await message.remove_reaction("⏳", bot.user)
                embed = build_embed(extracted, translations, source="image (Gemini OCR + Llama)")
                await message.reply(embed=embed)

            except Exception as e:
                await message.remove_reaction("⏳", bot.user)
                await message.reply(f"⚠️ Image error: {e}")

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
        embed = build_embed(text, translations, source="!translate command")
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"⚠️ Error: {e}")

@bot.command(name="ping")
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    bot.run(TOKEN)
