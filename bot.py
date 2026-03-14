import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
import aiohttp
import re
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
OCR_API_KEY = os.getenv("OCR_API_KEY")

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers ──────────────────────────────────────────────────────────────────

def contains_arabic(text: str) -> bool:
    return bool(re.search(r'[\u0600-\u06FF]', text))

def translate_arabic(text: str) -> dict:
    urdu    = GoogleTranslator(source='ar', target='ur').translate(text)
    english = GoogleTranslator(source='ar', target='en').translate(text)
    return {"urdu": urdu, "english": english}

async def extract_text_from_image(image_url: str) -> str:
    payload = {
        "url": image_url,
        "apikey": OCR_API_KEY,
        "language": "ara",
        "isOverlayRequired": False,
        "detectOrientation": True,
        "scale": True,
        "OCREngine": 2
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://api.ocr.space/parse/image", data=payload) as resp:
            data = await resp.json()
    try:
        return data["ParsedResults"][0]["ParsedText"].strip()
    except (KeyError, IndexError):
        return ""

def build_embed(original: str, translations: dict, source: str = "text") -> discord.Embed:
    embed = discord.Embed(title="🌐 Arabic Translation", color=0x00f3ff)
    embed.add_field(name="📝 Original Arabic", value=original[:1024] or "—", inline=False)
    embed.add_field(name="🇵🇰 Urdu",    value=translations["urdu"][:1024]    or "—", inline=False)
    embed.add_field(name="🇬🇧 English", value=translations["english"][:1024] or "—", inline=False)
    embed.set_footer(text=f"Source: {source} • Powered by Google Translate + OCR.space")
    return embed

# ── Events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready!")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if message.content and contains_arabic(message.content):
        try:
            translations = translate_arabic(message.content)
            embed = build_embed(message.content, translations, source="text")
            await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ Translation error: {e}")

    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                if not OCR_API_KEY:
                    await message.reply("⚠️ OCR API key not configured.")
                    continue

                await message.add_reaction("⏳")
                extracted = await extract_text_from_image(attachment.url)
                await message.remove_reaction("⏳", bot.user)

                if not extracted:
                    await message.reply("🖼️ No text found in image.")
                    continue

                if contains_arabic(extracted):
                    translations = translate_arabic(extracted)
                    embed = build_embed(extracted, translations, source="image (OCR.space)")
                    await message.reply(embed=embed)
                else:
                    await message.reply(f"🖼️ Text extracted but no Arabic found:\n```{extracted[:500]}```")
            except Exception as e:
                await message.reply(f"⚠️ Image processing error: {e}")

    await bot.process_commands(message)

# ── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="translate")
async def translate_command(ctx, *, text: str):
    if not contains_arabic(text):
        await ctx.reply("⚠️ Please provide Arabic text to translate.")
        return
    try:
        translations = translate_arabic(text)
        embed = build_embed(text, translations, source="!translate command")
        await ctx.reply(embed=embed)
    except Exception as e:
        await ctx.reply(f"⚠️ Translation error: {e}")

@bot.command(name="ping")
async def ping(ctx):
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

if __name__ == "__main__":
    bot.run(TOKEN)
