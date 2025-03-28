import asyncio
import logging
import os
import sys
import requests
import json
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
import re
import random
import numpy as np
from livekit.data.data_packet_type import DataPacketType
from functools import lru_cache
import hashlib

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
MENU = """MENU DO RESTAURANTE PORTUGUÊS:

CARNE:
1 Frango do Churrasco - 7.90€
1/2 Frango do Churrasco - 4.50€
1/2 Frango do Churrasco * - 8.00€
1 Espetada de Guia (Caleto) - 6.50€
1 Espetada de Frango c/ Bacon - 6.50€
1 Dose de Entrecosto - 8.00€
1/2 Dose de Entrecosto - 4.50€
1 Salsicha Toscana - 2.00€
1 Févera de Porco - 6.00€
1 Costeleta de Vitela - 25€/kg
1 Costeleta de Porco - 6.00€
1 Coelho* - 12.50€
Costelinha - 19€/kg
Picanha - 36.50€/kg
1 Bife do Frango - 6.00€
Bife do Lombo - 40€/kg

* Tempo estimado: 30 a 40 minutos

ACOMPANHAMENTOS:
1 Dose de Batata Frita - 3.75€
1 Dose de Batata Frita Barrosa - 2.50€
1 Dose de Arroz - 3.75€
1/2 Dose de Arroz - 2.50€
1 Salada Mista - 4.00€
1/2 Salada Mista - 2.75€
1 Salada de Tomate - 4.00€
1 Salada de Alface - 4.00€
1 Dose de Feijão Preto - 5.75€
1/2 Dose de Feijão Preto - 3.95€
1 Esparregado Grelos/Espinafres - 5.50€
1 Broa de Milho - 1.90€
1/2 Broa de Milho - 1.00€
1 Broa de Avintes - 3.50€
1/2 Broa de Avintes - 2.00€
1 Trança (Caceté) - 1.80€

PEIXE:
Bacalhau assado na brasa* - 19.50€ (1 Pessoa)
                           32.50€ (2 Pessoas)
(com batata cozida, ovo cozido, pimento e cebola)

* Tempo estimado: 40 minutos

REFRIGERANTES:
Refrigerantes 1 Litro - 2.75€
Refrigerantes 1.5 Litro - 3.00€

VINHOS:
Vinhos Verdes 0.75cl
Vinho da Casa Cruzeiro Lima - 4.00€ (Branco e Tinto)
Vinho Branco Muralhas Monção - 7.00€
Vinho Branco Casal Garcia - 7.00€

Vinhos Maduros 0.75cl
Vinho Porta da Ravessa - 4.50€ (Branco e Tinto)
Vinho Gasificado Castiço - 5.50€
Vinho Monte Velho Tinto - 7.00€
Vinho Eugénio de Almeida Tinto - 7.00€"""

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

# System prompt for the assistant - Updated for Quitanda
SYSTEM_PROMPT = """És um assistente da Churrascaria Quitanda que atende encomendas takeaway. 
Responde em português europeu, conciso e natural. Recolhe: 1) items do menu, 2) hora de levantamento, 
3) nome do cliente. Sê formal ("o senhor"/"a senhora"). Respostas breves como numa chamada telefónica real.

IMPORTANTE: Só aceites pedidos que estejam EXATAMENTE no menu da Quitanda. Se o cliente pedir algo que não está no menu, 
informe gentilmente que não está disponível e sugira alternativas do menu atual. Nunca aceite variações ou modificações 
dos pratos que não estejam explicitamente listadas no menu."""

# Make.com webhook URL for sending transcript
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "https://hook.eu2.make.com/your_webhook_id_here")

# Conversation transcript tracker
class ConversationTracker:
    def __init__(self):
        self.transcript = []
        self.order_details = {
            "customer_name": None,
            "pickup_time": None,
            "items": []
        }
    
    def add_user_message(self, message):
        self.transcript.append({"role": "user", "content": message, "timestamp": time.time()})
        logger.info(f"Added user message to transcript: {message}")
        
        # Try to extract order details from user messages
        self._extract_order_details(message, is_user=True)
    
    def add_assistant_message(self, message):
        self.transcript.append({"role": "assistant", "content": message, "timestamp": time.time()})
        logger.info(f"Added assistant message to transcript: {message}")
        
        # Also check assistant messages for order confirmation
        self._extract_order_details(message, is_user=False)
    
    def _extract_order_details(self, message, is_user=True):
        """Extract order details from conversation messages"""
        message_lower = message.lower()
        
        # Extract customer name
        if self.order_details["customer_name"] is None:
            # Common patterns for customer name in Portuguese conversations
            name_patterns = [
                r"meu nome é ([A-Za-zÀ-ÖØ-öø-ÿ\s]+)",
                r"chamo-me ([A-Za-zÀ-ÖØ-öø-ÿ\s]+)",
                r"nome (?:é|para) ([A-Za-zÀ-ÖØ-öø-ÿ\s]+)",
                r"(^|\s)([A-Za-zÀ-ÖØ-öø-ÿ]{2,})\s+([A-Za-zÀ-ÖØ-öø-ÿ]{2,})($|\s)"  # Simple first+last name pattern
            ]
            
            for pattern in name_patterns:
                matches = re.search(pattern, message_lower)
                if matches:
                    potential_name = matches.group(1).strip() if len(matches.groups()) == 1 else f"{matches.group(2)} {matches.group(3)}".strip()
                    # Verify it's not just a common phrase
                    common_words = ["obrigado", "bom", "boa", "dia", "tarde", "noite", "gostaria", "desejo", "quero"]
                    if len(potential_name.split()) >= 2 and not any(word in potential_name.lower() for word in common_words):
                        self.order_details["customer_name"] = potential_name
                        logger.info(f"Extracted customer name: {potential_name}")
        
        # Extract pickup time
        if self.order_details["pickup_time"] is None:
            # Patterns for pickup time in Portuguese contexts
            time_patterns = [
                r"(?:às|as|para) (\d{1,2})[h:. ]?(\d{0,2})",  # 15h30, 15:30, 15h, etc.
                r"(\d{1,2})[h:. ]?(\d{0,2}) (?:horas|hora)",   # 15h30 horas, 15 horas, etc.
                r"levantar (?:às|as|para) (\d{1,2})[h:. ]?(\d{0,2})",  # pickup specific
                r"buscar (?:às|as|para) (\d{1,2})[h:. ]?(\d{0,2})"     # pickup specific
            ]
            
            for pattern in time_patterns:
                matches = re.search(pattern, message_lower)
                if matches:
                    hour = int(matches.group(1))
                    minute = int(matches.group(2)) if matches.group(2) else 0
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        time_str = f"{hour:02d}:{minute:02d}"
                        self.order_details["pickup_time"] = time_str
                        logger.info(f"Extracted pickup time: {time_str}")
        
        # Extract menu items - Updated with the new comprehensive menu
        menu_items = {
            # Carne (Meat)
            "frango do churrasco": "Frango do Churrasco",
            "1/2 frango": "1/2 Frango do Churrasco",
            "espetada de guia": "Espetada de Guia (Caleto)",
            "espetada de frango": "Espetada de Frango c/ Bacon",
            "entrecosto": "Dose de Entrecosto",
            "salsicha toscana": "Salsicha Toscana",
            "févera de porco": "Févera de Porco",
            "costeleta de vitela": "Costeleta de Vitela",
            "costeleta de porco": "Costeleta de Porco",
            "coelho": "Coelho",
            "costelinha": "Costelinha",
            "picanha": "Picanha",
            "bife do frango": "Bife do Frango",
            "bife do lombo": "Bife do Lombo",
            
            # Acompanhamentos (Side dishes)
            "batata frita": "Dose de Batata Frita",
            "batata frita barrosa": "Dose de Batata Frita Barrosa",
            "arroz": "Dose de Arroz",
            "salada mista": "Salada Mista",
            "salada de tomate": "Salada de Tomate",
            "salada de alface": "Salada de Alface",
            "feijão preto": "Dose de Feijão Preto",
            "esparregado": "Esparregado Grelos/Espinafres",
            "broa de milho": "Broa de Milho",
            "broa de avintes": "Broa de Avintes",
            "trança": "Trança (Caceté)",
            "cacete": "Trança (Caceté)",
            
            # Peixe (Fish)
            "bacalhau": "Bacalhau assado na brasa",
            "bacalhau assado": "Bacalhau assado na brasa",
            
            # Bebidas (Drinks)
            "refrigerante": "Refrigerantes 1 Litro",
            "vinho verde": "Vinho da Casa Cruzeiro Lima",
            "muralhas monção": "Vinho Branco Muralhas Monção",
            "casal garcia": "Vinho Branco Casal Garcia",
            "porta da ravessa": "Vinho Porta da Ravessa",
            "castiço": "Vinho Gasificado Castiço",
            "monte velho": "Vinho Monte Velho Tinto",
            "eugénio de almeida": "Vinho Eugénio de Almeida Tinto"
        }
        
        # Look for menu items with quantity patterns
        quantity_patterns = [
            r"(\d+)\s+(?:de\s+)?([A-Za-zÀ-ÖØ-öø-ÿ\s]+)",  # 2 Francesinhas
            r"([A-Za-zÀ-ÖØ-öø-ÿ\s]+)\s+(?:-\s+)?(\d+)"    # Francesinhas - 2
        ]
        
        if is_user:  # Only extract items from user messages
            # Check for direct mentions of menu items
            for item_key, item_name in menu_items.items():
                if item_key in message_lower:
                    # Try to find quantity
                    quantity = 1
                    for pattern in quantity_patterns:
                        matches = re.search(pattern, message_lower)
                        if matches and (item_key in matches.group(1).lower() or item_key in matches.group(2).lower()):
                            try:
                                quantity = int(matches.group(1) if item_key in matches.group(2).lower() else matches.group(2))
                                break
                            except (ValueError, IndexError):
                                pass
                    
                    # Add to order items if not already there
                    item_entry = {"item": item_name, "quantity": quantity}
                    if not any(existing["item"] == item_name for existing in self.order_details["items"]):
                        self.order_details["items"].append(item_entry)
                        logger.info(f"Added item to order: {quantity}x {item_name}")
                    else:
                        # Update quantity if item exists
                        for existing in self.order_details["items"]:
                            if existing["item"] == item_name:
                                existing["quantity"] = quantity
                                logger.info(f"Updated item quantity: {quantity}x {item_name}")
        
        # Also check for mentions of portions (meia dose, uma dose)
        portion_patterns = [
            r"(?:uma|1)\s+dose\s+de\s+([A-Za-zÀ-ÖØ-öø-ÿ\s]+)",  # uma dose de arroz
            r"(?:meia|1/2)\s+dose\s+de\s+([A-Za-zÀ-ÖØ-öø-ÿ\s]+)"  # meia dose de arroz
        ]
        
        for i, pattern in enumerate(portion_patterns):
            matches = re.finditer(pattern, message_lower)
            for match in matches:
                item_base = match.group(1).strip()
                portion_type = "1/2" if i == 1 else "1"
                
                # Find the corresponding menu item
                for item_key, item_name in menu_items.items():
                    if item_base in item_key:
                        if "1/2" in item_name and portion_type == "1/2":
                            # It's already a half portion in the menu
                            full_item = item_name
                        elif portion_type == "1/2" and "1/2" not in item_name:
                            # Need to convert to half portion
                            full_item = f"1/2 {item_name}"
                        else:
                            full_item = item_name
                            
                        # Add to order items
                        item_entry = {"item": full_item, "quantity": 1}
                        if not any(existing["item"] == full_item for existing in self.order_details["items"]):
                            self.order_details["items"].append(item_entry)
                            logger.info(f"Added portion item to order: {full_item}")
                        break
    
    def get_transcript(self):
        return self.transcript
    
    def get_order_summary(self):
        """Get a formatted summary of the order"""
        if not self.order_details["items"]:
            return "Nenhum item foi pedido ainda."
        
        summary = "Resumo do pedido:\n"
        
        for item in self.order_details["items"]:
            summary += f"- {item['quantity']}x {item['item']}\n"
        
        if self.order_details["pickup_time"]:
            summary += f"\nHorário de levantamento: {self.order_details['pickup_time']}"
        
        if self.order_details["customer_name"]:
            summary += f"\nNome: {self.order_details['customer_name']}"
            
        return summary
    
    def send_to_webhook(self):
        """Send the complete transcript to the Make.com webhook"""
        try:
            if not MAKE_WEBHOOK_URL or MAKE_WEBHOOK_URL == "https://hook.eu2.make.com/your_webhook_id_here":
                logger.warning("Make.com webhook URL not configured. Transcript not sent.")
                return False
                
            payload = {
                "transcript": self.transcript,
                "order_details": self.order_details,
                "order_summary": self.get_order_summary()
            }
            
            logger.info(f"Sending transcript to Make.com webhook: {MAKE_WEBHOOK_URL}")
            response = requests.post(MAKE_WEBHOOK_URL, json=payload)
            
            if response.status_code == 200:
                logger.info("Transcript successfully sent to Make.com")
                return True
            else:
                logger.error(f"Failed to send transcript. Status code: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending transcript to Make.com: {e}")
            return False

def prewarm(proc):
    """Preload models for faster startup"""
    try:
        logger.info("Preloading VAD model...")
        proc.userdata["vad"] = silero.VAD.load(
            min_speech_duration=0.05,    # Shorter speech detection
            min_silence_duration=0.2,     # Shorter silence detection
            activation_threshold=0.6,     # Lower threshold for faster activation
            sample_rate=8000,            # Lower sample rate for faster processing
            force_cpu=True               # Force CPU for more consistent performance
        )
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

# Simplified interaction tracking - removing unnecessary verbosity analysis
class UserInteractionTracker:
    def __init__(self):
        # Remove verbosity tracking to speed up responses
        self.last_user_speech_end = None
        self.last_agent_speech_end = None
        
        # Tracking for empty speech detection only
        self.empty_speech_count = 0
        
        logger.info("Initialized simplified interaction tracker")
    
    def record_user_speech_start(self):
        # Record when user starts speaking
        self.user_speech_start_time = time.time()
    
    def record_user_speech_end(self):
        # Only update timing for speech detection
        if hasattr(self, 'user_speech_start_time'):
            self.last_user_speech_end = time.time()
    
    def record_agent_speech_end(self):
        self.last_agent_speech_end = time.time()
    
    def check_for_empty_speech(self, content):
        """Check if the speech content is meaningful or just background noise"""
        # Strip whitespace and punctuation
        cleaned_content = content.strip()
        if not cleaned_content or len(cleaned_content) < 2:
            self.empty_speech_count += 1
            return True
        
        # Check for common background noise transcriptions
        noise_patterns = ['.', '...', 'hmm', 'ah', 'uh', 'um', 'eh', 'oh']
        if cleaned_content.lower() in noise_patterns:
            self.empty_speech_count += 1
            return True
            
        # This is legitimate speech
        self.empty_speech_count = 0
        return False

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

