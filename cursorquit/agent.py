import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    cli,
    llm,
    metrics,
)
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import deepgram, openai, silero, elevenlabs
from livekit.plugins.elevenlabs import Voice

# Load environment variables from .env.local file
load_dotenv(dotenv_path=".env.local")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("restaurant_assistant.log"),
    ],
)
logger = logging.getLogger("portuguese-restaurant-assistant")

# Sandbox information (unused but kept for reference)
SANDBOX_ID = "contextual-node-16jzjr"

# Sample menu in Portuguese
MENU = """Nosso menu de hoje inclui: 1. Bacalhau à Brás, 2. Caldo Verde, 3. Francesinha."""

# System prompt for the assistant
SYSTEM_PROMPT = """Você é um assistente de voz para um restaurante português. Responda em português de forma amigável e natural.
Quando um cliente mencionar "reservar" ou pedir uma reserva, pergunte a data, hora e número de pessoas.
Se ele perguntar sobre o menu, informe os pratos disponíveis.
Mantenha as respostas concisas e naturais, como em uma conversa telefônica real."""

def prewarm(proc):
    """Preload models for faster startup"""
    try:
        logger.info("Preloading VAD model...")
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("VAD model preloaded successfully")
    except Exception as e:
        logger.error(f"Failed to preload VAD model: {e}")

async def entrypoint(ctx: JobContext):
    """Main entrypoint for the agent"""
    logger.info(f"Starting Portuguese restaurant voice assistant for room {ctx.room.name}")

    # Create chat context with system prompt
    logger.info("Setting up assistant system prompt")
    initial_ctx = llm.ChatContext().append(
        role="system",
        text=SYSTEM_PROMPT,
    )

    # Connect to the room
    logger.info("Connecting to LiveKit room...")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Wait for a participant to join
    logger.info("Waiting for a participant to join...")
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant joined: {participant.identity}")

    # Check if we have the required API keys
    deepgram_key = os.environ.get("DEEPGRAM_API_KEY", "")
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY", "")

    if not deepgram_key or deepgram_key.startswith(("YOUR_", "REPLACE_")):
        logger.warning("Deepgram API key is missing or invalid")

    try:
        # Create the voice assistant
        logger.info("Initializing voice assistant...")

        # Configure TTS with ElevenLabs
        if elevenlabs_key and not elevenlabs_key.startswith(("YOUR_", "REPLACE_")):
            logger.info("Using ElevenLabs TTS with voice 'William'")
            tts = elevenlabs.TTS(
                voice=Voice(id="sxmZDLpkJIwFRPyQ57rY", name="William", category="premade"),  # William voice ID
                api_key=elevenlabs_key
            )
        else:
            logger.info("Using OpenAI TTS (fallback)")
            tts = openai.TTS()

        # Configure STT with Deepgram
        stt = deepgram.STT(
            language="pt",
            model="nova-2",
        )

        # Use the preloaded VAD model if available
        vad = ctx.proc.userdata.get("vad")

        # Create the voice assistant pipeline
        assistant = VoicePipelineAgent(
            vad=vad,
            stt=stt,
            llm=openai.LLM(),
            tts=tts,
            chat_ctx=initial_ctx,
        )

        # Error handling
        @assistant.on("error")
        def on_error(error):
            logger.error(f"Error in voice assistant: {error}")
            if "deepgram" in str(error).lower():
                logger.error("This appears to be a Deepgram API issue. Check your API key.")
                asyncio.create_task(
                    assistant.say(
                        "Desculpe, estamos tendo problemas com nosso sistema de reconhecimento de voz."
                    )
                )

        # User speech events
        @assistant.on("user_speech_started")
        def on_user_speech_started():
            logger.info("User started speaking")

        @assistant.on("user_speech_ended")
        def on_user_speech_ended():
            logger.info("User stopped speaking")
            
        @assistant.on("user_speech_committed")
        def on_user_speech_committed(msg):
            if hasattr(msg, 'content'):
                logger.info(f"User speech committed: {msg.content}")

        # Agent speech events
        @assistant.on("agent_speech_started")
        def on_agent_speech_started():
            logger.info("Agent started speaking")

        @assistant.on("agent_speech_ended")
        def on_agent_speech_ended():
            logger.info("Agent stopped speaking")
            
        @assistant.on("agent_speech_committed")
        def on_agent_speech_committed(msg):
            if hasattr(msg, 'content'):
                logger.info(f"Agent speech committed: {msg.content}")

        # DTMF handler
        @assistant.on("dtmf_received")
        def on_dtmf_received(digits):
            logger.info(f"DTMF digits received: {digits}")
            if digits == "1":
                asyncio.create_task(
                    assistant.say(
                        "Você selecionou fazer uma reserva. Por favor, diga a data, hora e número de pessoas."
                    )
                )
            elif digits == "2":
                asyncio.create_task(assistant.say(f"Aqui está o nosso menu: {MENU}"))
            elif digits == "3":
                asyncio.create_task(
                    assistant.say("Transferindo para um atendente. Por favor, aguarde.")
                )
            elif digits == "0":
                asyncio.create_task(
                    assistant.say("Obrigado por ligar para o Restaurante Português. Até logo!")
                )

        # Track usage metrics
        usage_collector = metrics.UsageCollector()
        
        @assistant.on("metrics_collected")
        def on_metrics_collected(mtrcs):
            metrics.log_metrics(mtrcs)
            usage_collector.collect(mtrcs)

        # Log usage on shutdown
        async def log_usage():
            try:
                summary = usage_collector.get_summary()
                logger.info(f"Usage summary: {summary}")
            except Exception as e:
                logger.error(f"Failed to get usage summary: {e}")

        ctx.add_shutdown_callback(log_usage)

        # Handle room disconnection
        @ctx.room.on("disconnected")
        def on_room_disconnected():
            logger.info("Room disconnected")

        # Start the assistant
        logger.info("Starting voice assistant...")
        assistant.start(ctx.room, participant)

        # Initial greeting
        await assistant.say(
            "Olá! Bem-vindo ao Restaurante Português. Como posso ajudar você hoje?",
            allow_interruptions=True,
        )

        # Keep the connection alive
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"Error in entrypoint: {e}")
        import traceback
        logger.error(traceback.format_exc())

if __name__ == "__main__":
    # Run the application with CLI
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )