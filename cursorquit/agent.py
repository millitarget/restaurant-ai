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
import datetime

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

# Sample menu in European Portuguese with authentic Portuguese dishes
MENU = """O nosso menu de hoje inclui: 
1. Bacalhau à Lagareiro com batata a murro
2. Caldo Verde com chouriço de Trás-os-Montes
3. Francesinha do Porto com molho especial da casa
4. Arroz de tamboril com gambas
5. Leitão à Bairrada com batata frita
6. Pastéis de nata para sobremesa"""

# Wine recommendations in European Portuguese
WINE_RECOMMENDATIONS = """
Vinhos Tintos:
- Douro: Quinta do Crasto Reserva
- Alentejo: Herdade do Esporão Reserva
- Dão: Casa da Passarella O Oenólogo Vinhas Velhas

Vinhos Brancos:
- Vinho Verde: Soalheiro Alvarinho
- Douro: Niepoort Redoma Branco
- Bairrada: Luís Pato Vinha Formal

Vinhos do Porto:
- Taylor's 20 Anos
- Graham's Tawny 10 Anos
- Niepoort Vintage
"""

# Dessert menu in European Portuguese
DESSERT_MENU = """
Sobremesas Tradicionais:
1. Pastéis de nata com canela
2. Arroz doce com canela
3. Pudim Abade de Priscos
4. Pão de Ló de Ovar
5. Queijadas de Sintra
6. Toucinho do céu
"""

# System prompt for the assistant - Updated for European Portuguese takeout orders
SYSTEM_PROMPT = """És um assistente de voz para um restaurante português em Lisboa que atende encomendas para takeaway (levar para fora). Responde em português europeu de forma amigável e natural.
Usa expressões tipicamente portuguesas como "pois", "então", "ora bem", "é pá", "com certeza", e "pronto".

Segue SEMPRE estas etapas nesta ordem exata:
1. Primeiro, pergunta o que o cliente deseja encomendar do menu. Deixa o cliente dizer todos os itens que quer encomendar.
2. Depois que o cliente terminar de escolher, pergunta a hora de levantamento desejada para o pedido.
3. Finalmente, pergunta o nome do cliente para associar à encomenda.

Refere-te ao cliente como "o senhor" ou "a senhora" para ser formal.
Mantém as respostas concisas e naturais, como numa conversa telefónica real em Portugal.

Se o cliente perguntar sobre o menu, informa os pratos disponíveis com descrições autênticas da gastronomia portuguesa.
Quando completa o pedido, repete-o para confirmar todos os detalhes, incluindo os itens, hora de levantamento e nome do cliente."""

def prewarm(proc):
    """Preload models for faster startup"""
    try:
        logger.info("Preloading VAD model...")
        proc.userdata["vad"] = silero.VAD.load()
        logger.info("VAD model preloaded successfully")
    except Exception as e:
        logger.error(f"Failed to preload VAD model: {e}")

# Helper functions for European Portuguese responses
def get_time_greeting():
    """Returns an appropriate greeting based on the time of day in Portugal"""
    hour = (datetime.datetime.utcnow().hour + 1) % 24  # Portugal is UTC+1 (simplified)
    if 5 <= hour < 12:
        return "Bom dia"
    elif 12 <= hour < 20:
        return "Boa tarde"
    else:
        return "Boa noite"
        
def get_regional_specialties(region):
    """Returns specialty recommendations based on Portuguese regions"""
    regions = {
        "norte": "No Norte de Portugal, recomendo a nossa Francesinha do Porto ou o Bacalhau à Lagareiro.",
        "porto": "Do Porto, temos a autêntica Francesinha com molho especial da casa, acompanhada de batatas fritas caseiras.",
        "douro": "Da região do Douro, recomendo acompanhar a refeição com um bom vinho tinto Douro DOC.",
        "centro": "Do Centro de Portugal, o nosso Leitão à Bairrada é imperdível, preparado tradicionalmente.",
        "bairrada": "Da Bairrada, o nosso Leitão é preparado seguindo a receita tradicional, com a pele estaladiça.",
        "lisboa": "De Lisboa, recomendo as nossas Amêijoas à Bulhão Pato como entrada.",
        "alentejo": "Do Alentejo, embora não esteja no menu de hoje, por vezes temos Migas com Carne de Porco à Alentejana.",
        "algarve": "Do Algarve, ocasionalmente preparamos Cataplana de Marisco, especialmente aos fins-de-semana."
    }
    return regions.get(region.lower(), "Temos pratos de várias regiões de Portugal no nosso menu. Posso recomendar algo específico?")