# Common pre-generated responses for faster interaction
COMMON_RESPONSES = {
    "greeting": {
        "morning": "Bom dia! Churrascaria Quitanda, em que posso ajudar?",
        "afternoon": "Boa tarde! Churrascaria Quitanda, em que posso ajudar?",
        "evening": "Boa noite! Churrascaria Quitanda, em que posso ajudar?"
    },
    "menu_request": "Aqui está o nosso menu principal. O que gostaria de encomendar?",
    "confirmation": "Perfeito! Vou registar o seu pedido.",
    "thanks": "Muito obrigado pela sua encomenda.",
    "wait": ["Um momento...", "Já verifico...", "Vou ver isso...", "Um instante por favor..."],
    "acknowledgment": ["Entendido.", "Compreendo.", "Certo.", "Claro.", "Sim."],
    "not_available": "Peço desculpa, mas esse item não está disponível no nosso menu atual. Posso sugerir algumas alternativas do nosso cardápio?",
    "item_not_found": "Peço desculpa, mas não encontro esse item no nosso menu. Gostaria que eu lesse o menu novamente?"
}

# Custom say function - simplified for faster performance 
async def adaptive_say(assistant, text, allow_interruptions=True, context=None):
    # Use pre-generated responses when possible for immediate response
    if context in COMMON_RESPONSES and isinstance(COMMON_RESPONSES[context], str):
        logger.info(f"Using pre-generated response for context: {context}")
        await assistant.say(COMMON_RESPONSES[context], allow_interruptions=allow_interruptions)
        return
    elif context == "greeting":
        # Determine time of day for appropriate greeting
        current_hour = datetime.datetime.now().hour
        if current_hour < 12:
            greeting_type = "morning"
        elif current_hour < 19:
            greeting_type = "afternoon"
        else:
            greeting_type = "evening"
        await assistant.say(COMMON_RESPONSES["greeting"][greeting_type], allow_interruptions=allow_interruptions)
        return
    
    # For critical/menu responses only, handle in chunks to appear faster
    if context in ["menu_request", "order_summary"] and len(text) > 80:
        # Split on periods and send chunks
        sentences = text.split('.')
        chunks = []
        current = ""
        
        # Create chunks of reasonable size
        for sentence in sentences:
            if sentence.strip():
                if len(current) + len(sentence) < 80:
                    current += sentence + "."
                else:
                    if current:
                        chunks.append(current)
                    current = sentence + "."
        
        if current:
            chunks.append(current)
            
        # Send chunks sequentially
        for chunk in chunks:
            await assistant.say(chunk, allow_interruptions=True)
        return
    
    # For all other responses, send directly without content adaptation
    await assistant.say(text, allow_interruptions=allow_interruptions)

