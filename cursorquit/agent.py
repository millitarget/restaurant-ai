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
import time

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

# User Interaction Tracker for Adaptive Pacing
class UserInteractionTracker:
    def __init__(self):
        # Initial adaptation level - normal verbosity
        self.verbosity_level = "normal"  # Options: "concise", "normal", "detailed"
        
        # Tracking variables
        self.speech_durations = []
        self.response_times = []
        self.interruption_count = 0
        self.last_user_speech_end = None
        self.last_agent_speech_end = None
        
        # Analysis thresholds
        self.short_speech_threshold = 2.0   # seconds
        self.long_speech_threshold = 8.0   # seconds
        
        # Window size for pattern analysis
        self.window_size = 3
        
        logger.info(f"Initialized adaptive content with verbosity level: {self.verbosity_level}")
    
    def record_user_speech_start(self):
        # Record when user starts speaking
        self.user_speech_start_time = time.time()
        
        # If user starts speaking soon after agent finished, it might be an interruption
        if self.last_agent_speech_end and (time.time() - self.last_agent_speech_end < 0.5):
            self.interruption_count += 1
            logger.info(f"Possible interruption detected. Count: {self.interruption_count}")
    
    def record_user_speech_end(self):
        # Only calculate if we have a start time
        if hasattr(self, 'user_speech_start_time'):
            duration = time.time() - self.user_speech_start_time
            self.speech_durations.append(duration)
            
            # Keep only the last window_size entries
            if len(self.speech_durations) > self.window_size:
                self.speech_durations.pop(0)
                
            self.last_user_speech_end = time.time()
            
            # Log the duration for analysis
            logger.info(f"User speech duration: {duration:.2f}s")
    
    def record_agent_speech_end(self):
        self.last_agent_speech_end = time.time()
        
        # Calculate response time if user spoke before
        if self.last_user_speech_end:
            response_time = self.last_agent_speech_end - self.last_user_speech_end
            self.response_times.append(response_time)
            
            # Keep only the last window_size entries
            if len(self.response_times) > self.window_size:
                self.response_times.pop(0)
    
    def analyze_patterns_and_adjust_verbosity(self):
        # Only adjust if we have enough data
        if len(self.speech_durations) < 2:
            return self.verbosity_level
        
        # Calculate average speech duration in window
        avg_duration = sum(self.speech_durations) / len(self.speech_durations)
        
        # Analyze speech patterns and adjust verbosity
        new_verbosity = self.verbosity_level
        
        # 1. If user consistently speaks quickly/briefly, make responses more concise
        if avg_duration < self.short_speech_threshold:
            new_verbosity = "concise"
            logger.info(f"User speaks briefly ({avg_duration:.2f}s). Using concise responses.")
            
        # 2. If user consistently speaks at length, provide more detailed responses
        elif avg_duration > self.long_speech_threshold:
            new_verbosity = "detailed"
            logger.info(f"User speaks at length ({avg_duration:.2f}s). Using detailed responses.")
        
        # 3. If user frequently interrupts, use more concise responses
        elif self.interruption_count >= 2:
            new_verbosity = "concise"
            self.interruption_count = 0  # Reset counter after adjustment
            logger.info("Multiple interruptions detected. Using concise responses.")
        
        # Otherwise, use normal verbosity
        else:
            new_verbosity = "normal"
        
        # Only log if verbosity actually changed
        if new_verbosity != self.verbosity_level:
            logger.info(f"Adapting response style from {self.verbosity_level} to {new_verbosity}")
            self.verbosity_level = new_verbosity
            
        return self.verbosity_level

# Helper functions for content adaptation
def make_response_concise(text):
    """Make the response more concise for users who speak briefly"""
    
    # Split into sentences
    sentences = text.split('.')
    
    # If there's only 1-2 sentences, return as is
    if len(sentences) <= 2:
        return text
    
    # Remove greetings and fillers that are common in Portuguese
    filler_words = [
        "pois", "então", "ora bem", "é pá", "pronto", "com certeza", 
        "bem", "como sabe", "na verdade", "portanto"
    ]
    
    result_sentences = []
    for sentence in sentences:
        # Skip sentences that are just filler
        if sentence.strip() and not any(filler in sentence.lower() for filler in filler_words):
            # Remove filler phrases from within sentences
            for filler in filler_words:
                sentence = sentence.replace(filler, "")
                sentence = sentence.replace(filler.capitalize(), "")
            
            result_sentences.append(sentence.strip())
    
    # Join sentences back, limiting to essential information
    if len(result_sentences) > 3:
        result_sentences = result_sentences[:3]  # Keep only first 3 substantive sentences
    
    result = '. '.join(result_sentences)
    if not result.endswith('.'):
        result += '.'
        
    return result

def elaborate_response(text, context=None):
    """Elaborate the response for users who speak at length"""
    
    # If already a long response, don't make it longer
    if len(text) > 200:
        return text
    
    # Split into sentences
    sentences = text.split('.')
    sentences = [s.strip() for s in sentences if s.strip()]
    
    # Add more detail and Portuguese conversational elements
    elaborated = []
    
    # Add a warm greeting if this is at the beginning
    if context == "greeting":
        elaborated.append("Muito obrigado pela sua chamada.")
    
    for sentence in sentences:
        elaborated.append(sentence)
        
        # Add elaboration based on keywords in the sentence
        if "menu" in sentence.lower():
            elaborated.append("Todos os nossos pratos são preparados com ingredientes frescos e receitas tradicionais portuguesas")
        elif "bacalhau" in sentence.lower():
            elaborated.append("O nosso bacalhau é importado diretamente da Noruega e preparado segundo as melhores tradições portuguesas")
        elif "francesinha" in sentence.lower():
            elaborated.append("A nossa Francesinha é preparada com o autêntico molho do Porto, uma receita secreta da casa")
        elif "vinho" in sentence.lower():
            elaborated.append("Temos uma seleção de vinhos premiados de várias regiões vinícolas de Portugal")
        elif "sobremesa" in sentence.lower() or "doce" in sentence.lower():
            elaborated.append("As nossas sobremesas são feitas diariamente na nossa cozinha")
    
    # Add a polite closing if this seems like the end of a conversation
    if context == "closing":
        elaborated.append("Obrigado pela preferência e esperamos servi-lo novamente em breve")
    
    result = '. '.join(elaborated)
    if not result.endswith('.'):
        result += '.'
        
    return result

