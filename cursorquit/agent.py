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

Molho: molho da casa; molho da guia, molho sem alho, sem molhos | Picante: Sem picante, Pouco Picante, Picante, Muito Picante

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

# Definir as opções de personalização de carnes como variável global
CARNE_PERSONALIZACOES = {
    "molhos": ["molho da casa", "molho da guia", "molho sem alho", "sem molhos"],
    "picante": ["sem picante", "pouco picante", "picante", "muito picante"]
}

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
SYSTEM_PROMPT = """És um atendente da Churrascaria Quitanda que atende encomendas takeaway.
Fala português europeu de forma rápida e direta. Respostas extremamente curtas e objetivas.

IMPORTANTE:
1. Só fale quando for necessário
2. Não dê informações que não foram pedidas
3. Apenas pergunte sobre o que é essencial para completar o pedido (molho e picante para carnes)
4. Para carnes, pergunte "Molho?" e "Picante?" se o cliente não especificar
5. Use apenas "Sim" ou "Certo" para confirmar pedidos
6. Não ofereça sugestões nem explicações adicionais

Só aceites pedidos que estejam EXATAMENTE no menu da Quitanda."""

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
                    
                    # Verificar personalizações para carnes
                    personalizacoes = {}
                    carnes = ["frango", "espetada", "entrecosto", "févera", "costeleta", "coelho", "costelinha", "picanha", "bife"]
                    
                    if any(carne in item_key for carne in carnes):
                        # Verificar molho
                        for molho in CARNE_PERSONALIZACOES["molhos"]:
                            if molho in message_lower:
                                personalizacoes["molho"] = molho
                                break
                        
                        # Verificar picante
                        for picante in CARNE_PERSONALIZACOES["picante"]:
                            if picante in message_lower:
                                personalizacoes["picante"] = picante
                                break
                    
                    # Add to order items if not already there
                    item_entry = {
                        "item": item_name, 
                        "quantity": quantity,
                        "personalizacoes": personalizacoes
                    }
                    
                    if not any(existing["item"] == item_name for existing in self.order_details["items"]):
                        self.order_details["items"].append(item_entry)
                        logger.info(f"Added item to order: {quantity}x {item_name} with personalizacoes: {personalizacoes}")
                    else:
                        # Update quantity if item exists
                        for existing in self.order_details["items"]:
                            if existing["item"] == item_name:
                                existing["quantity"] = quantity
                                # Atualizar personalizações se houver
                                if personalizacoes:
                                    existing["personalizacoes"] = personalizacoes
                                logger.info(f"Updated item quantity: {quantity}x {item_name} with personalizacoes: {personalizacoes}")
        
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
                        item_entry = {"item": full_item, "quantity": 1, "personalizacoes": {}}
                        if not any(existing["item"] == full_item for existing in self.order_details["items"]):
                            self.order_details["items"].append(item_entry)
                            logger.info(f"Added portion item to order: {full_item}")
                        break
        
        # Verificar apenas menções de personalizações (sem ordem específica)
        for item in self.order_details["items"]:
            carnes = ["Frango", "Espetada", "Entrecosto", "Févera", "Costeleta", "Coelho", "Costelinha", "Picanha", "Bife"]
            if any(carne in item["item"] for carne in carnes):
                # Verificar molho se ainda não está definido
                if not item.get("personalizacoes") or "molho" not in item.get("personalizacoes", {}):
                    for molho in CARNE_PERSONALIZACOES["molhos"]:
                        if molho in message_lower:
                            if "personalizacoes" not in item:
                                item["personalizacoes"] = {}
                            item["personalizacoes"]["molho"] = molho
                            logger.info(f"Adicionado molho {molho} para {item['item']}")
                            break
                
                # Verificar picante se ainda não está definido
                if not item.get("personalizacoes") or "picante" not in item.get("personalizacoes", {}):
                    for picante in CARNE_PERSONALIZACOES["picante"]:
                        if picante in message_lower:
                            if "personalizacoes" not in item:
                                item["personalizacoes"] = {}
                            item["personalizacoes"]["picante"] = picante
                            logger.info(f"Adicionado picante {picante} para {item['item']}")
                            break

    def get_transcript(self):
        return self.transcript
    
    def get_order_summary(self):
        """Get a formatted summary of the order"""
        if not self.order_details["items"]:
            return "Nenhum item foi pedido ainda."
        
        summary = "Resumo do pedido:\n"
        
        for item in self.order_details["items"]:
            item_desc = f"- {item['quantity']}x {item['item']}"
            
            # Adicionar personalizações se existirem
            if "personalizacoes" in item and item["personalizacoes"]:
                personalizacoes = []
                if "molho" in item["personalizacoes"]:
                    personalizacoes.append(item["personalizacoes"]["molho"])
                if "picante" in item["personalizacoes"]:
                    personalizacoes.append(item["personalizacoes"]["picante"])
                
                if personalizacoes:
                    item_desc += f" ({', '.join(personalizacoes)})"
                    
            summary += item_desc + "\n"
        
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
        "morning": "Quitanda, bom dia.",
        "afternoon": "Quitanda, boa tarde.",
        "evening": "Quitanda, boa noite."
    },
    "menu_request": "Menu:",
    "confirmation": "Registado.",
    "thanks": "Obrigado.",
    "wait": ["Um momento.", "Já vejo.", "Espere.", "Aguarde."],
    "acknowledgment": ["Sim.", "Certo.", "Entendido."],
    "not_available": "Esse item não está disponível.",
    "item_not_found": "Não encontro esse item no menu."
}