def chunk_text(text, max_chunk_length=100):
    """Split text into natural chunks for more responsive speech"""
    # Split by natural break points
    chunks = []
    current_chunk = []
    current_length = 0
    
    # Split by sentences first
    sentences = text.split('.')
    
    for sentence in sentences:
        if not sentence.strip():
            continue
            
        # If sentence is too long, split by commas
        if len(sentence) > max_chunk_length:
            parts = sentence.split(',')
            for part in parts:
                if part.strip():
                    chunks.append(part.strip() + '.')
        else:
            chunks.append(sentence.strip() + '.')
    
    return chunks

class ResponseCache:
    def __init__(self, max_size=100):
        self.cache = {}
        self.max_size = max_size
        
    def get(self, text):
        """Get cached audio for text if available"""
        key = self._get_key(text)
        return self.cache.get(key)
        
    def set(self, text, audio):
        """Cache audio for text"""
        key = self._get_key(text)
        if len(self.cache) >= self.max_size:
            # Remove oldest item
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = audio
        
    def _get_key(self, text):
        """Generate cache key for text"""
        return hashlib.md5(text.encode()).hexdigest()

# Initialize cache
response_cache = ResponseCache()

# Update the speak function to use cache
async def speak(text):
    """Speak text with optimized streaming and natural pauses"""
    if not text:
        logger.warning("Empty text provided to speak function")
        return
        
    try:
        # Add natural fillers for more human-like response
        text = f"[Fale em português de Portugal com sotaque nativo] {text}"
        
        # Check cache first
        cached_audio = response_cache.get(text)
        if cached_audio:
            await stream_audio(np.frombuffer(cached_audio, dtype=np.int16))
            return
            
        # Split into chunks for progressive streaming
        chunks = chunk_text(text)
        
        for chunk in chunks:
            if not chunk.strip():
                continue
                
            # Check cache for chunk
            cached_chunk = response_cache.get(chunk)
            if cached_chunk:
                await stream_audio(np.frombuffer(cached_chunk, dtype=np.int16))
                await asyncio.sleep(0.1)
                continue
                
            # Generate audio for this chunk
            audio = tts.create(chunk)
            
            # Cache the audio
            response_cache.set(chunk, audio)
            
            # Convert to numpy array for streaming
            audio_array = np.frombuffer(audio, dtype=np.int16)
            
            # Stream the chunk with a small pause between chunks
            await stream_audio(audio_array)
            
            # Add a small natural pause between chunks
            await asyncio.sleep(0.1)
            
    except Exception as e:
        logger.error(f"Error in speak: {e}")
        # Fallback to simple text-to-speech if streaming fails
        try:
            audio = tts.create(text)
            await stream_audio(np.frombuffer(audio, dtype=np.int16))
        except Exception as e:
            logger.error(f"Fallback speak failed: {e}")
            # Final fallback if everything fails
            logger.error("All TTS attempts failed")