# Custom say function with adaptive content
async def adaptive_say(assistant, text, allow_interruptions=True, context=None):
    # Get the current verbosity level based on user interaction patterns
    verbosity_level = interaction_tracker.analyze_patterns_and_adjust_verbosity()
    
    # Adapt content based on verbosity level
    if verbosity_level == "concise":
        adapted_text = make_response_concise(text)
        logger.info(f"Using concise response style. Original length: {len(text)}, adapted length: {len(adapted_text)}")
    elif verbosity_level == "detailed":
        adapted_text = elaborate_response(text, context)
        logger.info(f"Using detailed response style. Original length: {len(text)}, adapted length: {len(adapted_text)}")
    else:
        adapted_text = text
        logger.info(f"Using normal response style. Length: {len(text)}")
    
    # Call the original say method with adapted content
    await assistant.say(adapted_text, allow_interruptions=allow_interruptions)

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

    # Initialize the adaptive pacing tracker
    global interaction_tracker
    interaction_tracker = UserInteractionTracker()
    logger.info("Initialized interaction tracker for adaptive content")

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
            logger.info("Using ElevenLabs TTS with voice 'Ana'")
            tts = elevenlabs.TTS(
                voice=Voice(
                    id="TsZfI8Nbn2Xd7ArC76n9", 
                    name="Ana", 
                    category="premade"
                ),
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
                    adaptive_say(
                        assistant,
                        "Peço desculpa, estamos com problemas no nosso sistema de reconhecimento de voz."
                    )
                )

        # User speech events with adaptive tracking
        @assistant.on("user_speech_started")
        def on_user_speech_started():
            logger.info("User started speaking")
            interaction_tracker.record_user_speech_start()

        @assistant.on("user_speech_ended")
        def on_user_speech_ended():
            logger.info("User stopped speaking")
            interaction_tracker.record_user_speech_end()
            
        @assistant.on("user_speech_committed")
        def on_user_speech_committed(msg):
            if hasattr(msg, 'content'):
                content = msg.content.lower() if msg.content else ""
                logger.info(f"User speech committed: {content}")
                
                # Check for regional cuisine inquiries
                regions = ["norte", "porto", "douro", "centro", "bairrada", "lisboa", "alentejo", "algarve"]
                for region in regions:
                    if region in content and ("especialidade" in content or "prato" in content or "típico" in content or "região" in content):
                        asyncio.create_task(adaptive_say(assistant, get_regional_specialties(region)))
                        return
                
                # Check for wine inquiries
                if "vinho" in content:
                    if "tinto" in content:
                        asyncio.create_task(adaptive_say(assistant, "Nos vinhos tintos, recomendo especialmente o nosso Quinta do Crasto Reserva do Douro."))
                    elif "branco" in content:
                        asyncio.create_task(adaptive_say(assistant, "Nos vinhos brancos, o Soalheiro Alvarinho de Vinho Verde é excelente para acompanhar pratos de peixe."))
                    elif "porto" in content or "do porto" in content:
                        asyncio.create_task(adaptive_say(assistant, "Temos uma excelente seleção de Vinhos do Porto. Recomendo o Taylor's 20 Anos para finalizar a sua refeição."))
                    else:
                        asyncio.create_task(adaptive_say(assistant, "Temos uma excelente carta de vinhos portugueses. Gostaria de conhecer os nossos tintos, brancos ou Vinhos do Porto?"))
                
                # Check for dessert inquiries
                if "sobremesa" in content or "doce" in content:
                    asyncio.create_task(adaptive_say(assistant, f"As nossas sobremesas são tradicionais portuguesas: {DESSERT_MENU}"))

        # Agent speech events with adaptive tracking
        @assistant.on("agent_speech_started")
        def on_agent_speech_started():
            logger.info("Agent started speaking")

        @assistant.on("agent_speech_ended")
        def on_agent_speech_ended():
            logger.info("Agent stopped speaking")
            interaction_tracker.record_agent_speech_end()
            
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
                    adaptive_say(
                        assistant,
                        "Selecionou a opção de reserva. Por favor, indique a data, hora e número de pessoas para a sua reserva."
                    )
                )
            elif digits == "2":
                asyncio.create_task(adaptive_say(assistant, f"Aqui está o nosso menu de hoje: {MENU}"))
            elif digits == "3":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Vou transferir a sua chamada para um dos nossos colaboradores. Um momento, por favor."
                    )
                )
            elif digits == "4":
                # Wine recommendations
                asyncio.create_task(adaptive_say(assistant, f"As nossas recomendações de vinhos: {WINE_RECOMMENDATIONS}"))
            elif digits == "5":
                # Dessert menu
                asyncio.create_task(adaptive_say(assistant, f"A nossa carta de sobremesas: {DESSERT_MENU}"))
            elif digits == "6":
                # Regional recommendations
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Para qual região de Portugal gostaria de conhecer as nossas especialidades? "
                        "Prima 1 para Norte, 2 para Centro, 3 para Lisboa, 4 para Alentejo, ou 5 para Algarve."
                    )
                )
            elif digits == "61":
                # North region specialties
                asyncio.create_task(adaptive_say(assistant, get_regional_specialties("norte")))
            elif digits == "62":
                # Central region specialties
                asyncio.create_task(adaptive_say(assistant, get_regional_specialties("centro")))
            elif digits == "63":
                # Lisbon region specialties
                asyncio.create_task(adaptive_say(assistant, get_regional_specialties("lisboa")))
            elif digits == "64":
                # Alentejo region specialties
                asyncio.create_task(adaptive_say(assistant, get_regional_specialties("alentejo")))
            elif digits == "65":
                # Algarve region specialties
                asyncio.create_task(adaptive_say(assistant, get_regional_specialties("algarve")))
            elif digits == "0":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Muito obrigado pela sua chamada para o Restaurante Português. Até à próxima!",
                        context="closing"
                    )
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
                # Log final adaptive content stats
                logger.info(f"Final verbosity level: {interaction_tracker.verbosity_level}")
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
        await adaptive_say(
            assistant,
            f"{greeting}! Bem-vindo ao Restaurante Português. O que gostaria de encomendar hoje?",
            allow_interruptions=True,
            context="greeting"
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