async def entrypoint(ctx: JobContext):
    """Main entrypoint for the agent"""
    logger.info(f"Starting Portuguese restaurant voice assistant for room {ctx.room.name}")

    # Create chat context with system prompt
    logger.info("Setting up assistant system prompt")
    initial_ctx = llm.ChatContext().append(
        role="system",
        text=SYSTEM_PROMPT,
    )
    
    # Add examples of European Portuguese responses
    initial_ctx = initial_ctx.append(
        role="system",
        text="""Exemplos de respostas em português europeu:
        
        Para pedido de reserva:
        "Com certeza! Para quantas pessoas deseja a reserva? E para que dia e hora, por favor?"
        
        Para pedido de recomendação:
        "Ora bem, hoje recomendo especialmente o Bacalhau à Lagareiro. É preparado com o melhor azeite português e acompanhado de batata a murro. É uma delícia!"
        
        Para informação sobre vinhos:
        "Temos uma excelente seleção de vinhos portugueses. Posso sugerir um Douro tinto que combina perfeitamente com o nosso bacalhau?"
        
        Para despedida:
        "Muito obrigado pela sua visita. Esperamos vê-lo novamente em breve. Até à próxima!"
        """
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
                voice=Voice(id="TsZfI8Nbn2Xd7ArC76n9", name="Ana", category="premade"),  # William voice ID
                api_key=elevenlabs_key
            )
        else:
            logger.info("Using OpenAI TTS (fallback)")
            tts = openai.TTS()

        # Configure STT with Deepgram - Set to European Portuguese
        stt = deepgram.STT(
            language="pt-PT",  # Specifically set to European Portuguese
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
                        "Peço desculpa, estamos com problemas no nosso sistema de reconhecimento de voz."
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
                content = msg.content.lower() if msg.content else ""
                logger.info(f"User speech committed: {content}")
                
                # Check for regional cuisine inquiries
                regions = ["norte", "porto", "douro", "centro", "bairrada", "lisboa", "alentejo", "algarve"]
                for region in regions:
                    if region in content and ("especialidade" in content or "prato" in content or "típico" in content or "região" in content):
                        asyncio.create_task(assistant.say(get_regional_specialties(region)))
                        return
                
                # Check for wine inquiries
                if "vinho" in content:
                    if "tinto" in content:
                        asyncio.create_task(assistant.say("Nos vinhos tintos, recomendo especialmente o nosso Quinta do Crasto Reserva do Douro."))
                    elif "branco" in content:
                        asyncio.create_task(assistant.say("Nos vinhos brancos, o Soalheiro Alvarinho de Vinho Verde é excelente para acompanhar pratos de peixe."))
                    elif "porto" in content or "do porto" in content:
                        asyncio.create_task(assistant.say("Temos uma excelente seleção de Vinhos do Porto. Recomendo o Taylor's 20 Anos para finalizar a sua refeição."))
                    else:
                        asyncio.create_task(assistant.say("Temos uma excelente carta de vinhos portugueses. Gostaria de conhecer os nossos tintos, brancos ou Vinhos do Porto?"))
                
                # Check for dessert inquiries
                if "sobremesa" in content or "doce" in content:
                    asyncio.create_task(assistant.say(f"As nossas sobremesas são tradicionais portuguesas: {DESSERT_MENU}"))

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

        # DTMF handler with European Portuguese responses
        @assistant.on("dtmf_received")
        def on_dtmf_received(digits):
            logger.info(f"DTMF digits received: {digits}")
            if digits == "1":
                asyncio.create_task(
                    assistant.say(
                        "Selecionou a opção de reserva. Por favor, indique a data, hora e número de pessoas para a sua reserva."
                    )
                )
            elif digits == "2":
                asyncio.create_task(assistant.say(f"Aqui está o nosso menu de hoje: {MENU}"))
            elif digits == "3":
                asyncio.create_task(
                    assistant.say("Vou transferir a sua chamada para um dos nossos colaboradores. Um momento, por favor.")
                )
            elif digits == "4":
                # Wine recommendations
                asyncio.create_task(assistant.say(f"As nossas recomendações de vinhos: {WINE_RECOMMENDATIONS}"))
            elif digits == "5":
                # Dessert menu
                asyncio.create_task(assistant.say(f"A nossa carta de sobremesas: {DESSERT_MENU}"))
            elif digits == "6":
                # Regional recommendations
                asyncio.create_task(
                    assistant.say(
                        "Para qual região de Portugal gostaria de conhecer as nossas especialidades? "
                        "Prima 1 para Norte, 2 para Centro, 3 para Lisboa, 4 para Alentejo, ou 5 para Algarve."
                    )
                )
            elif digits == "61":
                # North region specialties
                asyncio.create_task(assistant.say(get_regional_specialties("norte")))
            elif digits == "62":
                # Central region specialties
                asyncio.create_task(assistant.say(get_regional_specialties("centro")))
            elif digits == "63":
                # Lisbon region specialties
                asyncio.create_task(assistant.say(get_regional_specialties("lisboa")))
            elif digits == "64":
                # Alentejo region specialties
                asyncio.create_task(assistant.say(get_regional_specialties("alentejo")))
            elif digits == "65":
                # Algarve region specialties
                asyncio.create_task(assistant.say(get_regional_specialties("algarve")))
            elif digits == "0":
                asyncio.create_task(
                    assistant.say("Muito obrigado pela sua chamada para o Restaurante Português. Até à próxima!")
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

        # Get appropriate greeting based on time of day
        greeting = get_time_greeting()

        # Initial greeting with European Portuguese phrasing
        await assistant.say(
            f"{greeting}! Bem-vindo ao Restaurante Português. O que gostaria de encomendar hoje?",
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