# Custom say function - simplified for maximum speed 
async def adaptive_say(assistant, text, allow_interruptions=True, context=None):
    # Use pre-generated responses when possible for immediate response
    if context in COMMON_RESPONSES and isinstance(COMMON_RESPONSES[context], str):
        logger.info(f"Using pre-generated response for context: {context}")
        await assistant.say(COMMON_RESPONSES[context], allow_interruptions=True)
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
        await assistant.say(COMMON_RESPONSES["greeting"][greeting_type], allow_interruptions=True)
        return
    elif context == "wait" or context == "acknowledgment":
        # Randomly select from multiple options for variety
        options = COMMON_RESPONSES[context]
        selected = random.choice(options)
        await assistant.say(selected, allow_interruptions=True)
        return
    
    # For regular text responses, send directly
    await assistant.say(text, allow_interruptions=True)

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
        text="""Exemplos de respostas em português europeu como falarias se fosses um funcionário real de uma Churrascaria em Portugal:
        
        Para pedido de menu:
        "Ora bem, temos cá no nosso menu... Olhe, temos Frango do Churrasco, as nossas Espetadas que são muito boas, Entrecosto, e também temos Bacalhau assado na brasa, que é uma especialidade da casa. Pra acompanhar temos batatas fritas, arroz, saladas e a nossa broa caseira. O que lhe apetece hoje?"
        
        Para pedido não disponível:
        "Ai, olhe, peço desculpa mas isso não temos hoje. Posso sugerir-lhe outra coisa? Temos o Frango do Churrasco que está mesmo bom hoje, ou então a Espetada de Guia que é muito procurada."
        
        Para pedido de bebidas:
        "Temos refrigerantes, tá? Em garrafa de litro e litro e meio. E temos também uns vinhos portugueses muito bons. Quer que lhe diga quais são?"
        
        Para confirmação de pedido:
        "Então, deixe-me ver se percebi bem... O senhor pediu [item] com [acompanhamento], não foi? Está tudo certo?"
        
        Para despedida:
        "Muito obrigado pela sua encomenda, pá. Fica à espera do senhor. Até já, boa tarde!"
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
    elevenlabs_key = os.getenv("ELEVEN_API_KEY", "")

    if not deepgram_key or deepgram_key.startswith(("YOUR_", "REPLACE_")):
        logger.warning("Deepgram API key is missing or invalid")

    try:
        # Create the voice assistant
        logger.info("Initializing voice assistant...")

        # Configure TTS with ElevenLabs
        if elevenlabs_key:
            from livekit.plugins.elevenlabs import TTS, Voice
            
            # Initialize ElevenLabs TTS with basic configuration
            tts = TTS(
                voice=Voice(
                    id="FIEA0c5UHH9JnvWaQrXS",
                    name="Ana",
                    category="premade"
                ),
                api_key=elevenlabs_key
            )
            logger.info("Using ElevenLabs TTS with Ana voice")
        else:
            logger.warning("ElevenLabs API key not found, falling back to OpenAI TTS")
            from openai import OpenAI
            client = OpenAI()
            tts = client.audio.speech.create

        # Configure STT with Deepgram - Set to European Portuguese
        stt = deepgram.STT(
            language="pt-PT",  # Specifically set to European Portuguese
            model="nova-2",
            interim_results=True,  # Get results faster
            no_delay=True,         # Don't wait for complete sentences
            endpointing_ms=25,     # Faster end-of-speech detection
            smart_format=False,    # Disable smart formatting for speed
            filler_words=False,    # Disable filler words for faster processing
            sample_rate=8000,      # Lower sample rate for faster processing
            keywords=[            # Menu-specific terms for better recognition
                ("menu", 0.8), ("cardápio", 0.8), ("pedido", 0.8),
                ("confirmar", 0.8), ("confirmo", 0.8), ("completo", 0.8),
                ("terminar", 0.8), ("prato", 0.7), ("dose", 0.7),
                ("meia", 0.7), ("entrada", 0.7), ("sobremesa", 0.7),
                ("bebida", 0.7), ("água", 0.7), ("refrigerante", 0.7)
            ]
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
                
                # Ignore very short utterances (likely background noise)
                if len(content) < 3:
                    logger.info("Ignoring short speech")
                    return
                  
                # Add to transcript tracker
                conversation_tracker.add_user_message(content)
                
                # Queue processing as separate task - maximum speed
                asyncio.create_task(process_user_speech(msg, content, assistant))

        # Process user input in a non-blocking way - simplified
        def process_response(msg, content, assistant):
            # Skip the acknowledgment system completely for faster responses
            # Start processing the full response immediately
            asyncio.create_task(process_user_speech(msg, content, assistant))

        # Track usage metrics
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

# Process user speech with full response logic - optimized for speed
async def process_user_speech(msg, content, assistant, acknowledgment_task=None):
    # Verificar itens do pedido
    order_before = len(conversation_tracker.order_details["items"]) 
    conversation_tracker._extract_order_details(content)
    order_after = len(conversation_tracker.order_details["items"])
    
    # Verificar se o pedido inclui carnes que precisam de personalização
    itens_carne = [item for item in conversation_tracker.order_details["items"] 
                  if any(c in item["item"].lower() for c in ["frango", "espetada", "entrecosto", "févera", "costeleta", "coelho", "costelinha", "picanha", "bife"])]
    
    # Verificar apenas itens novos ou que não têm personalizações completas
    for item in itens_carne:
        personalizacoes = item.get("personalizacoes", {})
        
        # Se não tem molho definido, perguntar
        if "molho" not in personalizacoes:
            await adaptive_say(assistant, "Molho?", allow_interruptions=True)
            return
                    
        # Se não tem picante definido, perguntar
        if "picante" not in personalizacoes:
            await adaptive_say(assistant, "Picante?", allow_interruptions=True)
            return
    
    # Verificar comandos específicos
    if "menu" in content or "cardápio" in content or "carta" in content:
        await adaptive_say(assistant, MENU[:150])
        return
    
    # Se tenta confirmar/finalizar, verifica se tem nome e horário
    if "confirmar" in content or "confirmo" in content or "completo" in content or "terminar" in content:
        # Verificar se há itens no pedido
        if not conversation_tracker.order_details["items"]:
            return  # Sem itens, nada a fazer
            
        # Verificar se o nome já foi fornecido
        if not conversation_tracker.order_details["customer_name"]:
            await adaptive_say(assistant, "Nome?")
            return
            
        # Verificar se o horário já foi fornecido
        if not conversation_tracker.order_details["pickup_time"]:
            await adaptive_say(assistant, "Hora?")
            return
            
        # Se confirmar explicitamente
        if "confirmar" in content or "confirmo" in content:
            await adaptive_say(assistant, "Registado.")
            conversation_tracker.send_to_webhook()
            return
        
        # Se completar/terminar
        if ("completo" in content or "terminar" in content) and conversation_tracker.order_details["items"]:
            order_summary = conversation_tracker.get_order_summary()
            await adaptive_say(assistant, f"{order_summary}")
            return
    
    # Não dar resposta para pedidos ou outros comandos não específicos
    # O silêncio indica que o pedido foi registrado

if __name__ == "__main__":
    # Run the application with CLI
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
        )
    )