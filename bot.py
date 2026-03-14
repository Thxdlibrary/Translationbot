import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from deep_translator import GoogleTranslator
import pytesseract
from PIL import Image
import aiohttp
import io
import re
from flask import Flask
from threading import Thread

# ── Keep-alive server (prevents Render from sleeping) ────────────────────────
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive! 🤖"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

Thread(target=run_flask, daemon=True).start()

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# ── Bot setup ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ── Helpers ───────────────────────────────────────────────────────────────────

def contains_arabic(text: str) -> bool:
    """Return True if the string contains Arabic characters."""
    return bool(re.search(r'[\u0600-\u06FF]', text))

def translate_arabic(text: str) -> dict:
    """Translate Arabic text to both Urdu and English."""
    urdu   = GoogleTranslator(source='ar', target='ur').translate(text)
    english = GoogleTranslator(source='ar', target='en').translate(text)
    return {"urdu": urdu, "english": english}

async def extract_text_from_image(image_bytes: bytes) -> str:
    """Run OCR on image bytes and return extracted text."""
    image = Image.open(io.BytesIO(image_bytes))
    # Use Arabic + Urdu + English language data for Tesseract
    text = pytesseract.image_to_string(image, lang='ara+urd+eng')
    return text.strip()

def build_embed(original: str, translations: dict, source: str = "text") -> discord.Embed:
    """Build a nicely formatted Discord embed with translations."""
    embed = discord.Embed(
        title="🌐 Arabic Translation",
        color=0x00f3ff
    )
    embed.add_field(name="📝 Original Arabic", value=original[:1024] or "—", inline=False)
    embed.add_field(name="🇵🇰 Urdu",    value=translations["urdu"][:1024]    or "—", inline=False)
    embed.add_field(name="🇬🇧 English", value=translations["english"][:1024] or "—", inline=False)
    embed.set_footer(text=f"Source: {source} • Powered by Google Translate + Tesseract OCR")
    return embed

# ── Events ────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready and listening for Arabic text and images.")

@bot.event
async def on_message(message: discord.Message):
    # Ignore the bot's own messages
    if message.author.bot:
        return

    # ── 1. Check plain text for Arabic ───────────────────────────────────────
    if message.content and contains_arabic(message.content):
        try:
            translations = translate_arabic(message.content)
            embed = build_embed(message.content, translations, source="text")
            await message.reply(embed=embed)
        except Exception as e:
            await message.reply(f"⚠️ Translation error: {e}")

    # ── 2. Check attached images for Arabic text via OCR ─────────────────────
    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        image_bytes = await resp.read()

                extracted = await extract_text_from_image(image_bytes)

                if not extracted:
                    await message.reply("🖼️ I couldn't extract any text from that image.")
                    continue

                if contains_arabic(extracted):
                    translations = translate_arabic(extracted)
                    embed = build_embed(extracted, translations, source="image (OCR)")
                    await message.reply(embed=embed)
                else:
                    await message.reply(
                        f"🖼️ Image text extracted but no Arabic found:\n```{extracted[:500]}```"
                    )
            except Exception as e:
                await message.reply(f"⚠️ Image processing error: {e}")

    # Allow commands to still work
    await bot.process_commands(message)

# ── Commands ──────────────────────────────────────────────────────────────────

@bot.command(name="translate")
async def translate_command(ctx, *, text: str):
    """!translate <arabic text>  –  manually trigger a translation."""
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
    """Check if the bot is alive."""
    await ctx.reply(f"🏓 Pong! Latency: {round(bot.latency * 1000)}ms")

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)

pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