async def stream_audio(audio_array):
    """Stream audio with optimized buffering"""
    try:
        # Use a smaller chunk size for more responsive streaming
        chunk_size = 1024  # Reduced from 2048 for better responsiveness
        
        for i in range(0, len(audio_array), chunk_size):
            chunk = audio_array[i:i + chunk_size]
            if len(chunk) > 0:
                await asyncio.sleep(0.01)  # Small delay to prevent audio glitches
                await room.local_participant.publish_data(
                    chunk.tobytes(),
                    data_packet_type=DataPacketType.AUDIO
                )
    except Exception as e:
        logger.error(f"Error in stream_audio: {e}")
        raise

async def entrypoint(ctx: JobContext):
    """Main entrypoint for the agent"""
    logger.info(f"Starting Portuguese restaurant voice assistant for room {ctx.room.name}")

    # Initialize the simplified interaction tracker
    global interaction_tracker
    interaction_tracker = UserInteractionTracker()
    logger.info("Initialized simplified interaction tracker")
    
    # Global tracker for metrics, transcript and order details
    global conversation_tracker
    conversation_tracker = ConversationTracker()
    logger.info("Initialized conversation tracker for order details and transcript")

    # Create the chat context with system prompt
    logger.info("Setting up assistant system prompt")
    initial_ctx = llm.ChatContext().append(
        role="system",
        text=SYSTEM_PROMPT,
    )
    
    # Add examples of European Portuguese responses
    initial_ctx = initial_ctx.append(
        role="system",
        text="""Exemplos de respostas em português europeu para a Churrascaria Quitanda:
        
        Para pedido de menu:
        "Aqui está o nosso menu. Temos pratos de carne como Frango do Churrasco, Espetadas, Entrecosto, e também Bacalhau assado na brasa. Como acompanhamento, temos batatas fritas, arroz, saladas e broa. O que gostaria de encomendar?"
        
        Para pedido não disponível:
        "Peço desculpa, mas esse item não está disponível no nosso menu atual. Posso sugerir algumas alternativas? Por exemplo, temos o Frango do Churrasco ou a Espetada de Guia."
        
        Para pedido de bebidas:
        "Temos refrigerantes em garrafa de 1L e 1.5L, e uma seleção de vinhos portugueses. Gostaria de conhecer as nossas opções?"
        
        Para confirmação de pedido:
        "Perfeito! Vou registar o seu pedido. Para confirmar, o senhor/a senhora pediu [item] com [acompanhamento]. Está correto?"
        
        Para despedida:
        "Muito obrigado pela sua encomenda. Esperamos servi-lo novamente em breve. Até à próxima!"
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
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY", "")

    if not deepgram_key or deepgram_key.startswith(("YOUR_", "REPLACE_")):
        logger.warning("Deepgram API key is missing or invalid")

    # Global variable for room access in stream_audio function
    global room
    room = ctx.room

    try:
        # Create the voice assistant
        logger.info("Initializing voice assistant...")

        # Configure TTS with ElevenLabs
        if elevenlabs_key:
            from elevenlabs import VoiceSettings, generate, stream
            
            # Optimize streaming configuration
            def stream_audio_optimized(text, voice="Ana", model="eleven_multilingual_v2"):
                """Stream audio with optimized settings for faster processing"""
                audio_stream = generate(
                    text=text,
                    voice=voice,
                    model=model,
                    stream=True,
                    latency=1,  # Lower latency for faster response
                    stability=0.4,  # Slightly less stability for faster processing
                    similarity_boost=0.65,  # Balance voice quality and speed
                )
                return audio_stream
            
            # Configure TTS with faster settings - reduced stability for speed
            tts = elevenlabs.TTS(
                voice=Voice(
                    id="FIEA0c5UHH9JnvWaQrXS", 
                    name="Ana", 
                    category="premade",
                ),
                api_key=elevenlabs_key,
                # Add optimization parameters for faster processing
                settings=VoiceSettings(
                    stability=0.3,  # Further reduced for faster processing
                    similarity_boost=0.6,  # Further reduced for faster processing
                    style=0.0,      # Neutral style for faster processing
                    use_speaker_boost=True  # Clearer audio
                )
            )
            voice_message = f"Using ElevenLabs TTS with voice: Ana (optimized for speed)"
        else:
            logger.info("Using OpenAI TTS (fallback)")
            tts = openai.TTS(
                voice="alloy",  # Using alloy voice which has good Portuguese support
                model="tts-1"  # Using the latest model
            )

        # Configure STT with Deepgram - Set to European Portuguese
        stt = deepgram.STT(
            language="pt-PT",
            model="nova-2",
            interim_results=True,  # Enable interim results for faster feedback
            punctuate=True,
            # Add optimization parameters
            utterance_end_ms=500,  # Shorter utterance end time
            vad_events=True,  # Enable VAD events for better speech detection
            vad_tail_padding_ms=100,  # Shorter tail padding
            vad_parameters={
                "min_speech_duration_ms": 100,  # Shorter minimum speech duration
                "min_silence_duration_ms": 200,  # Shorter silence duration
                "speech_pad_ms": 50,  # Shorter speech padding
                "threshold": 0.5  # Lower threshold for faster detection
            }
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

        # User speech events with simplified tracking
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
                
                # Ignore empty content completely
                if not content:
                    logger.info("Ignoring empty speech")
                    return
                
                # Check if this is just noise or empty speech
                if interaction_tracker.check_for_empty_speech(content):
                    logger.info("Ignoring noise-only speech")
                    return
                  
                # Add to transcript tracker
                conversation_tracker.add_user_message(content)
                
                # Queue processing as separate task
                process_response(msg, content, assistant)

        # Process user input in a non-blocking way - simplified
        def process_response(msg, content, assistant):
            # Skip the acknowledgment system completely for faster responses
            # Start processing the full response immediately
            asyncio.create_task(process_user_speech(msg, content, assistant))

        # Process user speech with full response logic - optimized for speed
        async def process_user_speech(msg, content, assistant, acknowledgment_task=None):
            # Extract order details if present
            conversation_tracker._extract_order_details(content)
            
            # Direct keyword matching for faster response
            if "menu" in content or "cardápio" in content:
                await adaptive_say(assistant, f"Aqui está o nosso menu principal: {MENU[:150]}...", context="menu_request")
                return
                
            if "vinho" in content:
                if "tinto" in content:
                    await adaptive_say(assistant, "Nos vinhos tintos, temos o Monte Velho Tinto e o Eugénio de Almeida Tinto, ambos a 7.00€.")
                elif "branco" in content:
                    await adaptive_say(assistant, "Nos vinhos brancos, temos o Muralhas Monção e o Casal Garcia, ambos a 7.00€.")
                elif "verde" in content:
                    await adaptive_say(assistant, "Temos o Vinho da Casa Cruzeiro Lima, disponível em branco e tinto, a 4.00€.")
                else:
                    await adaptive_say(assistant, "Temos uma excelente carta de vinhos portugueses. Gostaria de conhecer os nossos tintos, brancos ou vinhos verdes?")
                return
            
            if "sobremesa" in content or "doce" in content:
                await adaptive_say(assistant, f"As nossas sobremesas são tradicionais portuguesas: {DESSERT_MENU}")
                return
            
            if "confirmar" in content or "confirmo" in content:
                order_summary = conversation_tracker.get_order_summary()
                await adaptive_say(assistant, f"Perfeito! O seu pedido foi confirmado: {order_summary}", context="confirmation")
                conversation_tracker.send_to_webhook()
                return
            
            if ("completo" in content or "terminar" in content) and conversation_tracker.order_details["items"]:
                order_summary = conversation_tracker.get_order_summary()
                await adaptive_say(assistant, f"Resumindo o seu pedido: {order_summary}. Está tudo correto?", context="order_summary")
                return

        # Track usage metrics - fixed indentation
        usage_collector = metrics.UsageCollector()

        @assistant.on("metrics_collected") 
        def on_metrics_collected(mtrcs):
            metrics.log_metrics(mtrcs)
            usage_collector.collect(mtrcs)

        # Log usage on shutdown and send transcript
        async def log_usage():
            try:
                # Log usage summary
                summary = usage_collector.get_summary()
                logger.info(f"Usage summary: {summary}")
                
                # Send conversation transcript to Make.com
                logger.info("Sending conversation transcript to Make.com webhook")
                transcript_sent = conversation_tracker.send_to_webhook()
                logger.info(f"Transcript sent: {transcript_sent}")
                
            except Exception as e:
                logger.error(f"Failed to get usage summary or send transcript: {e}")

        ctx.add_shutdown_callback(log_usage)

        # Handle room disconnection
        @ctx.room.on("disconnected")
        def on_room_disconnected():
            logger.info("Room disconnected")
            
            # Send the transcript when the room disconnects
            try:
                logger.info("Room disconnected, sending conversation transcript to Make.com")
                # Run in a task to avoid blocking
                asyncio.create_task(send_transcript_on_disconnect())
            except Exception as e:
                logger.error(f"Error sending transcript on disconnect: {e}")

        # Function to send transcript on disconnect
        async def send_transcript_on_disconnect():
            try:
                # Short delay to ensure all messages are processed
                await asyncio.sleep(1)
                # Send the transcript
                transcript_sent = conversation_tracker.send_to_webhook()
                logger.info(f"Transcript sent on disconnect: {transcript_sent}")
            except Exception as e:
                logger.error(f"Failed to send transcript on disconnect: {e}")

        # Agent speech event handlers - essential for transcript tracking
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
                content = msg.content
                logger.info(f"Agent speech committed: {content}")
                
                # Add to transcript tracker
                if content:
                    conversation_tracker.add_assistant_message(content)

        # DTMF handler with Quitanda-specific responses
        @assistant.on("dtmf_received")
        def on_dtmf_received(digits):
            logger.info(f"DTMF digits received: {digits}")
            if digits == "1":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Selecionou a opção de menu. Aqui está o nosso cardápio: " + MENU[:150] + "...",
                        context="menu_request"
                    )
                )
            elif digits == "2":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Selecionou a opção de bebidas. Temos refrigerantes em garrafa de 1L e 1.5L, e uma seleção de vinhos portugueses. Gostaria de conhecer as nossas opções?",
                        context="drinks"
                    )
                )
            elif digits == "3":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Selecionou a opção de sobremesas. " + DESSERT_MENU,
                        context="dessert"
                    )
                )
            elif digits == "4":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Selecionou a opção de vinhos. Temos uma excelente carta de vinhos portugueses. Gostaria de conhecer os nossos tintos, brancos ou vinhos verdes?",
                        context="wine"
                    )
                )
            elif digits == "0":
                asyncio.create_task(
                    adaptive_say(
                        assistant,
                        "Muito obrigado pela sua chamada para a Churrascaria Quitanda. Até à próxima!",
                        context="closing"
                    )
                )

        # Start the assistant
        logger.info("Starting voice assistant...")
        assistant.start(ctx.room, participant)

        # Initial greeting optimized for immediate response
        await adaptive_say(
            assistant,
            "Churrascaria Quitanda, em que posso ajudar?",
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